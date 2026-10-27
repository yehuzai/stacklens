#!/usr/bin/env bash
# 打包 macOS 版 stacklens.app。需要 Python 3.9+ 且 tkinter 可用。
set -euo pipefail
cd "$(dirname "$0")"

PY="${PY:-python3}"
"$PY" -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

python app.py --selftest

pyinstaller --noconfirm --clean --windowed \
  --name stacklens \
  --osx-bundle-identifier com.local.stacklens \
  app.py

echo
echo "完成。产物：dist/stacklens.app"
