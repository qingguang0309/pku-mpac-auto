# pku-mpac-auto

在 [mpac.pku.edu.cn](https://mpac.pku.edu.cn/) 上机预约（扫描电子显微镜）：可以手动一键预约，也可以每周定时自动预约。

## 一键使用

双击项目里的 **`mpac预约.app`**（可以拖到 Dock 上），浏览器会打开 http://127.0.0.1:8765 ：

1. **账号与 VPN**：第一次填写学号、密码、身份证后 6 位 → 保存 → 测试连接。都只存在本机钥匙串里。
2. **预约**：选日期和时段（可加备选），可先「查看空闲」，再点「预约…」→ 确认，下面会实时显示进度和结果。
3. **每周自动预约**：设置网站开放时间、提前准备分钟数和每周规则（首选 + 备选）→ 保存并开启；可以先「试运行一次」。
   下面列出接下来两周的每一次运行，某次不想约（比如这周三已经约到了）就点「取消这次」，只跳过那一次。

## 网络 / VPN

每次运行先试直连 mpac：在校园网就直接用；不在校园网就自动登录北大 VPN（vpn.pku.edu.cn，和 Ivanti Secure Access 客户端连的是同一个服务器），
依次填学号 → 密码 → 身份证后 6 位（证书验证）。

VPN 用开源客户端 openconnect（`brew install openconnect ocproxy`），**只在本机开一个 SOCKS 代理（127.0.0.1:11080）给预约程序的 Chrome 用**，
不改系统路由、不需要管理员密码，谷歌、本机代理等其它网络都不受影响。预约结束后自动断开。
Ivanti 客户端仍然可以照常手动使用，两者互不依赖。

## 文件

| 文件 | 作用 |
|---|---|
| `mpac预约.app` / `scripts/start_ui.sh` | 一键启动界面 |
| `assets/` + `scripts/make_icon.py` | 图标原图和生成脚本（换图后运行 `uv run --with pillow python scripts/make_icon.py`，再把 `assets/AppIcon.icns` 复制到 app 的 `Contents/Resources/applet.icns`） |
| `ui.py` + `ui/index.html` | 本地界面（只监听 127.0.0.1） |
| `book.py` | 预约流程：联网 → IAAA 登录 → 上机预约 → 选日期/时段 → 提交，被占就换下一个时段 |
| `vpn.py` | 判断是否需要 VPN，需要时登录北大 VPN 并开本地代理 |
| `common.py` | 日志、通知、钥匙串、配置读写 |
| `config.toml` | 每周自动预约的设置：开放时间 `open_at`、提前准备 `prepare_minutes`、每周规则 `rules`（界面里改也会写到这里） |
| `scheduler.py` | 按配置安装每周一次的 launchd 定时任务 |
| `logs/book.log` | 每次运行的记录；失败时还会存截图 `logs/fail_*.png`；VPN 详情在 `logs/vpn.log` |

## 命令行（可选）

```bash
uv run python book.py --date 20261120 --slot 14:00-17:00 --slot 09:00-12:00   # 指定日期和时段预约
uv run python book.py --date 20261120 --slot 14:00-17:00 --dry-run            # 只检查，不提交
uv run python book.py                                                          # 按 config.toml 的每周规则（定时任务跑的就是这个）
uv run python book.py --dry-run                                                # 同上，但不提交
uv run python book.py --skip 20261210                                          # 取消 12-10 那天的自动预约（规则不变）；--unskip 恢复
uv run python vpn.py check                                                     # 看现在需不需要 VPN、能不能连上
uv run python scheduler.py status                                              # 定时任务状态
```

## 每周自动预约怎么跑

网站大约每天 `open_at`（00:10）放出「今天 + 13 天」。`rules` 里每条都约（不是约到一个就停），每条在它那天开放的晚上各运行一次：

| 规则 | 哪天晚上运行 |
|---|---|
| 每周三 14:00-17:00 | 周四 00:05（约 13 天后的周三） |
| 每周四 14:00-17:00 | 周五 00:05 |
| 每周五 13:00-17:30 | 周六 00:05 |

每次运行：

1. `open_at` 前 `prepare_minutes` 分钟（00:05）开始：联网（必要时连 VPN）、登录、打开预约页，并弹通知「已开始、本次约哪个时段」；失败每 20 秒重试。
2. 从 `open_at` 前 `poll_before_minutes` 分钟（00:08）起每 3 秒刷新，新的一天一出现就提交，提交后重读表格确认约到了就立即结束、断开 VPN，不会等到 00:15；网站一直没开放才最多等到 `open_at` 后 `wait_max_minutes` 分钟（00:15）。
3. 规则勾了「有空就约」（partial）：有部分被占时把剩下空着的时间都约上（不连续就分几段提交）；没勾则整段都空才约。结果弹通知。
4. 这个时段里已经有你的预约（之前跑过或手动约过）就跳过，不重复约。

**临时取消某一次**：界面「接下来两周」里点「取消这次」（或 `book.py --skip YYYYMMDD`），那天的日期记进 `config.toml` 的 `skip_dates`。
到点程序照常启动，但不登录、不连 VPN，直接结束并弹通知「已按你的设置跳过」；规则和定时任务都不变，下一周照常约。点「恢复」撤销。过去的日子会自动清掉。

Mac 每次运行前要醒着：`sudo pmset repeat wakeorpoweron RFS 00:03:00`（周四、五、六 00:03 唤醒；改了规则后按界面提示更新）。

**注意：网站同一账号只能登录一处。** 自动预约运行时（周四、五、六 00:05 到约完，最晚 00:15）别在界面或浏览器里登录 mpac，否则会互相挤掉。
程序被挤掉后会自动重新登录；运行期间界面里会登录的按钮会暂时不可用。

运行时间由配置自动推算，界面里会显示「下一次」的具体日期。改完规则或时间要「保存并开启」（或 `uv run python scheduler.py install`），
并按界面提示更新 Mac 的自动唤醒，例如：`sudo pmset repeat wakeorpoweron S 00:03:00`（周六 00:03 唤醒）。

## 冲突处理

提交前先读预约表格：时段里只要有半小时已被别人占用，就跳过、换下一个时段。提交后重新读表格，格子里是你的名字才算成功。全部失败会弹通知。
