# -*- coding: utf-8 -*-
"""Dong goi ban giao Windows (V4) — CHAY NGAY KHONG CAN CAI DAT.

Zip gom:
  * code tool + model giong Viet (data/f5_vi) + giong mau (voice/, data/voice_profiles)
  * python/   : Python 3.11 portable cho Windows, da cai san PyTorch (CPU) +
                F5-TTS + faster-whisper ... (dung bang _build_v4/build_portable.py)
  * ffmpeg-master-latest-win64-gpl-shared/bin : ffmpeg + ffprobe cho Windows

Khach chi can giai nen -> nhay dup Start-Windows.bat. Khong dung Python cua may,
khong tai PyTorch, khong can quyen Admin -> chay duoc may quan net.

    python3 export_for_client.py                 # tao WhisperSTT_Delivery_V4.zip
    python3 export_for_client.py --budget 150    # (noi bo) dung sau 150s, chay lai de lam tiep
"""
import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ZIP_NAME = "BAN_GIAO_KHACH/WhisperSTT_VoiceClone_Studio.zip"
STATE = ROOT / "_build_v4" / "export_state.json"

PYTHON_SRC_CANDS = [ROOT / "python", ROOT / "_build_v4" / "python",
                    Path.home() / "build" / "out" / "python"]
FFMPEG_DIR = "ffmpeg-master-latest-win64-gpl-shared"

TOP_FILES = ["HUONG-DAN.txt", "BANG-THE-BIEU-CAM.txt", "Test-Voices.bat", "test_voices.py", "run.py", "setup_gpu.py", "setup_ai.py", "setup_chatterbox.py", "cli.py", "setup_clone.py", "download_ffmpeg.py",
             "requirements.txt", "Start-Windows.bat", "README.md", "WORKFLOW.md",
             "users.json"]
TOP_DIRS = ["app", "voice", "data/voice_profiles", "data/previews", "models_chatterbox"]
# model giong Viet: ban da loc chi giu trong so EMA (1.35GB, y het khi suy dien)
# thay cho model_last.pt goc 5.4GB (co ca optimizer de train tiep - khach khong can)
MODEL_CANDS = [ROOT / "_build_v4" / "model_ema.pt", ROOT / "data" / "f5_vi" / "model.pt"]
# vocoder + model Whisper kem san (cau truc cache HuggingFace) -> khong can tai
HF_BUNDLE = ROOT / "_build_v4" / "hf_cache"
SKIP_NAMES = {"__pycache__", ".DS_Store", ".git"}
SKIP_SUFFIX = (".pyc", ".part", ".zip", ".lib")   # .lib: thu vien lien ket C++, chay khong can
STORE_SUFFIX = (".safetensors", ".pt", ".mp3", ".zip", ".dll", ".pyd", ".exe", ".bin", ".so", ".whl")


def walk(src, arc_prefix):
    if src.is_file():
        yield src, arc_prefix
        return
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_NAMES)
        for f in sorted(files):
            if f in SKIP_NAMES or f.endswith(SKIP_SUFFIX):
                continue
            p = Path(root) / f
            yield p, arc_prefix + "/" + p.relative_to(src).as_posix()


def plan(edition=""):
    py = next((p for p in PYTHON_SRC_CANDS if (p / "python.exe").exists()), None)
    if not py:
        sys.exit("[x] Khong thay Python portable (python/python.exe). Dung bang:\n"
                 "    python3 _build_v4/build_portable.py <py.tar.gz> _build_v4/wheels python")
    ff = ROOT / FFMPEG_DIR / "bin"
    if not (ff / "ffmpeg.exe").exists():
        sys.exit("[x] Thieu %s/bin/ffmpeg.exe (ban Windows)." % FFMPEG_DIR)
    items = []
    for f in TOP_FILES:
        if (ROOT / f).exists():
            items.append((ROOT / f, f))
    for d in TOP_DIRS:
        if (ROOT / d).exists():
            items += list(walk(ROOT / d, d))
    model = next(p for p in MODEL_CANDS if p.exists())
    items.append((model, "data/f5_vi/model.pt"))
    items.append((ROOT / "data" / "f5_vi" / "vocab.txt", "data/f5_vi/vocab.txt"))
    if HF_BUNDLE.exists():
        # ban khach chi soan kich ban -> khong can Whisper large (chi giu small de
        # tu nghe script khi them giong moi)
        items += [x for x in walk(HF_BUNDLE, "data/hf_cache") if "large-v3" not in x[1]]
    for f in sorted(ff.iterdir()):
        if f.name.lower() != "ffplay.exe":
            items.append((f, FFMPEG_DIR + "/bin/" + f.name))
    items += list(walk(py, "python"))
    if edition:
        # ban Doanh nghiep: kem lop nhan dien + file ACTIVE de tool tu bat
        items += list(walk(ROOT / "editions" / edition, "editions/" + edition))
        act = ROOT / "_build_v4" / ("ACTIVE_" + edition)
        act.parent.mkdir(parents=True, exist_ok=True)
        act.write_text(edition, encoding="utf-8")
        items.append((act, "editions/ACTIVE"))
    # model nang nhat de cuoi (de chia lam nhieu luot khi --budget)
    items.sort(key=lambda x: x[0].stat().st_size > 1_000_000_000)
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=0)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--edition", default="", help="vd: starlight (thu muc editions/<ten>)")
    a = ap.parse_args()
    os.chdir(ROOT)
    global ZIP_NAME, STATE
    if a.edition:
        ed = ROOT / "editions" / a.edition / "edition.json"
        if not ed.is_file():
            sys.exit("[x] Khong thay %s" % ed)
        name = json.loads(ed.read_text(encoding="utf-8")).get("name") or a.edition
        ZIP_NAME = "BAN_GIAO_KHACH/%s.zip" % name.replace(" ", "_")
        STATE = ROOT / "_build_v4" / ("export_state_%s.json" % a.edition)
    Path(ZIP_NAME).parent.mkdir(exist_ok=True)
    t0 = time.time()
    items = plan(a.edition)
    done = 0
    if not a.fresh and STATE.exists() and Path(ZIP_NAME).exists():
        done = json.loads(STATE.read_text()).get("done", 0)
    mode = "a" if done else "w"
    if mode == "w":
        print("[*] Dang dong goi %d file vao %s ..." % (len(items), ZIP_NAME))
    with zipfile.ZipFile(ZIP_NAME, mode, zipfile.ZIP_DEFLATED, allowZip64=True,
                         compresslevel=6) as z:
        i = done
        while i < len(items):
            src, arc = items[i]
            el = time.time() - t0
            if a.budget and (el > a.budget or
                             (src.stat().st_size > 1_000_000_000 and el > 5)):
                break           # file khong lo chi bat dau o dau 1 luot
            comp = zipfile.ZIP_STORED if src.suffix.lower() in STORE_SUFFIX \
                else zipfile.ZIP_DEFLATED
            z.write(src, arc, compress_type=comp)
            i += 1
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"done": i, "total": len(items)}))
    if i < len(items):
        print("[..] Da xong %d/%d file — chay lai lenh de lam tiep." % (i, len(items)))
        return 2
    print("[v] Da tao %s (%.2f GB)" % (ZIP_NAME, Path(ZIP_NAME).stat().st_size / 1e9))
    print("[*] Khach: giai nen -> nhay dup Start-Windows.bat (khong can cai gi them).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
