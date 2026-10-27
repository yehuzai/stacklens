@echo off
REM 在 Windows 上打包 stacklens.exe。需要 Python 3.9+ 且 tkinter 可用。
setlocal
cd /d "%~dp0"

py -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

python app.py --selftest
if errorlevel 1 (
  echo 自检失败，停止打包。
  exit /b 1
)

pyinstaller --noconfirm --clean --windowed --onefile ^
  --name stacklens ^
  app.py

echo.
echo 完成。产物：dist\stacklens.exe
endlocal
