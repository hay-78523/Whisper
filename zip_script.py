import zipfile
import os

zip_name = "WhisperSTT_Fixed.zip"
exclude_dirs = {"data", ".git", "__pycache__", "venv", "test_env", "zirmiiNP", "zi0amcy0"}
exclude_exts = {".zip", ".mp3", ".wav", ".aiff", ".m4a"}

if os.path.exists(zip_name):
    os.remove(zip_name)

with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith(".")]
        for file in files:
            if not file.startswith(".") and not any(file.endswith(ext) for ext in exclude_exts):
                filepath = os.path.join(root, file)
                arcname = os.path.relpath(filepath, ".")
                zipf.write(filepath, arcname)

print(f"Created {zip_name} successfully.")
