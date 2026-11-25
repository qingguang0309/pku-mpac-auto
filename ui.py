"""mpac 预约的本地界面：http://127.0.0.1:8765 （只监听本机）。

  uv run python ui.py          # 启动并打开浏览器
双击 mpac预约.app 也是做同样的事。
"""
import datetime as dt
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import book
import scheduler
import vpn
from common import (KC_ACCOUNT, KC_ID6, KC_PHONE, LISTENERS, ROOT, Stop, kc_get, kc_set, load_config, log, notify,
                    save_config, set_skip, skip_dates)

PORT = 8765
LAST = ROOT / "logs" / "last_auto.json"
TOKEN = secrets.token_hex(16)  # 页面里带着它才能调用接口，防止别的网页偷偷发请求

job = {"id": 0, "kind": None, "running": False, "logs": [], "ok": None, "result": None}
job_lock = threading.Lock()


def auto_running() -> bool:
    """每周自动预约正在跑（book.AUTO_LOCK 里记着它的进程号，且进程还活着）。"""
    try:
        os.kill(int(book.AUTO_LOCK.read_text()), 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def start_job(kind: str, fn) -> bool:
    if auto_running():
        # 网站同一账号只能登录一处，这时候登录会把自动预约挤掉
        raise RuntimeError("每周自动预约正在进行，请等它结束再操作（同一账号同时登录会互相挤掉）")
    with job_lock:
        if job["running"]:
            return False
        job.update(id=job["id"] + 1, kind=kind, running=True, logs=[], ok=None, result=None)

    def worker():
        listener = lambda line: job["logs"].append(line[11:])  # 去掉日期，只留时间
        LISTENERS.append(listener)
        try:
            job["result"] = fn()
            job["ok"] = True
        except Stop as e:
            log(f"❌ {e}")
            job["ok"], job["result"] = False, str(e)
        except Exception as e:
            log(f"❌ 出错：{e!r}")
            job["ok"], job["result"] = False, f"{type(e).__name__}: {e}"
        finally:
            LISTENERS.remove(listener)
            job["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return True


# ---------- 各项任务 ----------

def task_network():
    v = vpn.ensure()
    try:
        return "校园网直连，可以访问 mpac" if not v.proxy else "北大 VPN 连接成功，可以访问 mpac"
    finally:
        v.stop()


def task_check(date: str):
    with book.Session() as s:
        if date not in s.dates():
            raise Stop(f"{date} 还没有开放预约")
        grid = s.grid(date)
        return {"date": date, "me": s.me, "cells": [{"t": k, "who": v} for k, v in grid.items()]}


def task_book(date: str, slots: list[str], dry_run: bool):
    for s in slots:
        book.half_hours(s)
    log(f"===== 界面预约：{date}，候选时段 {'、'.join(slots)}{'（试运行，不提交）' if dry_run else ''}")
    with book.Session() as s:
        if date not in s.dates():
            raise Stop(f"{date} 还没有开放预约")
        got = s.book(date, slots, dry_run)
    msg = f"{date[4:6]}-{date[6:]} {got} " + ("可以约（试运行，未提交）" if dry_run else "预约成功")
    if not dry_run:
        notify("mpac 预约成功", msg)
    return msg


def task_otp_login(code: str):
    """用手机令牌登录一次，并勾选「同设备登录免二次验证」，之后自动预约不用再输令牌。"""
    with book.Session(otp=code) as s:
        return f"已用手机令牌登录（{s.me}），并记住了这台电脑。之后自动预约不用再输入令牌"


def task_auto_test():
    try:
        return book.auto(dry_run=True)
    except book.Skipped as e:  # 这次要约的都取消了，不算失败
        log(f"⏭ {e}")
        return str(e)


# ---------- HTTP ----------

def state() -> dict:
    acct = kc_get(KC_ACCOUNT)
    cfg = load_config()
    return {
        "account": acct[0] if acct and acct[0].isascii() else None,
        "id6_saved": kc_get(KC_ID6) is not None,
        "phone_saved": kc_get(KC_PHONE) is not None,
        "config": {**{k: cfg.get(k) for k in ("open_at", "prepare_minutes", "poll_before_minutes", "wait_max_minutes", "days_ahead", "rules")},
                   "skip_dates": skip_dates(cfg)},
        "auto_running": auto_running(),
        "need_otp": book.NEED_OTP.exists(),
        "schedule": scheduler.describe(),
        "pmset": scheduler.pmset_command(),
        "schedule_installed": scheduler.PLIST.exists(),
        "last_auto": json.loads(LAST.read_text()) if LAST.exists() else None,
        "today": dt.date.today().strftime("%Y%m%d"),
        "job": job,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code: int, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self) -> bool:
        return self.headers.get("X-Token") == TOKEN and self.headers.get("Host", "").startswith(("127.0.0.1", "localhost"))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = (ROOT / "ui" / "index.html").read_text().replace("__TOKEN__", TOKEN)
            return self.send(200, html.encode(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            if not self.authorized():
                return self.send(403, {"error": "forbidden"})
            return self.send(200, state())
        if self.path in ("/favicon.png", "/favicon.ico"):
            return self.send(200, (ROOT / "ui" / "favicon.png").read_bytes(), "image/png")
        if self.path == "/api/ping":
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.authorized():
            return self.send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if self.path == "/api/credentials":
                user = body.get("user", "").strip()
                if body.get("password"):
                    if not (user.isascii() and user.isalnum()):
                        raise ValueError("学号只能是数字或字母")
                    kc_set(KC_ACCOUNT, user, body["password"])
                if body.get("id6"):
                    code = body["id6"].strip().upper()
                    if len(code) != 6 or not code[:5].isdigit() or not (code[5].isdigit() or code[5] == "X"):
                        raise ValueError("身份证后 6 位格式不对")
                    kc_set(KC_ID6, "id6", code)
                if body.get("phone"):
                    tel = body["phone"].strip().replace(" ", "").replace("-", "")
                    if len(tel) != 11 or not tel.isdigit():
                        raise ValueError("手机号应为 11 位数字")
                    kc_set(KC_PHONE, "phone", tel)
                return self.send(200, {"ok": True})
            if self.path == "/api/network":
                return self.send(200, {"started": start_job("network", task_network)})
            if self.path == "/api/check":
                return self.send(200, {"started": start_job("check", lambda: task_check(body["date"]))})
            if self.path == "/api/book":
                return self.send(200, {"started": start_job(
                    "book", lambda: task_book(body["date"], body["slots"], bool(body.get("dry_run"))))})
            if self.path == "/api/schedule":
                cfg = load_config()
                rules = []
                for r in body["rules"]:
                    book.half_hours(r["slot"])
                    if not 1 <= int(r["weekday"]) <= 7:
                        raise ValueError("星期不对")
                    rules.append({"weekday": int(r["weekday"]), "slot": r["slot"], **({"partial": True} if r.get("partial") else {})})
                if not rules:
                    raise ValueError("至少填一条规则")
                h, m = (int(x) for x in body["open_at"].split(":"))
                prep = int(body["prepare_minutes"])
                if not 1 <= prep <= 30:
                    raise ValueError("提前准备时间请填 1–30 分钟")
                cfg.update(open_at=f"{h:02d}:{m:02d}", prepare_minutes=prep, rules=rules)
                save_config(cfg)
                scheduler.install() if body.get("enabled") else scheduler.uninstall()
                return self.send(200, {"ok": True})
            if self.path == "/api/otp_login":
                code = str(body.get("code", "")).strip()
                if not re.fullmatch(r"\d{6}", code):
                    raise ValueError("手机令牌是 6 位数字")
                return self.send(200, {"started": start_job("otp", lambda: task_otp_login(code))})
            if self.path == "/api/skip":
                # 取消 / 恢复某一天的自动预约（只改 skip_dates，规则和定时任务不变）
                if auto_running():
                    raise RuntimeError("每周自动预约正在进行，请等它结束再改")
                return self.send(200, {"ok": True, "skip_dates": set_skip(str(body["date"]), bool(body["skip"]))})
            if self.path == "/api/auto_test":
                return self.send(200, {"started": start_job("auto", task_auto_test)})
            if self.path == "/api/quit":
                self.send(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.send(404, {"error": "not found"})
        except (ValueError, KeyError, RuntimeError) as e:
            self.send(400, {"error": str(e)})


def main():
    url = f"http://127.0.0.1:{PORT}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # 已经在运行
        webbrowser.open(url)
        return
    print(f"界面已启动：{url}", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, lambda: subprocess.run(["open", url])).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
