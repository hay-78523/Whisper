@echo off
rem Khoi dong Whisper STT tren Windows - nhay dup chuot vao file nay la chay.
rem Lan dau se tu cai thu vien (can mang, vai phut). Cac lan sau vao thang.
cd /d "%~dp0"
echo === Whisper STT ===

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

echo [*] Khoi dong server... Trinh duyet se tu mo. Dong cua so nay la TAT ung dung.
%PY% run.py --open
pause
