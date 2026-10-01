@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente ausente. Execute scripts\install.bat primeiro.
  exit /b 1
)
call ".venv\Scripts\python.exe" -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
if errorlevel 1 (
  echo Instale a dependencia opcional: .venv\Scripts\python.exe -m pip install -r requirements-media-test.txt
  exit /b 1
)
call ".venv\Scripts\python.exe" -m pytest -q tests\test_p1.py -k ffmpeg
exit /b %errorlevel%
