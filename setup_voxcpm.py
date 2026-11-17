"""Cai VoxCPM2 (ban test) - engine clone giong ca tieng Anh + tieng Viet, Apache-2.0. Chi 1 lan.

  1. Thu vien voxcpm vao CHINH Python engine dang chay script nay (--no-deps: khong nang cap
     torch / transformers -> Chatterbox, F5 giu nguyen). Them inflect (doc so tieng Anh).
  2. Model openbmb/VoxCPM2 (~5GB) vao models_voxcpm2. Tai tiep duoc khi mat mang (file .part).

Windows: bam dup Cai-VoxCPM2.bat.   Mac: <python engine> setup_voxcpm.py
Xong thi vao /admin > Engine giong clone: "mac dinh ban test (VoxCPM2)" la dang dung VoxCPM2.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "models_voxcpm2"
BASE = "https://huggingface.co/openbmb/VoxCPM2/resolve/main/"
VOXCPM_VER = "2.0.3"
FILES = [("config.json", 1_000), ("tokenizer.json", 1_000_000), ("tokenizer_config.json", 1_000),
         ("special_tokens_map.json", 100), ("tokenization_voxcpm2.py", 500),
         ("audiovae.pth", 300_000_000), ("model.safetensors", 4_500_000_000)]


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


def _run(code):
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


def install_lib():
    r = _run("import voxcpm, inflect; print('ok')")
    if r.returncode == 0:
        print("[v] Thu vien voxcpm da co.")
        return True
    r = _run("import torch, transformers; print(torch.__version__)")
    if r.returncode != 0:
        print("[x] Python nay chua co torch/transformers (khong phai Python engine cua tool):\n    %s"
              % sys.executable)
        return False
    tv = r.stdout.strip()
    major_minor = tuple(int(x) for x in tv.split("+")[0].split(".")[:2])
    if major_minor < (2, 5):
        print("[x] VoxCPM2 can torch >= 2.5 (may nay %s). Chay lai Start-Windows.bat de cap nhat." % tv)
        return False
    print("[*] Cai thu vien voxcpm %s (torch %s giu nguyen)..." % (VOXCPM_VER, tv), flush=True)
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if subprocess.run(pip + ["--no-deps", "voxcpm==%s" % VOXCPM_VER]).returncode != 0:
        return False
    if subprocess.run(pip + ["--no-warn-conflicts", "inflect"]).returncode != 0:
        return False
    r = _run("import voxcpm; print('ok')")
    if r.returncode != 0:
        print("[x] Cai xong nhung khong import duoc voxcpm:\n%s" % (r.stderr or "")[-800:])
        return False
    print("[v] Thu vien voxcpm san sang.")
    return True


def main():
    print("=== CAI VOXCPM2 (ban test: clone giong Anh + Viet, cam xuc tu mau that) ===")
    if not install_lib():
        return 1
    OUT.mkdir(exist_ok=True)
    todo = [(f, s) for f, s in FILES if not ok(f, s)]
    if not todo:
        print("[v] Model VoxCPM2 da co du trong %s" % OUT)
    else:
        free = shutil.disk_usage(OUT).free
        need = sum(s for _f, s in todo) + 500_000_000
        if free < need:
            print("[x] O dia con %.1f GB, can ~%.1f GB de tai model VoxCPM2." % (free / 1e9, need / 1e9))
            return 1
        print("[*] Tai model VoxCPM2 (~5GB, chi 1 lan)...")
        for f, s in todo:
            print(" - %s" % f, flush=True)
            if not fetch(f, s):
                print("[x] Khong tai duoc %s. Kiem tra mang roi chay lai (tai tiep cho dang do)." % f)
                return 1
    print("[v] Xong! Mo (lai) tool -> /admin: Engine giong clone = VoxCPM2. Giong clone tu video")
    print("    se doc bang VoxCPM2; cau co the cam xuc dung doan hao hung / tram cat tu chinh video.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
