# -*- coding: utf-8 -*-
"""TACH NHAC NEN khoi giong noi (Demucs htdemucs_ft, two-stems = vocals).

Mau giong cat tu video quang cao thuong co nhac nen: clone thang thi giong bi lan nhac,
Whisper con nghe ao ("Hay subscribe cho kenh...") -> doc sai chu. Tach giong ra truoc
roi moi chon mau / lay loi mau.

Chay bang Python engine (python\\ trong ban portable, co torch) o TIEN TRINH RIENG
(khong lam nang server, tat xong la tra RAM/VRAM):
    python vocalsep.py --in mix.wav --out vocals.wav [--sr 24000] [--device auto|cuda|cpu]
-> in JSON {"ok": true, "music_db": ..., "device": ..., "seconds": ...}
   music_db = nang luong phan NHAC so voi phan GIONG (dB): ~ -40 la mau sach, > -20 la nhac to.

Cai thu vien (1 lan, KHONG doi phien ban goi nao):  pip install --no-deps demucs==4.1.0 julius==0.2.8
(demucs 4.1 nap model tu HuggingFace bang safetensors; tu doc/ghi wav nen khong can lameenc/sphn.)
Model (~320MB, 4 file) tai 1 lan vao data/hf_cache cua tool (HF_HOME) — mat mang thi dung ban da tai.
    python vocalsep.py --prefetch     # cai thu vien + tai san model (Start-Windows.bat chay nen)
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = "htdemucs_ft"
PIP_SPEC = ("demucs==4.1.0", "julius==0.2.8")


def _set_cache():
    """Model vao thu muc cua tool (ban Windows da dat HF_HOME=data\\hf_cache trong .bat)."""
    if not os.environ.get("HF_HOME"):
        os.environ["HF_HOME"] = str(ROOT / "data" / "hf_cache")


def _pick_device(want):
    import torch
    want = (want or "auto").lower()
    if want == "cuda" or (want == "auto" and torch.cuda.is_available()):
        if torch.cuda.is_available():
            return "cuda"
    return "cpu"                    # MPS: mot so phep FFT cua htdemucs chua on -> CPU cho chac


def _load(path):
    """wav/flac (soundfile) -> tensor (2, T) o 44.1kHz cho Demucs."""
    import numpy as np
    import soundfile as sf
    import torch
    y, sr = sf.read(str(path), dtype="float32", always_2d=True)      # (T, C)
    y = y.T
    if y.shape[0] == 1:
        y = np.concatenate([y, y], 0)
    elif y.shape[0] > 2:
        y = y[:2]
    wav = torch.from_numpy(np.ascontiguousarray(y))
    if sr != 44100:
        import torchaudio.functional as AF
        wav = AF.resample(wav, sr, 44100)
    return wav


def _importable():
    import subprocess
    r = subprocess.run([sys.executable, "-c", "import demucs.apply, demucs.pretrained"],
                       capture_output=True, text=True)
    return r.returncode == 0


def install():
    """Cai demucs + julius vao CHINH Python nay, --no-deps: torch / transformers / numpy giu
    nguyen phien ban (cac goi con lai demucs 4.1 can — einops, pyyaml, safetensors,
    huggingface_hub, tqdm — da co san trong Python engine)."""
    import subprocess
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
           "--no-warn-conflicts", "--no-deps"] + list(PIP_SPEC)
    r = subprocess.run(pip, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write((r.stderr or r.stdout or "")[-800:])
    return _importable()


def separate(src, dst, sr=24000, device="auto", model_name=MODEL, shifts=1, log=None):
    """src (mix) -> dst (chi giong, mono, `sr` Hz, PCM16). Tra ve dict thong tin."""
    _set_cache()
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    t0 = time.time()
    dev = _pick_device(device)
    model = get_model(model_name)
    model.eval()
    wav = _load(src)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std().clamp_min(1e-8)
    with torch.inference_mode():
        out = apply_model(model, ((wav - mean) / std)[None], device=dev, shifts=shifts,
                          split=True, overlap=0.25, progress=False)[0]
    out = out * std + mean                                          # (sources, 2, T)
    vi = model.sources.index("vocals")
    vocals = out[vi]
    music = out.sum(0) - vocals
    e_v = float((vocals ** 2).mean()) + 1e-12
    e_m = float((music ** 2).mean()) + 1e-12
    v = vocals.mean(0)
    if sr and sr != 44100:
        import torchaudio.functional as AF
        v = AF.resample(v[None], 44100, int(sr))[0]
    y = v.cpu().numpy().astype("float32")
    peak = float(np.abs(y).max()) if len(y) else 0.0
    if peak > 0.99:                     # chong vo tieng khi ghi PCM16
        y *= 0.99 / peak
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), y, int(sr or 44100), subtype="PCM_16")
    return {"ok": True, "music_db": round(10 * np.log10(e_m / e_v), 1), "device": dev,
            "model": model_name, "seconds": round(time.time() - t0, 1),
            "duration": round(len(y) / float(sr or 44100), 2)}


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=False)
    ap.add_argument("--out", dest="dst", required=False)
    ap.add_argument("--sr", type=int, default=24000)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--shifts", type=int, default=1)
    ap.add_argument("--check", action="store_true", help="chi kiem tra da cai thu vien chua")
    ap.add_argument("--install", action="store_true", help="cai thu vien (--no-deps) neu chua co")
    ap.add_argument("--prefetch", action="store_true", help="cai thu vien + tai san model (1 lan)")
    a = ap.parse_args(argv)
    if a.check or a.install or a.prefetch:
        ok = _importable()
        if not ok and (a.install or a.prefetch):
            ok = install()
        if ok and a.prefetch:
            _set_cache()
            for endpoint in (None, "https://hf-mirror.com"):        # mang chan HF -> mirror
                if endpoint:
                    os.environ["HF_ENDPOINT"] = endpoint
                try:
                    from demucs.pretrained import get_model
                    get_model(a.model)
                    break
                except Exception as e:  # noqa: BLE001
                    print("[!] tai model tach nhac loi: %s" % e, flush=True)
            else:
                ok = False
        print(json.dumps({"ok": ok}))
        return 0 if ok else 1
    try:
        info = separate(a.src, a.dst, sr=a.sr, device=a.device, model_name=a.model, shifts=a.shifts)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}))
        return 1
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
