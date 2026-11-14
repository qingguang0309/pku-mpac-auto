"""mpac 上机预约：联网（校园网直连或北大 VPN）→ IAAA 登录 → 上机预约 → 选日期/时段 → 提交预约。

用法：
  uv run python book.py              # 每周自动预约（定时任务跑的就是这个）：按 config.toml 的 rules 依次尝试
  uv run python book.py --dry-run    # 同上，但不点「提交预约」
  uv run python book.py --date 20261120 --slot 14:00-17:00 --slot 09:00-12:00   # 指定日期和时段（按顺序尝试）
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse

from playwright.sync_api import Page, sync_playwright

import vpn
from common import LOG_DIR, WEEKDAYS, Stop, credentials, load_config, log, notify

BASE = "https://mpac.pku.edu.cn"
AUTO_LOCK = LOG_DIR / "auto.lock"
IAAA_URL = (
    "https://iaaa.pku.edu.cn/iaaa/oauth.jsp?appID=mpac"
    "&appName=%e5%8c%97%e4%ba%ac%e5%a4%a7%e5%ad%a6%e6%9d%90%e6%96%99%e5%8a%a0%e5%b7%a5%e4%b8%8e%e6%b5%8b%e8%af%95%e4%b8%ad%e5%bf%83"
    "&redirectUrl=https://mpac.pku.edu.cn/test.aspx"
)


# ---------- 时段换算 ----------

def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def half_hours(slot: str) -> list[str]:
    """'09:00-10:00' → ['09:00-09:30', '09:30-10:00']，和网页表格里每个格子对应。"""
    start, end = (to_min(x) for x in slot.split("-"))
    if start >= end or start % 30 or end % 30 or end > 24 * 60:
        raise ValueError(f"时段格式不对：{slot}（要求 HH:MM-HH:MM，半小时为单位）")
    return [f"{to_hhmm(t)}-{to_hhmm(t + 30)}" for t in range(start, end, 30)]


# ---------- 页面操作 ----------

def booking_url(pid: int) -> str:
    # 必须带「当前的日期」：不带的话网站给的预约表格可能还停在前一天，看不到最新开放的那天
    now = dt.datetime.now()
    return f"{BASE}/Front.aspx?page=sbsjyy&pid={pid}&" + urllib.parse.urlencode({"当前的日期": f"{now:%m %d %Y %I:%M%p}"})


def logged_in_name(page: Page) -> str | None:
    m = re.search(r"(\S+?)\s*[，,]\s*您好", page.inner_text("body"))
    return m.group(1) if m else None


def login(page: Page) -> None:
    user, pwd = credentials()
    log(f"登录 IAAA：{user}")
    page.goto(IAAA_URL, wait_until="networkidle")
    page.fill("#user_name", user)
    page.fill("#password", pwd)
    page.click("#logon_button")
    try:
        page.wait_for_url(re.compile(r"^https://mpac\.pku\.edu\.cn/"), timeout=20_000)
        page.wait_for_load_state("networkidle")
        return
    except Exception:
        pass
    for area, what in (("#sms_area", "短信验证码"), ("#otp_area", "手机令牌"), ("#code_area", "图片验证码")):
        if page.locator(area).is_visible():
            raise Stop(f"IAAA 要求输入{what}，无法自动登录，请手动登录一次后再试")
    msg = page.locator("#msg").inner_text().strip() if page.locator("#msg").count() else ""
    raise Stop(f"IAAA 登录失败：{msg or '未跳转回 mpac'}")


def grid_state(page: Page, date: str) -> dict[str, str]:
    """某天每个半小时格子的状态：'free'（可约）或格子里的文字（预约人姓名；空字符串表示不可约）。"""
    return page.evaluate(
        """(date) => {
          const rows = [...document.querySelectorAll('#pfrom tr')];
          const head = rows[1] ? [...rows[1].cells].map(c => c.innerText.trim()) : [];
          const col = head.indexOf(date.slice(4,6) + '-' + date.slice(6,8));
          const out = {};
          if (col < 0) return out;
          for (const r of rows.slice(2)) {
            const label = r.cells[0] && r.cells[0].innerText.trim();
            const cell = r.cells[col + 1];
            if (!label || !cell) continue;
            out[label] = cell.querySelector('input[name=chkitem]') ? 'free' : cell.innerText.trim();
          }
          return out;
        }""",
        date,
    )


def submit(page: Page, date: str, slot: str, dry_run: bool) -> str:
    """选好日期和时段，点「提交预约」，返回网站给出的提示文字。"""
    start, end = slot.split("-")
    form = page.locator("form.apform").filter(has=page.locator("select[name='预约日期']"))
    form.locator("select[name='预约日期']").select_option(date)
    for name, want in (("起始时段", start), ("截止时段", end)):
        sel = form.locator(f"select[name='{name}']")
        value = sel.evaluate("(s, w) => { const o = [...s.options].find(o => o.text.trim() === w); return o ? o.value : null }", want)
        if value is None:
            raise ValueError(f"{name}下拉框里没有 {want}")
        sel.select_option(value)
    if dry_run:
        return "dry-run：已选好，未提交"

    messages: list[str] = []
    page.on("dialog", lambda d: (messages.append(d.message), d.accept()))
    with page.expect_response(lambda r: "Post.ashx" in r.url, timeout=20_000) as resp_info:
        form.locator("input[name='btn_提交预约']").click()
    try:
        data = resp_info.value.json()
        messages.insert(0, f"Type={data.get('Type')} Message={data.get('Message')}")
    except Exception:
        # 提交被接受时网站会立刻跳转页面，响应内容随之读不到；成功与否以重新读表格为准
        messages.insert(0, f"HTTP {resp_info.value.status}，页面已跳转")
    page.wait_for_timeout(1500)
    return " | ".join(m for m in messages if m)


class Session:
    """with Session() as s: 联网 → 开 Chrome → 登录 → 打开上机预约页。退出时关浏览器、断开自己开的 VPN。"""

    def __init__(self, pid: int | None = None):
        self.pid = pid or load_config()["instrument_pid"]

    def __enter__(self) -> "Session":
        if getattr(self, "me", None):  # 已经打开过（自动预约先打开、后用 with 收尾）
            return self
        self.vpn = vpn.ensure()
        self._pw = sync_playwright().start()
        args = [f"--proxy-server={self.vpn.proxy}"] if self.vpn.proxy else ["--no-proxy-server"]
        self.browser = self._pw.chromium.launch(channel="chrome", headless=True, args=args)
        self.page = self.browser.new_context(locale="zh-CN").new_page()
        try:
            self.page.goto(booking_url(self.pid), wait_until="networkidle")
            if not logged_in_name(self.page):
                login(self.page)
                self.reload()
            self.me = logged_in_name(self.page)
            if not self.me:
                raise Stop("登录后仍然不是登录状态，请手动检查网站")
            if self.page.locator("select[name='预约日期']").count() == 0:
                raise Stop(f"上机预约页面没有快速预约表单（pid={self.pid} 是否有预约权限？）")
            log(f"已登录：{self.me}")
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            try:
                shot = LOG_DIR / f"fail_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
                self.page.screenshot(path=str(shot), full_page=True)
                log(f"失败截图：{shot.name}")
            except Exception:
                pass
        for close in (lambda: self.browser.close(), lambda: self._pw.stop(), lambda: self.vpn.stop()):
            try:
                close()
            except Exception:
                pass

    def reload(self) -> None:
        self.page.goto(booking_url(self.pid), wait_until="networkidle")
        if not logged_in_name(self.page):
            # 网站同一账号只保留一个登录：别处（比如界面里）登录后，这里会被挤掉，重新登录即可
            log("登录状态丢失（可能别处登录了同一账号），重新登录")
            login(self.page)
            self.page.goto(booking_url(self.pid), wait_until="networkidle")

    def dates(self) -> list[str]:
        return self.page.locator("form.apform select[name='预约日期'] option").evaluate_all("os => os.map(o => o.value)")

    def grid(self, date: str) -> dict[str, str]:
        return grid_state(self.page, date)

    def wait_for_date(self, date: str, retry_s: int, max_min: float) -> None:
        deadline = time.time() + max_min * 60
        while date not in (ds := self.dates()):
            newest = ds[-1] if ds else "页面上没有日期下拉框"
            if time.time() > deadline:
                raise Stop(f"{date} 一直没有开放预约（最新：{newest}）")
            log(f"{date} 还没开放（最新 {newest}），{retry_s} 秒后刷新")
            time.sleep(retry_s)
            self.reload()

    def book(self, date: str, slots: list[str], dry_run: bool = False) -> str:
        """按顺序尝试各个时段，返回约到的时段；全部失败抛 Stop。"""
        for slot in slots:
            cells = half_hours(slot)
            state = self.grid(date)
            if all(state.get(c) == self.me for c in cells):
                log(f"{date} {slot} 已经是你的预约，无需重复提交")
                return slot
            taken = {c: state.get(c, "?") for c in cells if state.get(c) != "free"}
            if taken:
                log(f"{slot} 不可约，跳过：" + "，".join(f"{c} {who or '不开放'}" for c, who in taken.items()))
                continue
            result = submit(self.page, date, slot, dry_run)
            log(f"提交 {slot}：{result}")
            if dry_run:
                return slot
            self.reload()
            state = self.grid(date)
            if all(state.get(c) == self.me for c in cells):
                log(f"✅ 预约成功：{date} {slot}")
                return slot
            log(f"{slot} 提交后表格里不是你的名字，换下一个时段")
        raise Stop(f"{date} 所有候选时段都没约到：{'、'.join(slots)}")


def run(date: str, slots: list[str], dry_run: bool = False) -> str:
    """指定日期，按顺序尝试几个时段。"""
    cfg = load_config()
    for x in slots:
        half_hours(x)  # 先检查格式
    log(f"===== 开始：日期 {date}，候选时段 {'、'.join(slots)}{'（dry-run，不提交）' if dry_run else ''}")
    with Session(cfg["instrument_pid"]) as s:
        s.wait_for_date(date, cfg["wait_retry_seconds"], cfg["wait_max_minutes"])
        got = s.book(date, slots, dry_run)
        return f"{'可以约（试运行，未提交）' if dry_run else '已约到'} {pretty(date, got)}"


def pretty(date: str, slot: str) -> str:
    """'20261127', '14:00-17:30' → '11-27 周五 14:00-17:30'"""
    return f"{date[4:6]}-{date[6:]} 周{WEEKDAYS[dt.datetime.strptime(date, '%Y%m%d').weekday()]} {slot}"


def weekly_plan(cfg: dict, today: dt.date) -> list[tuple[str, str]]:
    """本次要尝试的 [(日期, 时段)]，按优先级。

    以第一条规则（如周五）最近开放的那天为准，其余规则取同一周里对应的那天；
    还没开放或已经过去的日子跳过。例：周六运行 → 周五(+13)、周四(+12)、周三(+11)。"""
    newest = today + dt.timedelta(days=cfg["days_ahead"])
    first = cfg["rules"][0]["weekday"]
    anchor = newest - dt.timedelta(days=(newest.isoweekday() - first) % 7)
    plan = []
    for r in cfg["rules"]:
        d = anchor + dt.timedelta(days=int(r["weekday"]) - first)
        if today <= d <= newest:
            plan.append((d.strftime("%Y%m%d"), r["slot"]))
    return plan


def open_session_with_retry(pid: int, give_up_at: dt.datetime) -> "Session":
    """准备阶段：联网 + 登录。失败就每 20 秒重试，直到 give_up_at。"""
    while True:
        try:
            return Session(pid).__enter__()
        except Exception as e:
            if dt.datetime.now() >= give_up_at:
                raise
            log(f"准备失败，20 秒后重试：{e}")
            time.sleep(20)


def auto(dry_run: bool = False) -> str:
    """每周自动预约：提前联网登录，到开放时间立即依次尝试各条规则。"""
    cfg = load_config()
    today = dt.date.today()
    plan = weekly_plan(cfg, today)
    if not plan:
        raise Stop("今天没有需要预约的日期（规则里的日子都不在可预约范围内）")
    for _, slot in plan:
        half_hours(slot)
    desc = "、".join(pretty(d, slot) for d, slot in plan)
    log(f"===== 每周自动预约：{desc}{'（dry-run，不提交）' if dry_run else ''}")
    if not dry_run:
        # 一启动就提醒：本次已开始、要约哪几个时段（按顺序，约到一个就停）
        order = " → ".join(pretty(d, slot) for d, slot in plan)
        until = dt.datetime.strptime(cfg["open_at"], "%H:%M") + dt.timedelta(minutes=cfg["wait_max_minutes"])
        notify("⏳ mpac 自动预约已开始",
               f"预计 {cfg['open_at']} 开放，依次尝试：{order}。运行期间（到 {until:%H:%M}）请勿登录 mpac")

    oh, om = (int(x) for x in cfg["open_at"].split(":"))
    open_time = dt.datetime.combine(today, dt.time(oh, om))
    poll_from = open_time - dt.timedelta(minutes=cfg.get("poll_before_minutes", 0))
    poll_until = open_time + dt.timedelta(minutes=cfg["wait_max_minutes"])
    LOG_DIR.mkdir(exist_ok=True)
    AUTO_LOCK.write_text(str(os.getpid()))  # 界面看到它就不去登录，免得把这里的登录挤掉
    try:
        return _auto_run(cfg, plan, open_time, poll_from, poll_until, dry_run)
    finally:
        AUTO_LOCK.unlink(missing_ok=True)


def _auto_run(cfg, plan, open_time, poll_from, poll_until, dry_run) -> str:
    s = open_session_with_retry(cfg["instrument_pid"], give_up_at=open_time + dt.timedelta(minutes=2))
    with s:  # 结束时关浏览器、断开 VPN（出错会截图）
        # 每周只要一次：这周的规则时段里已经有你的预约（比如上次跑过、或你手动约过），就不再约
        open_dates = s.dates()
        for date, slot in plan:
            if date in open_dates and all(s.grid(date).get(c) == s.me for c in half_hours(slot)):
                log(f"本周已有预约：{pretty(date, slot)}，不再预约")
                return f"本周已约到 {pretty(date, slot)}（之前约的，这次没有重复预约）"
        if (wait := (poll_from - dt.datetime.now()).total_seconds()) > 0:
            log(f"准备就绪，{poll_from:%H:%M} 开始刷新（{int(wait)} 秒后），网站预计 {cfg['open_at']} 开放")
            time.sleep(wait)
            s.reload()
        failures = []
        for date, slot in plan:
            try:
                s.wait_for_date(date, cfg["wait_retry_seconds"], max(0.0, (poll_until - dt.datetime.now()).total_seconds() / 60))
                s.book(date, [slot], dry_run)
                return f"{'可以约（试运行，未提交）' if dry_run else '已约到'} {pretty(date, slot)}"
            except Stop as e:
                log(f"{date} {slot} 没约到：{e}")
                failures.append(f"{date[4:6]}-{date[6:]} {slot}")
        raise Stop("本周几个时段都没约到：" + "、".join(failures) + "。可以打开界面手动约其它时间")


def save_last_auto(ok: bool, msg: str, dry_run: bool) -> None:
    """记下最近一次每周自动预约的结果，界面里会显示。"""
    LOG_DIR.mkdir(exist_ok=True)
    (LOG_DIR / "last_auto.json").write_text(json.dumps(
        {"time": f"{dt.datetime.now():%Y-%m-%d %H:%M}", "ok": ok, "message": msg, "dry_run": dry_run}, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="不点提交预约")
    ap.add_argument("--date", help="指定日期 YYYYMMDD（和 --slot 一起用）；不指定则按 config.toml 每周规则")
    ap.add_argument("--slot", action="append", help="指定时段（可重复，按优先级）")
    args = ap.parse_args()
    if bool(args.date) != bool(args.slot):
        ap.error("--date 和 --slot 要一起用")
    is_auto = not args.date
    try:
        msg = run(args.date, args.slot, args.dry_run) if args.date else auto(args.dry_run)
        notify("✅ mpac 上机预约成功" if not args.dry_run else "mpac 预约试运行", f"{msg}（扫描电子显微镜）")
        code = 0
    except Stop as e:
        log(f"❌ {e}")
        msg, code = str(e), 1
        notify("❌ mpac 上机预约没约到", msg)
    except Exception as e:
        log(f"❌ 出错：{e!r}")
        msg, code = f"程序出错：{type(e).__name__}: {e}"[:300], 2
        notify("❌ mpac 上机预约出错", msg)
    if is_auto:
        save_last_auto(code == 0, msg, args.dry_run)
    return code


if __name__ == "__main__":
    sys.exit(main())
