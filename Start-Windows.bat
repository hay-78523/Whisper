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

echo [*] Kiem tra du lieu cho Voice Clone...
mkdir ".\data\f5_vi" 2>nul
if not exist ".\data\f5_vi\vocab.txt" (
    echo Dang tai vocab.txt...
    curl -L -o ".\data\f5_vi\vocab.txt" https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice/resolve/main/config.json
)
if not exist ".\data\f5_vi\model.pt" (
    echo Dang tai model_last.pt khoang 1.3GB - vui long doi chut...
    curl -L -o ".\data\f5_vi\model.pt" https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice/resolve/main/model_last.pt
)

rem -- Cai PyTorch va F5-TTS truc tiep vao Python he thong (khong can f5env) --
%PY% -c "import ctypes; ctypes.windll.kernel32.SetErrorMode(0x8003); import torch, torchaudio, f5_tts" >nul 2>nul
if errorlevel 1 (
    echo [*] PyTorch / F5-TTS chua duoc cai, hoac bi loi phien ban.
    echo [*] Dang tien hanh cai dat lai... ^(Tai ~2.5GB, mat 5-15 phut tuy mang. Vui long KIEN NHAN!^)
    
    rem Go bo sach se cac ban cu/loi hien tai
    %PY% -m pip uninstall -y torch torchvision torchaudio f5-tts
    
    rem Thu cai dat ban CUDA truoc, va cai f5-tts khong de de len torch CUDA
    %PY% -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
    %PY% -m pip install f5-tts --extra-index-url https://download.pytorch.org/whl/cu118
    
    rem Kiem tra lai xem CUDA co that su chay duoc khong tren card cua ban
    %PY% -c "import ctypes; ctypes.windll.kernel32.SetErrorMode(0x8003); import torch; assert torch.cuda.is_available()" >nul 2>nul
    if errorlevel 1 (
        echo [!] May tinh nay khong tuong thich voi PyTorch CUDA ^(Khong co Card NVIDIA hoac chua cai Driver^).
        echo [*] Dang tu dong chuyen sang cai dat ban CPU ^(Chay duoc tren moi may^)...
        
        rem Go bo ban CUDA loi
        %PY% -m pip uninstall -y torch torchvision torchaudio
        
        rem Cai ban CPU
        %PY% -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
    )
    echo [v] Cai dat AI hoan tat!
)

echo [*] Khoi dong server... Trinh duyet se tu mo. Dong cua so nay la TAT ung dung.
%PY% run.py --open
pause
