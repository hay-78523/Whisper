import os
import re

files = os.listdir("/Users/phatle/whisper_stt/voice")

def clean_name(f):
    name = f.replace("voice_preview_", "")
    name = name.split(".mp3")[0]
    name = name.split(" - ")[0]
    name = name.split(" for ")[0]
    name = name.split(",")[0]
    # replace spaces with _
    name = name.replace(" ", "_").strip("_")
    # keep only alphanumeric and _
    name = re.sub(r'[^a-zA-Z0-9_]', '', name)
    # truncate to 24 chars
    return name[:24]

for f in files:
    print(f"{f} -> {clean_name(f)}")
