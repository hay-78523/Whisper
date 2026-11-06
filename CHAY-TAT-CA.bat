@echo off
rem BAM 1 LAN LA CHAY HET: tai GPU + Chatterbox + AI local (lan dau), tao mau cam xuc,
rem kiem tra cam xuc + do giong, gom ket qua vao 1 file zip, roi mo phan mem.
cd /d "%~dp0"
title Whisper - CHAY TAT CA
echo ================================================================
echo   CHAY TAT CA: cai dat -^> mau cam xuc -^> kiem tra -^> gom ket qua
echo   Lan dau co the mat 30-90 phut (tai GPU ~3GB + AI ~3.5GB + tao mau).
echo   Cu de may chay, KHONG tat cua so nay.
echo ================================================================

if not exist "%~dp0python\python.exe" (
    echo [x] Thieu thu muc python\ - giai nen lai DAY DU file zip roi chay lai.
    pause
    exit /b 1
)
set "PY=%~dp0python\python.exe"
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PATH=%~dp0python;%~dp0python\Scripts;%~dp0ffmpeg-master-latest-win64-gpl-shared\bin;%PATH%"
set "HF_HOME=%~dp0data\hf_cache"
set "KMP_DUPLICATE_LIB_OK=TRUE"

echo.
echo [1] Kiem tra engine giong...
"%PY%" -c "import torch, f5_tts.api; print('[v] Engine giong san sang - torch', torch.__version__)"
if errorlevel 1 (
    echo [x] Engine giong khong chay. Giai nen lai day du, dat o thu muc ngan vd D:\W, tat diet virus.
    pause
    exit /b 1
)

echo.
echo [2] May co card NVIDIA: tai PyTorch ban GPU (1 lan, ~3GB)...
"%PY%" setup_gpu.py

echo.
echo [3] Kiem tra model Chatterbox (giong Anh co cam xuc)...
"%PY%" setup_chatterbox.py

echo.
echo [4] AI bien kich (Ollama) - tai trong cua so rieng, khong can cho...
start "AI local" /min "%PY%" setup_ai.py

echo.
"%PY%" chay_tat_ca.py

echo.
echo [*] Mo phan mem de thu bang tay. Dong cua so nay la TAT phan mem.
"%PY%" run.py --open
pause
