import os
import sys
import urllib.request
import zipfile
import time
import ssl

# Fix SSL: CERTIFICATE_VERIFY_FAILED on older Windows machines or restricted networks
ssl._create_default_https_context = ssl._create_unverified_context

URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"
ZIP_FILE = "ffmpeg.zip"
EXTRACT_DIR = "ffmpeg-master-latest-win64-gpl-shared"
TARGET_EXE = os.path.join(EXTRACT_DIR, "bin", "ffmpeg.exe")

def report(count, block_size, total_size):
    global start_time
    if count == 0:
        start_time = time.time()
        return
    duration = time.time() - start_time
    progress_size = int(count * block_size)
    try:
        speed = progress_size / (1024 * 1024 * duration)
    except ZeroDivisionError:
        speed = 0
    percent = int(count * block_size * 100 / total_size)
    sys.stdout.write(f"\rDang tai FFmpeg... {percent}% ({progress_size / (1024*1024):.1f} MB / {total_size / (1024*1024):.1f} MB) - Toc do: {speed:.1f} MB/s")
    sys.stdout.flush()

if __name__ == "__main__":
    if os.path.exists(TARGET_EXE):
        print("[v] Da tim thay FFmpeg, bo qua tai xuong.")
        sys.exit(0)
        
    print("[!] Bat dau tai thu vien ho tro AI (FFmpeg Shared)...")
    print("Vui long kien nhan, file nang khoang 130MB. Quoc te co the hoi cham.")
    
    retries = 3
    for attempt in range(retries):
        try:
            urllib.request.urlretrieve(URL, ZIP_FILE, reporthook=report)
            print("\n[v] Tai xong! Dang giai nen...")
            with zipfile.ZipFile(ZIP_FILE, 'r') as zip_ref:
                zip_ref.extractall()
            os.remove(ZIP_FILE)
            print("[v] Cai dat FFmpeg thanh cong!")
            sys.exit(0)
        except Exception as e:
            print(f"\n[x] Loi khi tai (lan {attempt + 1}): {e}")
            time.sleep(3)
    
    print("[x] Khong the tai FFmpeg sau nhieu lan thu. Vui long kiem tra lai mang (Wifi/LAN) hoac tat VPN neu co.")
    sys.exit(1)
