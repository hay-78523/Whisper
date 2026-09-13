import os
import zipfile
import stat

def zip_for_client():
    zip_name = "WhisperSTT_Delivery.zip"
    if os.path.exists(zip_name):
        os.remove(zip_name)
    
    print(f"[*] Dang dong goi phan mem vao {zip_name} (Sẽ bao gồm model 1.3GB và giọng mẫu của bạn)...")
    
    excludes = {
        "__pycache__", ".git", ".DS_Store", "f5env", "venv", 
        "python-installer.exe", "vc_redist.x64.exe"
    }
    
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk("."):
            # Bo qua thu muc rac
            dirs[:] = [d for d in dirs if d not in excludes and not d.startswith(".")]
            
            for file in files:
                if file.endswith(".zip") or file in excludes:
                    continue
                
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, ".")
                
                # Zip
                zipf.write(file_path, arcname)
    
    print(f"[v] Da tao thanh cong file: {zip_name}")
    print("[*] Ban co the copy file zip nay giao cho khach!")
    print("[*] Khi khach chay Start-Windows.bat, no se tu dong cai them PyTorch 2.5GB (vi moi may co card man hinh khac nhau, bat buoc khach phai tu tai de chong loi).")
    input("Bam Enter de thoat...")

if __name__ == "__main__":
    zip_for_client()
