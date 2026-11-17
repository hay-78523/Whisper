@echo off
rem Khoi dong Whisper STT tren Windows - nhay dup chuot vao file nay la chay.
rem Ban V4: da dong goi san Python + PyTorch + Voice Clone trong thu muc python\
rem -> KHONG can cai gi, KHONG phu thuoc Python cua may (chay duoc may quan net).
cd /d "%~dp0"
echo === Whisper STT ===

if exist "%~dp0python\python.exe" goto portable
echo [!] Khong thay thu muc python\ (ban dong goi san) - chuyen sang che do tu cai dat.
goto install

:portable
echo [*] Ban dong goi san: Python + PyTorch + Voice Clone - khong can cai dat.
set "PY=%~dp0python\python.exe"
rem Chan moi thu vien Python cua may (Python he thong / user site) chen vao
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PATH=%~dp0python;%~dp0python\Scripts;%~dp0ffmpeg-master-latest-win64-gpl-shared\bin;%PATH%"
rem Model Whisper / vocoder tai ve luu NGAY TRONG thu muc tool (may quan net
rem thuong xoa thu muc user khi khoi dong lai -> khong phai tai lai moi lan)
set "HF_HOME=%~dp0data\hf_cache"
set "KMP_DUPLICATE_LIB_OK=TRUE"

echo [*] Kiem tra engine Voice Clone (lan dau co the mat 10-40 giay)...
"%PY%" -c "import torch, f5_tts.api; print('[v] Voice Clone san sang - torch', torch.__version__)"
if errorlevel 1 (
    echo.
    echo [x] Engine Voice Clone KHONG chay duoc tren may nay.
    echo [x] 1. Giai nen lai DAY DU file zip ^(dung WinRAR / 7-Zip^), dat o thu muc ngan
    echo [x]    vi du  D:\WhisperSTT  hoac  C:\W   - KHONG chay truc tiep trong file zip.
    echo [x] 2. Tam tat phan mem diet virus / dong bang may neu no chan file .dll
    echo [x] Cac tinh nang khac van chay. Chup man hinh nay gui ky thuat neu van loi.
    echo.
    pause
)

rem May co card NVIDIA: tu tai PyTorch ban GPU (1 lan, ~3GB) - loi thi van chay CPU
"%PY%" setup_gpu.py

rem AI local (Ollama) cho "AI bien kich": cua so rieng, khong chan tool
start "AI local" /min "%PY%" setup_ai.py
rem Tai model Chatterbox (giong Anh tu nhien) chay nen, chi lan dau
start "Chatterbox" /min "%PY%" setup_chatterbox.py --wait
rem Tach nhac nen mau giong (Demucs): cai thu vien + tai model ~320MB chay nen, chi lan dau
start "Tach nhac nen" /min "%PY%" app\core\vocalsep.py --prefetch

echo [*] Khoi dong server... Trinh duyet se tu mo. Dong cua so nay la TAT ung dung.
"%PY%" run.py --open
pause
exit /b

:install

rem -- Kiem tra va Tu dong cai Python 3.11 neu chua co --
where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py -3
) else (
    set PY=python
)

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo [!] May tinh cua ban chua co Python.
    echo [*] Dang TU DONG TAI VA CAI DAT Python 3.11 - Vui long doi vai phut...
    curl -L -o python-installer.exe https://www.python.org/ftp/python/3.11.8/python-3.11.8-amd64.exe
    echo [*] Dang tien hanh cai dat - Vui long khong tat cua so nay...
    start /wait python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
    del python-installer.exe
    
    echo [*] Dang cap nhat duong dan Path...
    set "PATH=%USERPROFILE%\AppData\Local\Programs\Python\Python311;%USERPROFILE%\AppData\Local\Programs\Python\Python311\Scripts;%PATH%"
    set PY="%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe"
    
    %PY% --version >nul 2>nul
    if errorlevel 1 (
        echo [x] Cai dat tu dong that bai. Vui long cai thu cong tu https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo [v] Cai dat Python hoan tat!
)

rem -- Kiem tra va Tu dong cai Microsoft Visual C++ Redistributable danh cho AI --
reg query "HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" /v Version >nul 2>nul
if errorlevel 1 (
    echo [!] Thieu Microsoft Visual C++ Redistributable - Yeu cau de chay AI.
    echo [*] Dang TU DONG TAI VA CAI DAT VC++ - Neu co bang xac nhan hien len, vui long chon YES...
    curl -L -o vc_redist.x64.exe https://aka.ms/vs/17/release/vc_redist.x64.exe
    start /wait vc_redist.x64.exe /install /quiet /norestart
    del vc_redist.x64.exe
)

echo [*] Kiem tra / cai thu vien co ban - lan dau hoi lau...
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [x] Cai thu vien loi - kiem tra mang roi chay lai.
  pause
  exit /b 1
)

%PY% download_ffmpeg.py
if errorlevel 1 (
  pause
  exit /b 1
)

set "PATH=%~dp0ffmpeg-master-latest-win64-gpl-shared\bin;%PATH%"

echo [*] Kiem tra Voice Clone (engine + model giong Viet)...
%PY% setup_clone.py --check >nul 2>nul
if errorlevel 1 (
    echo [*] Chua day du - dang tu dong cai dat.
    echo [*] Lan dau tai ~2.5GB PyTorch + 1.3GB model giong, mat 10-25 phut tuy mang.
    echo [*] Tai bi dut cung khong sao: chay lai la tiep tuc tu cho dang do.
    %PY% setup_clone.py --no-test
    if errorlevel 1 (
        echo [!] Voice Clone chua hoan tat - tool VAN CHAY duoc voi cac giong khac.
        echo [!] Vao trang /admin bam "Tai model giong Viet", hoac chay lai:
        echo [!]     python setup_clone.py
    )
)

rem May co card NVIDIA: kich hoat / tai PyTorch ban GPU
%PY% setup_gpu.py

echo [*] Khoi dong server... Trinh duyet se tu mo. Dong cua so nay la TAT ung dung.
%PY% run.py --open
pause
