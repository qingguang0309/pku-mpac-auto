#!/bin/zsh
# 用项目专属的 Chrome 配置（./profile）打开 mpac，并开启 9222 调试端口，
# 这样登录状态会保存下来，自动化脚本也能接管这个浏览器。
# *.pku.edu.cn 绕过本机代理直连（走代理会连不上 mpac），其余流量照常走 7890 代理。
DIR="$(cd "$(dirname "$0")/.." && pwd)"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$DIR/profile" \
  --remote-debugging-port=9222 \
  --proxy-server="http://127.0.0.1:7890" --proxy-bypass-list="*.pku.edu.cn;pku.edu.cn" \
  --no-first-run --no-default-browser-check \
  "https://mpac.pku.edu.cn/" >/dev/null 2>&1 &
