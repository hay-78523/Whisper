@echo off
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "HF_HOME=%~dp0data\hf_cache"
set "PATH=%~dp0python;%~dp0ffmpeg-master-latest-win64-gpl-shared\bin;%PATH%"
echo === KIEM TRA CAM XUC + DO GIONG (GPU ~20-40 giay/giong) ===
"%~dp0python\python.exe" kiem_tra_cam_xuc.py %*
echo.
echo Gui thu muc kiem_tra_cam_xuc (wav + BAO_CAO.csv) cho ky thuat.
pause
