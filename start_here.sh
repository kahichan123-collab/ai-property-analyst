#!/usr/bin/env bash
# ===================================================================
#  AI Property Analyst - Mac / Linux 啟動檔
#  用法：  bash start_here.sh        （或先 chmod +x start_here.sh）
#  需要 Python 3.10 或以上，之後在瀏覽器開 http://127.0.0.1:8765/
# ===================================================================
set -u
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "找不到 python3。請先安裝 Python 3.10 或以上。" >&2
  exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)'; then
  echo "Python 版本太舊，本工具需要 3.10 或以上。目前：$(python3 -V)" >&2
  exit 1
fi

PORT="${1:-8765}"

export ANALYST_PASSWORD=hkhouse2026
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

echo
echo "  AI Property Analyst"
echo "  -------------------"
echo "  網址   http://127.0.0.1:${PORT}/"
echo "  登入   帳號任意（例如 owner）  ·  密碼 hkhouse2026"
echo "  要停止：按 Ctrl+C"
echo

if command -v open >/dev/null 2>&1; then
  (sleep 2; open "http://127.0.0.1:${PORT}/") >/dev/null 2>&1 &
elif command -v xdg-open >/dev/null 2>&1; then
  (sleep 2; xdg-open "http://127.0.0.1:${PORT}/") >/dev/null 2>&1 &
fi

python3 analyst_server.py "${PORT}" local
