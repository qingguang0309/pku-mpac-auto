#!/bin/zsh
# 连上 PKU VPN 后运行一次：记录路由和各条线路的连通性，写到 logs/vpn_diag.txt
OUT="$(cd "$(dirname "$0")/.." && pwd)/logs/vpn_diag.txt"
{
  echo "### $(date)"
  echo "### 网卡"; ifconfig | grep -E "^[a-z]|inet " 
  echo "### 路由表"; netstat -rn -f inet | head -60
  echo "### DNS"; scutil --dns | grep -E "nameserver|search domain" | sort -u
  t() { printf "%-36s " "$1"; shift; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" --max-time 8 "$@" || echo "失败"; }
  echo "### 连通性"
  t "mpac 直连"           --noproxy '*' https://mpac.pku.edu.cn/
  t "mpac 走代理7890"     -x http://127.0.0.1:7890 https://mpac.pku.edu.cn/
  t "google 走代理7890"   -x http://127.0.0.1:7890 https://www.google.com/
  t "anthropic 走代理7890" -x http://127.0.0.1:7890 https://api.anthropic.com/
  t "baidu 直连"          --noproxy '*' https://www.baidu.com/
} > "$OUT" 2>&1
echo "已写入 $OUT ，现在可以断开 VPN 了"
