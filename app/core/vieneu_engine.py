# -*- coding: utf-8 -*-
"""VieNeu-TTS — model AI voice thuan Viet chay local (48kHz, 20 giong Bac/Trung/Nam,
clone tuc thi tu mau 3-8 giay, ho tro the [cười]/[thở dài] goc). Apache 2.0.

Chay qua DAEMON thuong truc trong venv rieng: nap model 1 lan (~40s dau tien),
sau do moi cau chi mat ~2 giay. Chua cai venv thi available() = False, tool an nhom giong.
"""

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .users import _user_data_dir

DATA = _user_data_dir()
VENV_PY = DATA / "vieneu_env" / "bin" / "python"
if sys.platform == "win32":
    VENV_PY = DATA / "vieneu_env" / "Scripts" / "python.exe"
WORKER = Path(__file__).resolve().parent / "vieneu_worker.py"
CUSTOM_DIR = DATA / "vieneu_voices"          # giong duc rieng tu mau 3-8 giay

_CUSTOM_LABEL = {
    "HoaiMy": "Hoài My", "HoaiMy_tram": "Hoài My trầm", "HoaiMy_cao": "Hoài My cao",
}


def list_custom():
    """[(voice_id, nhan)] cho cac giong duc rieng trong kho."""
    if not CUSTOM_DIR.is_dir():
        return []
    out = []
    for f in sorted(CUSTOM_DIR.glob("*.wav")):
        name = f.stem
        label = _CUSTOM_LABEL.get(name, name.replace("_", " "))
        out.append(("vieneu:@" + name, "%s — nữ tự nhiên (đúc riêng)" % label))
    return out

# 20 giong dung san — (id, nhan hien thi)
PRESET_VOICES = [
    ("vieneu:Adam", "Adam — nam miền Nam, tự nhiên"),
    ("vieneu:Xuân Vĩnh", "Xuân Vĩnh — nam miền Nam, tự nhiên"),
    ("vieneu:Thái Sơn", "Thái Sơn — nam miền Nam, kể chuyện"),
    ("vieneu:Minh Triết", "Minh Triết — nam miền Nam, tin tức"),
    ("vieneu:Đức Trí", "Đức Trí — nam miền Nam, audiobook"),
    ("vieneu:Phạm Tuyên", "Phạm Tuyên — nam miền Bắc, tự nhiên"),
    ("vieneu:Minh Đức", "Minh Đức — nam miền Bắc, tin tức"),
    ("vieneu:Thanh Bình", "Thanh Bình — nam miền Bắc, kể chuyện"),
    ("vieneu:Quang Sơn", "Quang Sơn — nam miền Trung, tự nhiên"),
    ("vieneu:Ngọc Huyền", "Ngọc Huyền — nữ miền Bắc, tự nhiên"),
    ("vieneu:Trúc Ly", "Trúc Ly — nữ miền Bắc, tự nhiên"),
    ("vieneu:Đoan Trang", "Đoan Trang — nữ miền Bắc, tự nhiên"),
    ("vieneu:Ngọc Linh", "Ngọc Linh — nữ miền Bắc, kể chuyện"),
    ("vieneu:Mai Anh", "Mai Anh — nữ miền Bắc, tin tức"),
    ("vieneu:Quỳnh Anh", "Quỳnh Anh — nữ miền Bắc, audiobook"),
    ("vieneu:Ngọc Trân", "Ngọc Trân — nữ miền Trung, tự nhiên"),
    ("vieneu:Thục Đoan", "Thục Đoan — nữ miền Nam, kể chuyện"),
    ("vieneu:Thùy Dung", "Thùy Dung — nữ miền Nam, tin tức"),
    ("vieneu:Mỹ Duyên", "Mỹ Duyên — nữ miền Nam, audiobook"),
    ("vieneu:Kim Thanh", "Kim Thanh — nữ miền Nam, audiobook"),
]

_proc = [None]
_lock = threading.Lock()


def available():
    return VENV_PY.exists()


def _ensure_worker():
    """Khoi dong daemon neu chua co / da chet. Tra ve process."""
    p = _proc[0]
    if p is not None and p.poll() is None:
        return p
    from ..config import VIENEU_PRECISION
    p = subprocess.Popen([str(VENV_PY), str(WORKER), VIENEU_PRECISION],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
    # cho dong {"ready": true} — lan dau nap model co the ~40s
    deadline = time.time() + 300
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            raise RuntimeError("Engine VieNeu khong khoi dong duoc.")
        try:
            if json.loads(line).get("ready"):
                _proc[0] = p
                return p
        except ValueError:
            continue
    raise RuntimeError("Engine VieNeu khoi dong qua lau.")


def synth_cues_vieneu(texts, voice=None, ref_audio=None, progress=None):
    """Doc N doan — tra ve list wav bytes theo thu tu. Model nap 1 lan, giu nong."""
    if not available():
        raise ValueError("VieNeu chưa cài trên máy này (xem README mục VieNeu).")
    if voice and voice.startswith("@"):          # giong duc rieng -> dung mau lam ref
        ref = CUSTOM_DIR / (voice[1:] + ".wav")
        if not ref.exists():
            raise ValueError("Không có giọng đúc riêng '%s'." % voice[1:])
        ref_audio, voice = str(ref), None
    with _lock:                                   # daemon xu ly tuan tu
        for attempt in (1, 2):                    # worker chet giua chung -> khoi dong lai 1 lan
            p = _ensure_worker()
            with tempfile.TemporaryDirectory() as td:
                req = {"texts": texts, "voice": voice, "ref_audio": ref_audio, "out_dir": td}
                try:
                    p.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                    p.stdin.flush()
                except (BrokenPipeError, OSError):
                    _proc[0] = None
                    continue
                if progress:
                    progress(0, len(texts))
                while True:
                    line = p.stdout.readline()
                    if not line:                  # worker chet
                        _proc[0] = None
                        break
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if "error" in msg:
                        raise RuntimeError("VieNeu loi: %s" % msg["error"])
                    if "progress" in msg and progress:
                        progress(msg["progress"], msg["total"])
                    if "done" in msg:
                        return [Path(f).read_bytes() if f else b"" for f in msg["done"]]
        raise RuntimeError("Engine VieNeu gap su co — thu lai.")
