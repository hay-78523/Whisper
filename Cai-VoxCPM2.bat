@echo off
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "HF_HOME=%~dp0data\hf_cache"
set "PATH=%~dp0python;%~dp0ffmpeg-master-latest-win64-gpl-shared\bin;%PATH%"
echo === CAI VOXCPM2 (ban test) - tai ~5GB, chi 1 lan ===
"%~dp0python\python.exe" setup_voxcpm.py
echo.
echo Xong thi mo lai Start-Windows.bat, vao /admin xem "Engine giong clone".
pause
