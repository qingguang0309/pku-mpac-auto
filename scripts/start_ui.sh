#!/bin/zsh
# 启动界面：服务已在运行就直接打开网页，否则后台启动服务（ui.py 会自己打开浏览器）
DIR="$(cd "$(dirname "$0")/.." && pwd)"
if curl -s -m 1 --noproxy '*' http://127.0.0.1:8765/api/ping >/dev/null; then
  open http://127.0.0.1:8765/
else
  mkdir -p "$DIR/logs"
  cd "$DIR" && nohup "$HOME/.local/bin/uv" run python ui.py >> logs/ui.log 2>&1 &
fi
