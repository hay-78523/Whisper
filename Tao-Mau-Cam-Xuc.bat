@echo off
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "HF_HOME=%~dp0data\hf_cache"
set "PATH=%~dp0python;%~dp0ffmpeg-master-latest-win64-gpl-shared\bin;%PATH%"
echo === TAO MAU CAM XUC + CHAM DO GIONG (can GPU NVIDIA, ~1-2 phut/giong) ===
"%~dp0python\python.exe" tao_mau_cam_xuc.py %*
echo.
echo Nghe thu trong thu muc mau_cam_xuc (file wav + BAO_CAO.csv).
pause
