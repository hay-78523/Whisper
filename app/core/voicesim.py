# -*- coding: utf-8 -*-
"""Cham diem GIONG GIONG (speaker similarity) giua 2 doan audio.

Dung bo nhan dien giong cua Chatterbox (models_chatterbox/ve.safetensors, 5.7MB,
DA KEM SAN trong goi) — LSTM 3 lop tren mel 40 bang -> vector 256 chieu.
Hai doan cung 1 nguoi noi -> vector gan nhau (cosine cao).

Chay duoc 2 kieu:
  * import trong tien trinh co torch:   embed(path) / similarity(a, b)
  * tien trinh rieng (khong lam nang server):
        python voicesim.py --ve <ve.safetensors> --ref a.wav [b.wav ...] --cand c.wav [d.wav ...]
        python voicesim.py --ve ... --ref src.wav --windows "t0,t1;t0,t1;..."
    -> in JSON {"sims": [...]} (do giong cua tung cand/window so voi tam giong cua ref)

Doc lap voi package app (chi can torch + numpy + soundfile/librosa) de chay bang
Python engine (python\\ trong ban portable) ma khong can cai them gi.
"""
import json
import sys
from pathlib import Path

SR = 16000
N_FFT, HOP, WIN, N_MELS = 400, 160, 400, 40
PARTIAL = 160                 # 1.6 giay / khuc
STEP = 80                     # khuc chong nhau 50%

_MODEL = {}


def _find_ve():
    root = Path(__file__).resolve().parents[2]
    for d in (root / "models_chatterbox", root / "data" / "chatterbox"):
        if (d / "ve.safetensors").exists():
            return d / "ve.safetensors"
    return None


def available():
    return _find_ve() is not None


def _load_model(ve_path=None):
    import torch
    from torch import nn
    key = str(ve_path or _find_ve())
    if key in _MODEL:
        return _MODEL[key]
    if key == "None":
        raise RuntimeError("Thieu models_chatterbox/ve.safetensors")
    from safetensors.torch import load_file

    class VE(nn.Module):
        def __init__(self):
            nn.Module.__init__(self)
            self.lstm = nn.LSTM(N_MELS, 256, num_layers=3, batch_first=True)
            self.proj = nn.Linear(256, 256)
            self.similarity_weight = nn.Parameter(torch.tensor([10.0]))
            self.similarity_bias = nn.Parameter(torch.tensor([-5.0]))

        def forward(self, mels):                      # (B, T, 40)
            _, (hidden, _) = self.lstm(mels)
            raw = torch.relu(self.proj(hidden[-1]))
            return raw / torch.linalg.norm(raw, dim=1, keepdim=True).clamp_min(1e-8)

    m = VE()
    m.load_state_dict(load_file(key))
    m.eval()
    _MODEL[key] = m
    return m


def _load_wav(path, t0=None, t1=None):
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if t0 is not None:
        y = y[int(t0 * sr):int(t1 * sr) if t1 else None]
    if sr != SR:
        import librosa
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    return np.asarray(y, dtype="float32")


def _trim_silence(y):
    """Bo khung lang (< -40dB so voi dinh) de chi cham phan CO TIENG NOI."""
    import numpy as np
    if len(y) < SR // 2:
        return y
    fr = 400
    n = len(y) // fr
    rms = np.sqrt(np.mean(y[:n * fr].reshape(n, fr) ** 2, axis=1) + 1e-10)
    db = 20 * np.log10(rms)
    keep = db >= db.max() - 40
    out = y[:n * fr].reshape(n, fr)[keep].reshape(-1)
    return out if len(out) >= SR // 2 else y


def _mel(y):
    """Mel 40 bang (bien do mu 2, khong log) — dung kieu Chatterbox VoiceEncoder."""
    import numpy as np
    import librosa
    spec = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP, win_length=WIN,
                               center=True, pad_mode="reflect")) ** 2
    basis = librosa.filters.mel(sr=SR, n_fft=N_FFT, n_mels=N_MELS, fmin=0, fmax=8000)
    return (basis @ spec).T.astype("float32")          # (T, 40)


def embed_array(y, ve_path=None):
    """Audio 16kHz -> vector giong 256 chieu (da chuan hoa)."""
    import numpy as np
    import torch
    mel = _mel(_trim_silence(y))
    if len(mel) < PARTIAL:
        mel = np.pad(mel, ((0, PARTIAL - len(mel)), (0, 0)))
    starts = list(range(0, max(1, len(mel) - PARTIAL + 1), STEP))
    parts = np.stack([mel[s:s + PARTIAL] for s in starts])
    with torch.inference_mode():
        e = _load_model(ve_path)(torch.from_numpy(parts)).mean(0)
    e = e / torch.linalg.norm(e).clamp_min(1e-8)
    return e.numpy()


def embed(path, ve_path=None, t0=None, t1=None):
    return embed_array(_load_wav(path, t0, t1), ve_path)


def centroid(paths, ve_path=None):
    """Tam giong cua nhieu doan (vd ca file nguon dai) — dai dien 'giong that' cua nguoi do."""
    import numpy as np
    es = np.stack([embed(p, ve_path) for p in paths])
    c = es.mean(0)
    return c / max(1e-8, float(np.linalg.norm(c)))


def similarity(a, b, ve_path=None):
    """Do giong giong 2 file (cosine, 0..1)."""
    import numpy as np
    return float(np.dot(embed(a, ve_path), embed(b, ve_path)))


def _main(argv):
    import argparse
    import numpy as np
    ap = argparse.ArgumentParser()
    ap.add_argument("--ve", default=None)
    ap.add_argument("--ref", nargs="+", required=True)
    ap.add_argument("--cand", nargs="*", default=[])
    ap.add_argument("--windows", default="")
    a = ap.parse_args(argv)
    c = centroid(a.ref, a.ve)
    sims = [float(np.dot(c, embed(p, a.ve))) for p in a.cand]
    if a.windows:
        y = _load_wav(a.ref[0])
        for w in a.windows.split(";"):
            t0, t1 = (float(x) for x in w.split(","))
            sims.append(float(np.dot(c, embed_array(y[int(t0 * SR):int(t1 * SR)], a.ve))))
    print(json.dumps({"sims": [round(s, 4) for s in sims]}))


if __name__ == "__main__":
    _main(sys.argv[1:])
