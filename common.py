"""公共部分：路径、日志、通知、钥匙串读写、配置读写。"""
import datetime as dt
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
CONFIG = ROOT / "config.toml"

# 钥匙串条目：学号+密码（IAAA 和 VPN 共用）；身份证后 6 位（VPN 的证书验证）
KC_ACCOUNT = "pku-mpac-auto"
KC_ID6 = "pku-mpac-auto-id6"
KC_PHONE = "pku-mpac-auto-phone"  # VPN 有时要求补全手机号中间 4 位

# 日志监听者（UI 用来实时显示进度）
LISTENERS: list = []


class Stop(Exception):
    """需要人来处理、重试也没用的情况（登录失效、需要验证码、时段全被占等）。"""


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "book.log", "a") as f:
        f.write(line + "\n")
    for fn in list(LISTENERS):
        fn(line)


def notify(title: str, msg: str) -> None:
    script = f"display notification {json.dumps(msg, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)} sound name \"Glass\""
    subprocess.run(["osascript", "-e", script], check=False)


def clean_env() -> dict:
    """去掉终端里的代理环境变量：北大的站点不能走本机代理。"""
    return {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}


# ---------- 钥匙串 ----------

def kc_get(service: str) -> tuple[str, str] | None:
    meta = subprocess.run(["security", "find-generic-password", "-s", service], capture_output=True, text=True)
    pwd = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
    m = re.search(r'"acct"<blob>=(?:"([^"]*)"|0x([0-9A-Fa-f]+))', meta.stdout)
    if meta.returncode != 0 or pwd.returncode != 0 or not m:
        return None
    acct = m.group(1) if m.group(1) is not None else bytes.fromhex(m.group(2)).decode("utf-8", "replace")
    return acct, pwd.stdout.rstrip("\n")


def kc_set(service: str, account: str, secret: str) -> None:
    """先删掉旧条目再写入。通过 security -i 的标准输入传密码，不出现在进程列表里。"""
    while subprocess.run(["security", "delete-generic-password", "-s", service], capture_output=True).returncode == 0:
        pass
    q = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    r = subprocess.run(["security", "-i"], input=f"add-generic-password -s {q(service)} -a {q(account)} -w {q(secret)}\n",
                       capture_output=True, text=True)
    if kc_get(service) is None:
        raise RuntimeError(f"写入钥匙串失败：{r.stderr.strip()[:200]}")


def credentials() -> tuple[str, str]:
    got = kc_get(KC_ACCOUNT)
    if not got:
        raise Stop("还没有保存学号和密码，请在界面的「账号与 VPN」里填写")
    user, pwd = got
    if not user.isascii():
        raise Stop(f"钥匙串里的账号是「{user}」，不像学号，请在界面里重新保存")
    return user, pwd


def id6() -> str | None:
    got = kc_get(KC_ID6)
    return got[1] if got else None


def phone() -> str | None:
    got = kc_get(KC_PHONE)
    return got[1] if got else None


# ---------- 配置 ----------

def load_config() -> dict:
    return tomllib.loads(CONFIG.read_text())


WEEKDAYS = "一二三四五六日"  # 下标 0 = 周一（weekday=1）


def run_schedule(cfg: dict) -> tuple[int, int, int]:
    """自动运行的时间 (weekday 1-7, 时, 分)：第一条规则的那天开放的日子，open_at 之前 prepare_minutes 分钟。"""
    wd = (cfg["rules"][0]["weekday"] - 1 - cfg["days_ahead"]) % 7 + 1
    oh, om = (int(x) for x in cfg["open_at"].split(":"))
    t = oh * 60 + om - int(cfg["prepare_minutes"])
    if t < 0:  # 跨到前一天
        t += 24 * 60
        wd = (wd - 2) % 7 + 1
    return wd, t // 60, t % 60


def save_config(cfg: dict) -> None:
    rules = "\n".join(f'  {{ weekday = {int(r["weekday"])}, slot = "{r["slot"]}" }},' for r in cfg["rules"])
    CONFIG.write_text(f'''# mpac 自动预约配置（界面里「每周自动预约」保存时会重写本文件）。
# 手动改了这里之后，需要再运行一次：uv run python scheduler.py install

# 网站每天几点放出新的一天（可预约「今天 + days_ahead」那天）
open_at = "{cfg["open_at"]}"
# 提前几分钟开始准备（连 VPN、登录、打开预约页）
prepare_minutes = {int(cfg["prepare_minutes"])}
# 开放前几分钟就开始每隔几秒刷新（开放时间不确定时留余量），新的一天一出现立刻提交
poll_before_minutes = {int(cfg.get("poll_before_minutes", 2))}
days_ahead = {int(cfg["days_ahead"])}

# 仪器编号：扫描电子显微镜 = 203（上机预约页面网址里的 pid）
instrument_pid = {int(cfg["instrument_pid"])}

# 每周想约的时段，按优先级排列。weekday：1=周一 … 7=周日。
# 程序只在第一条开放的那天运行，依次尝试同一周里的这几天，约到一个就停。
rules = [
{rules}
]

# 刷新间隔（秒）；开放时间之后最多再等几分钟
wait_retry_seconds = {int(cfg["wait_retry_seconds"])}
wait_max_minutes = {int(cfg["wait_max_minutes"])}
''')
