"""按 config.toml 安装 / 卸载每周自动预约的 macOS 定时任务（launchd）。

运行时间由配置推出来：第一条规则（如周五）开放的那天（周六），open_at 之前 prepare_minutes 分钟（00:05）。

  uv run python scheduler.py install     # 安装或更新（改了 config.toml 后重新运行这条）
  uv run python scheduler.py uninstall   # 停用
  uv run python scheduler.py status      # 查看是否已安装
"""
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from common import ROOT, WEEKDAYS, load_config, run_schedule

LABEL = "cn.pku.mpac-auto"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def describe() -> str:
    wd, h, m = run_schedule(load_config())
    return f"每周{WEEKDAYS[wd - 1]} {h:02d}:{m:02d}"


def pmset_command() -> str:
    """让 Mac 在运行前 2 分钟自动唤醒的命令（需要用户自己用管理员权限执行）。"""
    wd, h, m = run_schedule(load_config())
    t = h * 60 + m - 2
    if t < 0:
        t += 24 * 60
        wd = (wd - 2) % 7 + 1
    letter = "MTWRFSU"[wd - 1]
    return f"sudo pmset repeat wakeorpoweron {letter} {t // 60:02d}:{t % 60:02d}:00"


def install() -> None:
    wd, h, m = run_schedule(load_config())
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    (ROOT / "logs").mkdir(exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [uv, "run", "--project", str(ROOT), "python", str(ROOT / "book.py")],
        "WorkingDirectory": str(ROOT),
        # launchd 的 Weekday：0/7 = 周日，1 = 周一 … 6 = 周六
        "StartCalendarInterval": {"Weekday": wd % 7, "Hour": h, "Minute": m},
        "StandardOutPath": str(ROOT / "logs/launchd.out.log"),
        "StandardErrorPath": str(ROOT / "logs/launchd.err.log"),
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    }
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "load", str(PLIST)], check=True)
    print(f"已安装：{describe()} 运行 book.py（{PLIST}）")


def uninstall() -> None:
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    PLIST.unlink(missing_ok=True)
    print("已卸载定时任务")


def status() -> None:
    if not PLIST.exists():
        print("未安装")
        return
    cal = plistlib.loads(PLIST.read_bytes())["StartCalendarInterval"]
    wd = cal.get("Weekday")
    day = f"每周{WEEKDAYS[(wd - 1) % 7]}" if wd is not None else "每天"
    print(f"已安装：{day} {cal['Hour']:02d}:{cal['Minute']:02d}")
    print("唤醒命令：", pmset_command())


if __name__ == "__main__":
    {"install": install, "uninstall": uninstall, "status": status}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__)
    )()
