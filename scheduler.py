"""按 config.toml 安装 / 卸载每周自动预约的 macOS 定时任务（launchd）。

运行时间由配置推出来：每条规则的日子开放的那天（提前 13 天，如周三 → 周四），open_at 之前 prepare_minutes 分钟（00:05）。

  uv run python scheduler.py install     # 安装或更新（改了 config.toml 后重新运行这条）
  uv run python scheduler.py uninstall   # 停用
  uv run python scheduler.py status      # 查看是否已安装

另装一个提醒任务：每次运行前 3 小时（21:05）弹通知「今晚要自动预约，请插电、别合盖」。
"""
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from common import ROOT, WEEKDAYS, load_config, run_schedule

LABEL = "cn.pku.mpac-auto"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
REMIND_LABEL = f"{LABEL}.remind"
REMIND_PLIST = PLIST.with_name(f"{REMIND_LABEL}.plist")
REMIND_HOURS = 3  # 运行前几小时提醒


def remind_schedule(runs: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    out = set()
    for wd, h, m in runs:
        t = h * 60 + m - REMIND_HOURS * 60
        if t < 0:
            t, wd = t + 1440, (wd - 2) % 7 + 1
        out.add((wd, t // 60, t % 60))
    return sorted(out)


def describe() -> str:
    runs = run_schedule(load_config())
    days = "、".join(WEEKDAYS[wd - 1] for wd, _, _ in runs)
    return f"每周{days} {runs[0][1]:02d}:{runs[0][2]:02d}" if runs else "没有规则"


def pmset_command() -> str:
    """让 Mac 在每次运行前 2 分钟自动唤醒的命令（需要用户自己用管理员权限执行）。"""
    days, hm = set(), None
    for wd, h, m in run_schedule(load_config()):
        t = h * 60 + m - 2
        if t < 0:
            t += 24 * 60
            wd = (wd - 2) % 7 + 1
        days.add(wd)
        hm = (t // 60, t % 60)
    letters = "".join("MTWRFSU"[d - 1] for d in sorted(days))
    return f"sudo pmset repeat wakeorpoweron {letters} {hm[0]:02d}:{hm[1]:02d}:00"


def _load(path: Path, label: str, extra_args: list[str], runs: list[tuple[int, int, int]]) -> None:
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    plist = {
        "Label": label,
        "ProgramArguments": [uv, "run", "--project", str(ROOT), "python", str(ROOT / "book.py"), *extra_args],
        "WorkingDirectory": str(ROOT),
        # launchd 的 Weekday：0/7 = 周日，1 = 周一 … 6 = 周六
        "StartCalendarInterval": [{"Weekday": wd % 7, "Hour": h, "Minute": m} for wd, h, m in runs],
        "StandardOutPath": str(ROOT / "logs/launchd.out.log"),
        "StandardErrorPath": str(ROOT / "logs/launchd.err.log"),
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    }
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "load", str(path)], check=True)


def install() -> None:
    runs = run_schedule(load_config())
    (ROOT / "logs").mkdir(exist_ok=True)
    _load(PLIST, LABEL, [], runs)
    _load(REMIND_PLIST, REMIND_LABEL, ["--remind"], remind_schedule(runs))
    print(f"已安装：{describe()} 运行 book.py（{PLIST}），运行前 {REMIND_HOURS} 小时提醒插电、别合盖")


def uninstall() -> None:
    for p in (PLIST, REMIND_PLIST):
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        p.unlink(missing_ok=True)
    print("已卸载定时任务")


def status() -> None:
    if not PLIST.exists():
        print("未安装")
        return
    cal = plistlib.loads(PLIST.read_bytes())["StartCalendarInterval"]
    cal = cal if isinstance(cal, list) else [cal]
    days = "、".join(WEEKDAYS[(c["Weekday"] - 1) % 7] for c in cal)
    print(f"已安装：每周{days} {cal[0]['Hour']:02d}:{cal[0]['Minute']:02d}")
    print("提醒任务：", "已安装" if REMIND_PLIST.exists() else "未安装（再运行一次 install）")
    print("唤醒命令：", pmset_command())


if __name__ == "__main__":
    {"install": install, "uninstall": uninstall, "status": status}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__)
    )()
