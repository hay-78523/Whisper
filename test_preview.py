from app.core import clone
import time

t0 = time.time()
print("Starting synth...")
try:
    clips = clone.synth_cues_clone(["Xin chào! Mình là giọng đọc mới của bạn đây. Cùng tạo nội dung thật hay nha!"], "eleventlab")
    print(f"Synth done in {time.time()-t0:.2f}s, generated {len(clips)} clips")
    if clips and clips[0]:
        print(f"Clip size: {len(clips[0])} bytes")
    else:
        print("Clip is empty!")
except Exception as e:
    print(f"Error: {e}")
