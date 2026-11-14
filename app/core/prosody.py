# -*- coding: utf-8 -*-
"""Cham NHIP DIEU giong doc (doc lap ngon ngu, chi can numpy + soundfile/librosa).

Giong nguoi that: cau co HINH VONG CUNG — mo nhe, don len 1-2 cho nhan ro, ngat theo cum y;
cac am tiet to nho KHAC NHAU. Giong may: chu nao cung nhan ngang nhau, cach deu.
Chi so (tren phan co tieng noi):
  peak_var : do lech chuan do to cac DINH AM TIET (dB) — thap = chu nao cung nhan nhu nhau
  arc      : chenh giua dinh to nhat va dinh trung vi trong tung cum (dB) — co cho "don nhan"
  pauses   : so khoang ngat >= 150ms / 10 giay — ngat theo cum y
  pitch    : bien do len xuong giong p10-p90 (cung)
  score    : 0..100 tong hop "song dong" (de so sanh ban nay voi ban kia CUNG 1 giong)
"""
import io

import numpy as np

SR = 16000
HOP = 160                      # 10 ms


def _load(src):
    import soundfile as sf
    if isinstance(src, (bytes, bytearray)):
        y, sr = sf.read(io.BytesIO(src), dtype="float32", always_2d=False)
    else:
        y, sr = sf.read(str(src), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if sr != SR:
        import librosa
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    return np.asarray(y, dtype=np.float32)


def measure(src, with_pitch=True):
    """src: duong dan / bytes wav -> dict chi so nhip dieu (None neu qua ngan / im lang)."""
    y = _load(src)
    if len(y) < SR * 1.0:
        return None
    n = len(y) // HOP
    fr = y[:n * HOP].reshape(n, HOP)
    db = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-6)
    top = float(np.percentile(db, 97))
    voiced = db > top - 35
    if voiced.sum() < 50:
        return None
    # duong bao do to muot 50 ms -> dinh am tiet (cach nhau >= 120 ms, noi >= 3 dB)
    k = 5
    env = np.convolve(db, np.ones(k) / k, mode="same")
    peaks = []
    last = -999
    for i in range(1, n - 1):
        if env[i] >= env[i - 1] and env[i] > env[i + 1] and env[i] > top - 25:
            lo = env[max(0, i - 15):i + 16].min()
            if env[i] - lo >= 3.0:
                if i - last >= 12:
                    peaks.append(i)
                    last = i
                elif env[i] > env[last]:
                    peaks[-1] = i
                    last = i
    pv = env[peaks] if peaks else np.array([top])
    peak_var = float(np.std(pv))
    # cum = doan giua cac khoang ngat >= 150 ms; trong moi cum: dinh lon nhat - trung vi
    quiet = db < top - 30
    pk_set = set(peaks)
    pauses, groups, cur, run = 0, [], [], 0
    for i in range(n):
        if quiet[i]:
            run += 1
            if run == 15:                       # khoang ngat >= 150 ms -> het 1 cum
                pauses += 1
                if cur:
                    groups.append(cur)
                    cur = []
        else:
            run = 0
            if i in pk_set:
                cur.append(env[i])
    if cur:
        groups.append(cur)
    arcs = [max(g) - float(np.median(g)) for g in groups if len(g) >= 3]
    arc = float(np.mean(arcs)) if arcs else 0.0
    dur = len(y) / SR
    out = {"peak_var": round(peak_var, 2), "arc": round(arc, 2),
           "pauses": round(pauses / dur * 10, 2), "pitch": None}
    if with_pitch:
        try:
            import librosa
            f0 = librosa.yin(y, fmin=70, fmax=400, sr=SR, frame_length=1024, hop_length=HOP)
            f = f0[:n][voiced[:len(f0)]]
            st = 12 * np.log2(f / np.median(f))
            st = st[np.abs(st) <= 9]
            out["pitch"] = round(float(np.percentile(st, 90) - np.percentile(st, 10)), 2)
        except Exception:
            pass
    # tong hop 0..100: nhan khong deu + co cho don nhan + co ngat cum + len xuong giong
    s = (min(1.0, peak_var / 6.0) * 35 + min(1.0, arc / 8.0) * 30
         + min(1.0, out["pauses"] / 6.0) * 15 + min(1.0, (out["pitch"] or 0) / 9.0) * 20)
    out["score"] = round(s, 1)
    return out


def closeness(a, b):
    """Muc GIONG NHIP DIEU 2 ban (0..1): ban clone song dong gan bang giong goc -> gan 1."""
    if not a or not b:
        return None
    parts = []
    for k, scale in (("peak_var", 4.0), ("arc", 5.0), ("pauses", 4.0), ("pitch", 5.0)):
        if a.get(k) is None or b.get(k) is None:
            continue
        parts.append(max(0.0, 1.0 - abs(a[k] - b[k]) / scale))
    return round(sum(parts) / len(parts), 3) if parts else None
