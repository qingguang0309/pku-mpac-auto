"""北大 VPN（vpn.pku.edu.cn，和 Ivanti Secure Access 客户端连的是同一个服务器）。

用开源客户端 openconnect 的 pulse 协议登录：学号 → 密码 → 身份证后 6 位（证书验证）。
连上后不改系统路由，只在本机开一个 SOCKS 代理（127.0.0.1:11080），
只有预约程序的 Chrome 用它；谷歌、本机代理等其它网络完全不受影响，也不需要管理员密码。

  uv run python vpn.py check     # 看现在需不需要 VPN、VPN 能不能连上
"""
import re
import socket
import subprocess
import sys
import time

import pexpect

from common import LOG_DIR, Stop, clean_env, credentials, id6, log, phone

OPENCONNECT = "/opt/homebrew/bin/openconnect"
OCPROXY = "/opt/homebrew/bin/ocproxy"
VPN_URL = "https://vpn.pku.edu.cn/"
SOCKS_PORT = 11080
PROXY = f"socks5://127.0.0.1:{SOCKS_PORT}"
PROBE_URL = "https://mpac.pku.edu.cn/"


def reachable(via_vpn: bool = False, timeout: int = 6) -> bool:
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout)]
    cmd += ["--socks5-hostname", f"127.0.0.1:{SOCKS_PORT}"] if via_vpn else ["--noproxy", "*"]
    r = subprocess.run(cmd + [PROBE_URL], capture_output=True, text=True, env=clean_env())
    return r.stdout.strip() not in ("", "000")


def port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", SOCKS_PORT)) == 0


class Vpn:
    """proxy 为 None 表示直连（在校园网里）。只有自己启动的 VPN 才会在 stop() 时断开。"""

    def __init__(self, proxy: str | None, proc: subprocess.Popen | None = None):
        self.proxy, self.proc = proxy, proc

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()  # openconnect 收到 SIGTERM 会注销会话再退出
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            log("VPN 已断开")


def fill_mask(mask: str, number: str | None) -> str | None:
    """'153****5085' + 完整手机号 → 被星号遮住的那几位；对不上就返回 None。"""
    if not number or len(mask) != len(number):
        return None
    if any(m != "*" and m != n for m, n in zip(mask, number)):
        return None
    return "".join(n for m, n in zip(mask, number) if m == "*")


def authenticate() -> dict:
    """完成认证：学号 → 密码 → 身份证后 6 位 →（有时）补全手机号。返回 openconnect 给出的会话信息。"""
    user, pwd = credentials()
    code, tel = id6(), phone()
    secrets = [s for s in (pwd, code, tel) if s]
    child = pexpect.spawn(OPENCONNECT, ["--protocol=pulse", "--authenticate", "-u", user, VPN_URL],
                          env=clean_env(), encoding="utf-8", timeout=45)
    asked = {"pwd": 0, "code": 0, "resp": 0}
    try:
        while True:
            # 证书验证的提示是两行：「Token code request:」+「Please enter your passcode:」，只认真正要输入的那一行
            i = child.expect([r"Password:", r"(?i)passcode:|证书[^\n]*[:：]|验证码[^\n]*[:：]", r"(?i)username:",
                              r"(?i)please enter response:", pexpect.EOF])
            if i == 0:
                asked["pwd"] += 1
                if asked["pwd"] > 1:
                    raise Stop("VPN 认证失败：学号或密码不对，请在界面里重新保存")
                child.sendline(pwd)
            elif i == 1:
                asked["code"] += 1
                if not code:
                    raise Stop("VPN 要求证书验证（身份证后 6 位），请先在界面里保存")
                if asked["code"] > 1:
                    raise Stop("VPN 证书验证失败：身份证后 6 位不对，请在界面里重新保存")
                child.sendline(code)
            elif i == 2:
                child.sendline(user)
            elif i == 3:
                # 例：「补充额外凭据，4位缺位电话号码：[153****5085]」
                question = child.before.strip().splitlines()[-1] if child.before.strip() else "（无提示）"
                asked["resp"] += 1
                m = re.search(r"\[([0-9*]{7,})\]", question)
                answer = fill_mask(m.group(1), tel) if m and "电话" in question else None
                if answer is None:
                    raise Stop(f"VPN 要求补充验证：{question}。"
                               + ("请在界面里保存手机号" if "电话" in question else "程序不认识这个问题，请告诉我"))
                if asked["resp"] > 2:
                    raise Stop("VPN 补充验证失败：手机号不对，请在界面里重新保存")
                child.sendline(answer)
            else:
                break
    except pexpect.TIMEOUT:
        raise Stop("VPN 认证超时（vpn.pku.edu.cn 没有响应）")
    finally:
        out = (child.before or "") if isinstance(child.before, str) else ""
        child.close()
    info = dict(re.findall(r"^([A-Z_]+)='([^']*)'", out, re.M))
    if "COOKIE" not in info:
        tail = out
        for s in secrets:
            tail = tail.replace(s, "***")
        raise Stop("VPN 认证失败：" + " / ".join(tail.strip().splitlines()[-3:])[:200])
    return info


def ensure() -> Vpn:
    """能直连就直连；否则登录 VPN 并开本地 SOCKS 代理。失败会重新判断网络、再试一次（网络可能刚切换）。"""
    try:
        return _ensure_once()
    except Stop as e:
        log(f"网络准备失败，5 秒后重试一次：{e}")
        time.sleep(5)
        return _ensure_once()


def _ensure_once() -> Vpn:
    if reachable():
        log("网络：校园网直连，不需要 VPN")
        return Vpn(None)
    if port_open() and reachable(via_vpn=True):
        log("网络：VPN 已经连着，直接使用")
        return Vpn(PROXY)
    log("网络：不在校园网，正在连接北大 VPN…")
    info = authenticate()
    LOG_DIR.mkdir(exist_ok=True)
    logf = open(LOG_DIR / "vpn.log", "a")
    proc = subprocess.Popen(
        # --no-dtls：不用 UDP 的 ESP 通道，只走 TLS；ESP 协商有时要等一分多钟才通
        [OPENCONNECT, "--protocol=pulse", "--no-dtls", "--cookie-on-stdin", "--servercert", info["FINGERPRINT"],
         "--script-tun", "--script", f"{OCPROXY} -D {SOCKS_PORT}", info.get("CONNECT_URL", VPN_URL)],
        stdin=subprocess.PIPE, stdout=logf, stderr=subprocess.STDOUT, text=True, env=clean_env(),
    )
    proc.stdin.write(info["COOKIE"] + "\n")
    proc.stdin.close()
    vpn = Vpn(PROXY, proc)
    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(1)
        if proc.poll() is not None:
            raise Stop(f"VPN 连接中断（退出码 {proc.returncode}），详情见 logs/vpn.log")
        if port_open() and reachable(via_vpn=True, timeout=5):
            log("网络：VPN 已连接")
            return vpn
    vpn.stop()
    raise Stop("VPN 已登录，但 45 秒内仍打不开 mpac，详情见 logs/vpn.log")


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        try:
            v = ensure()
            log(f"mpac 可以访问（{'通过 VPN' if v.proxy else '直连'}）")
            v.stop()
        except Stop as e:
            log(f"❌ {e}")
    else:
        print(__doc__)
