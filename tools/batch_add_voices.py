import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core import clone
from app.core.vieneu_engine import DATA

def clean_name(f):
    name = f.replace("voice_preview_", "")
    name = name.split(".mp3")[0]
    name = name.split(" - ")[0]
    name = name.split(" for ")[0]
    name = name.split(",")[0]
    name = name.replace(" ", "_").strip("_")
    name = re.sub(r'[^a-zA-Z0-9_]', '', name)
    return name[:24]

voice_dir = Path("/Users/phatle/whisper_stt/voice")

print("Bắt đầu tự động thêm tất cả giọng clone...")
for filename in voice_dir.iterdir():
    if filename.is_file() and filename.suffix.lower() in [".mp3", ".wav"]:
        name = clean_name(filename.name)
        print(f"\n--- Xử lý: {name} ({filename.name}) ---")
        
        try:
            d = DATA / "voice_profiles" / name
            if d.exists():
                print(f" -> Xóa thư mục cũ đang lỗi...")
                clone.delete_profile(name)
            
            print(f" -> Đang nhận diện chữ và tự tạo test âm thanh...")
            audio_bytes = filename.read_bytes()
            clone.add_profile(name, audio_bytes, "")
            print(f" -> HOÀN THÀNH {name}!")
        except Exception as e:
            print(f" -> LỖI với {name}: {e}")
