@echo off
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "HF_HOME=%~dp0data\hf_cache"
set "PATH=%~dp0python;%~dp0ffmpeg-master-latest-win64-gpl-shared\bin;%PATH%"
echo === TEST TAT CA GIONG CLONE (GPU ~5-15 giay/giong) ===
"%~dp0python\python.exe" test_voices.py %*
echo.
echo Gui thu muc test_voices (file wav + BAO_CAO.csv) cho ky thuat.
pause
