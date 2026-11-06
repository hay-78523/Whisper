"""Tai model Chatterbox (giong clone tieng Anh tu nhien, co muc cam xuc) - chi 1 lan.

Neu thu muc models_chatterbox da du file thi bo qua. Tai tiep duoc khi mat mang
giua chung (file .part). Tai xong tool tu dung Chatterbox cho giong tieng Anh.
"""
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "models_chatterbox"
BASE = "https://huggingface.co/ResembleAI/chatterbox/resolve/main/"
FILES = [("ve.safetensors", 5_000_000), ("tokenizer.json", 10_000), ("conds.pt", 50_000),
         ("s3gen.safetensors", 1_000_000_000), ("t3_cfg.safetensors", 2_000_000_000)]


def ok(f, min_size):
    p = OUT / f
    return p.exists() and p.stat().st_size >= min_size


def fetch(name, min_size):
    dst = OUT / name
    part = OUT / (name + ".part")
    for attempt in range(1, 6):
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "Mozilla/5.0",
                                                           **({"Range": "bytes=%d-" % have} if have else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                total = int(r.headers.get("Content-Length") or 0) + have
                mode = "ab" if have and r.status == 206 else "wb"
                done = have if mode == "ab" else 0
                t0 = time.time()
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if total and time.time() - t0 > 2:
                            t0 = time.time()
                            print("   %s: %d%% (%d/%d MB)" % (name, done * 100 // total, done >> 20, total >> 20), flush=True)
            if part.stat().st_size >= min_size:
                os.replace(part, dst)
                return True
        except Exception as e:
            print("   loi tai %s (lan %d): %s" % (name, attempt, e), flush=True)
            time.sleep(5 * attempt)
    return False


def main():
    OUT.mkdir(exist_ok=True)
    todo = [(f, s) for f, s in FILES if not ok(f, s)]
    if not todo:
        print("[v] Model Chatterbox da co du.")
        return 0
    print("[*] Tai model Chatterbox (~3.2GB, chi 1 lan) - giong clone tieng Anh tu nhien hon...")
    for f, s in todo:
        print(" - %s" % f, flush=True)
        if not fetch(f, s):
            print("[x] Khong tai duoc %s. Kiem tra mang roi mo lai tool (tool van chay bang F5)." % f)
            return 1
    print("[v] Xong! Tu lan tao giong tiep theo, giong tieng Anh dung Chatterbox.")
    return 0


if __name__ == "__main__":
    rc = main()
    if "--wait" in sys.argv:
        time.sleep(8)
    sys.exit(rc)
