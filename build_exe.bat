@echo off
chcp 65001 >nul
cd /d %~dp0

if exist .venv\Scripts\python.exe (set PY=.venv\Scripts\python.exe) else (set PY=python)

echo [1/2] 安装依赖...
%PY% -m pip install -r requirements.txt || goto :err

echo [2/2] 使用 PyInstaller 打包...
%PY% -m PyInstaller --noconfirm --clean --windowed --name imgReEditor --collect-all qdarktheme main.py || goto :err

echo.
echo 打包完成： dist\imgReEditor\imgReEditor.exe
echo 可运行 "dist\imgReEditor\imgReEditor.exe --selftest" 验证打包结果。
pause
exit /b 0

:err
echo.
echo 打包失败，请检查上方错误信息。
pause
exit /b 1
