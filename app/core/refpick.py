"""Chon doan mau (ref) tot nhat tu file giong DAI (30 giay - vai phut).

F5-TTS bat chuoc gan nhu y het doan mau 6-11 giay: nhip, cao do, cam xuc. Vi vay:
  * mau chinh  (ref.wav)            : doan cat DUNG ranh gioi cau, nhip giong vua
                                       phai, cao do quen thuoc cua nguoi noi;
  * mau hao hung (styles/hype)      : doan nguoi do noi soi noi / len giong nhieu nhat;
  * mau tram   (styles/calm)        : doan nguoi do noi nhe, tram, it len xuong nhat.
The cam xuc ([vui], [hao hung]... / [buon], [diu dang]...) se doc bang mau tuong ung
-> cam xuc den tu CHINH giong do, khong phai bop meo am thanh sau khi doc.
"""
import re

SR = 24000


def _load(path):
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if sr != SR:
        import librosa
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    return np.asarray(y, dtype="float32")


def _f0_track(y):
    """Cao do (Hz) va nang luong giong (dB) theo khung 10ms (NaN = khong co tieng)."""
    import numpy as np
    import librosa
    y16 = librosa.resample(y, orig_sr=SR, target_sr=16000)
    hop = 160
    f0 = librosa.yin(y16, fmin=65, fmax=400, sr=16000, frame_length=1024, hop_length=hop)
    rms = librosa.feature.rms(y=y16, frame_length=1024, hop_length=hop)[0]
    n = min(len(f0), len(rms))
    f0, rms = f0[:n], rms[:n]
    db = (20 * np.log10(rms + 1e-6)).astype("float64")
    f0 = f0.astype("float64")
    voiced = db >= (db.max() - 30)
    f0[~voiced] = np.nan
    db[~voiced] = np.nan
    return f0, db                                 # 100 khung / giay


def _clean_win_text(text):
    """Chuyen so ngan (nam, %, so dem <= 4 chu so) va tu vay muon sang chu tieng Viet chuan."""
    if re.search(r"\d{5,}", text or ""):
        return ""
    try:
        from . import clone, expressive
        if clone._is_vietnamese(text):
            text = expressive.normalize_numbers_vi(text)
        elif re.search(r"\d", text or ""):
            text = expressive.normalize_numbers_en(text)
    except Exception:
        if re.search(r"\d", text or ""):
            return ""
    return "" if re.search(r"\d", text or "") else (text or "").strip()


def _join_words_natural(ws):
    """Ghep cac tu cua Whisper thanh cau: neu Whisper dat dau cham '.' nhung khoang
    ngat thuc te trong audio < 0.28s (hoac cum <= 4 tu ngat < 0.36s) thi doi thanh
    dau phay ',' de F5-TTS hoc dung ngu dieu nhan nha giua cac ve cau."""
    parts = []
    clause_words = 0
    for k, w in enumerate(ws):
        tok = w.get("text") or ""
        clause_words += 1
        if k < len(ws) - 1:
            s = tok.rstrip()
            if s.endswith(".") and not s.endswith("..."):
                gap = float(ws[k + 1]["start"]) - float(w["end"])
                if gap < 0.28 or (gap < 0.36 and clause_words <= 4):
                    tok = s[:-1] + "," + tok[len(s):]
                    clause_words = 0
                else:
                    clause_words = 0
            elif s[-1:] in "?!,;:…":
                clause_words = 0
        parts.append(tok)
    return "".join(parts).strip()


def _windows(words, dur_total, min_sec=4.8, max_sec=10.5, min_conf=0.52):
    """Cac doan bat dau dau cau, ket thuc cuoi cau (co khoang ngat thuc >= 0.22s), dai min..max giay."""
    W = [w for w in words if (w.get("text") or "").strip()]
    if not W:
        return []
    ends = {
        i for i, w in enumerate(W)
        if i == len(W) - 1 or (
            w["text"].strip()[-1:] in ".?!…"
            and float(W[i + 1]["start"]) - float(w["end"]) >= 0.22
        )
    }
    if len(ends) < 2:                              # Whisper khong cham cau -> dung cho ngat > 0.30s
        ends = {i for i in range(len(W) - 1) if W[i + 1]["start"] - W[i]["end"] > 0.30}
        ends.add(len(W) - 1)
    starts = [0] + sorted(i + 1 for i in ends if i + 1 < len(W))
    out = []
    for si in starts:
        for ei in sorted(e for e in ends if e >= si):
            t0 = max(0.0, W[si]["start"] - 0.06)
            t1 = min(dur_total, W[ei]["end"] + 0.12)
            d = t1 - t0
            if d > max_sec:
                break
            if d < min_sec:
                continue
            ws = W[si:ei + 1]
            gaps = [ws[k + 1]["start"] - ws[k]["end"] for k in range(len(ws) - 1)]
            if gaps and max(gaps) > 0.95:         # ngat qua lau giua doan
                continue
            probs = [float(w.get("p", 1.0)) for w in ws]
            min_p = min(probs) if probs else 1.0
            avg_p = (sum(probs) / len(probs)) if probs else 1.0
            if min_p < min_conf:                  # co tu nghe khong ro / lan nhac nen manh
                continue
            raw_text = _join_words_natural(ws)
            text = _clean_win_text(raw_text)
            if not text:
                continue
            n_words = len(re.findall(r"\w+", text))
            speech_dur = sum(max(0.04, w["end"] - w["start"]) for w in ws)
            max_wdur = max((float(w["end"]) - float(w["start"])) for w in ws) if ws else 0.3
            out.append({
                "t0": t0, "t1": t1, "dur": d, "text": text,
                "wpm": n_words / d * 60.0,
                "speech_wpm": n_words / max(1.0, speech_dur) * 60.0,
                "avg_p": avg_p, "min_p": min_p, "max_wdur": max_wdur,
                "words": [{"start": float(w["start"]), "end": float(w["end"]), "text": w["text"]} for w in ws],
            })
    return out


def pick(src_path, words, want_styles=True):
    """-> {'main': win, 'hype': win|None, 'calm': win|None, 'y': audio}  (None neu khong du)."""
    import numpy as np
    y = _load(src_path)
    total = len(y) / SR
    wins = _windows(words, total, min_sec=4.8, max_sec=10.5, min_conf=0.52)
    if not wins:
        wins = _windows(words, total, min_sec=4.5, max_sec=11.5, min_conf=0.35)
    if not wins and total >= 4.0:
        wins = _windows(words, total, min_sec=4.0, max_sec=12.0, min_conf=0.30)
    if not wins:
        return None
    f0, db = _f0_track(y)
    v_all = f0[~np.isnan(f0)]
    db_all = db[~np.isnan(db)]
    if len(v_all) < 80:
        return None
    med_all = float(np.median(v_all))
    db_med = float(np.median(db_all)) if len(db_all) else -20.0
    # bo khung nhay quang tam (loi bat cao do), giu trong +-10 cung quanh giong thuong
    with np.errstate(invalid="ignore"):
        f0[np.abs(12 * np.log2(f0 / med_all)) > 10] = np.nan
    wpm_med = float(np.median([w["wpm"] for w in wins]))
    for w in wins:
        i0, i1 = int(w["t0"] * 100), int(w["t1"] * 100)
        seg = f0[i0:i1]
        seg_db = db[i0:i1]
        v = seg[~np.isnan(seg)]
        v_db = seg_db[~np.isnan(seg_db)]
        if len(v) < 50:
            w["ok"] = False
            continue
        st = 12 * np.log2(v / med_all)
        w["ok"] = True
        w["f0"] = float(np.median(st))                                   # cao/thap hon giong thuong (cung)
        w["rng"] = float(np.percentile(st, 90) - np.percentile(st, 10))  # do bien thien ngu dieu
        w["db"] = float(np.median(v_db) - db_med) if len(v_db) else 0.0  # do manh/nhe cua giong (dB)
        # Do SNR (ty le giong noi / nhac nen trong khoang nghi) de uu tien doan sach nhac nen nhat
        a_s, b_s = int(w["t0"] * SR), int(w["t1"] * SR)
        c_seg = y[a_s:b_s]
        if len(c_seg) > SR:
            mask = np.zeros(len(c_seg), dtype=bool)
            for wd in w.get("words") or []:
                m0 = max(0, int((wd["start"] - w["t0"] - 0.02) * SR))
                m1 = min(len(c_seg), int((wd["end"] - w["t0"] + 0.03) * SR))
                if m1 > m0:
                    mask[m0:m1] = True
            ns = c_seg[~mask]
            sp = c_seg[mask]
            if len(ns) > int(0.15 * SR) and len(sp) > int(0.5 * SR):
                rms_sp = float(np.sqrt(np.mean(sp ** 2)) + 1e-6)
                rms_ns = float(np.sqrt(np.mean(ns ** 2)) + 1e-6)
                w["snr"] = float(20.0 * np.log10(rms_sp / rms_ns))
            else:
                w["snr"] = 10.0
        else:
            w["snr"] = 10.0
    # giu cac doan cung 1 nguoi noi (tranh video co 2 nguoi khac tong giong xen vao)
    wins = [w for w in wins if w.get("ok") and abs(w["f0"]) <= 4.5]
    if not wins:
        return None

    def main_score(w):
        # Uu tien doan SACH NHAC NEN nhat (snr cao), do chinh xac phien am cao (avg_p, min_p),
        # khong bi dinh hieu ung am thanh/tieng trong dau tu (max_wdur <= 0.39s), ngu dieu tu nhien.
        conf_bonus = (w.get("avg_p", 0.9) - 0.90) * 32.0 + (w.get("min_p", 0.5) - 0.70) * 7.0
        snr_bonus = 0.20 * min(20.0, max(0.0, w.get("snr", 10.0) - 4.0))
        sfx_pen = max(0.0, w.get("max_wdur", 0.30) - 0.39) * 15.0
        return (conf_bonus
                + snr_bonus
                - sfx_pen
                - abs(w["wpm"] - wpm_med) / 28.0
                - abs(w["f0"]) * 0.65
                + 0.08 * max(-2.0, min(2.5, w["db"]))
                + 0.34 * min(9.5, w["rng"])
                - abs(w["dur"] - 6.8) * 0.12)

    ranked = sorted(wins, key=main_score, reverse=True)
    main = ranked[0]
    # cac doan ung vien tot nhat (khong chong nhau) — clone.py cham "giong giong" de chon lai
    cands = []
    for w in ranked:
        if all(w["t1"] <= c["t0"] or w["t0"] >= c["t1"] for c in cands):
            w["score"] = round(main_score(w), 3)
            cands.append(w)
        if len(cands) >= 5:
            break
    res = {"main": main, "hype": None, "calm": None, "y": y, "cands": cands}
    if want_styles and total >= 18 and len(wins) >= 3:
        def far(w):                                  # khong trung doan voi mau chinh
            return w["t1"] <= main["t0"] or w["t0"] >= main["t1"]
        # Chi chon mau cam xuc (hype/calm) khi co do chinh xac phien am rat cao (avg_p >= 0.96, min_p >= 0.80)
        # va cung sach nhac nen (snr >= main_snr - 4dB) de tranh doi sang doan lan nhac nen.
        pool = [w for w in wins if far(w)
                and w.get("avg_p", 0.0) >= 0.96 and w.get("min_p", 0.0) >= 0.80
                and w.get("snr", 0.0) >= main.get("snr", 10.0) - 4.0
                and abs(w["wpm"] - main["wpm"]) <= main["wpm"] * 0.18]
        if pool:
            def emo_e(w):
                return w["f0"] + 0.45 * min(9.5, w["rng"]) + 0.35 * w["db"]
            h = max(pool, key=emo_e)
            if emo_e(h) >= emo_e(main) + 0.65:
                res["hype"] = h
            c = min([w for w in pool if w is not res["hype"]], key=emo_e, default=None)
            if c and emo_e(c) <= emo_e(main) - 0.55:
                res["calm"] = c
    return res


def _clean_bgm_with_words(c, t0, words):
    """Dung timestamp tung tu cua Whisper de:
    1) Uoc luong pho nhac nen tu chinh cac khoang nghi giua tu va khu nhe (Wiener floor=0.26)
       ma khong lam mat phu am dau/cuoi;
    2) Ha am luong nhac nen trong cac khoang nghi giua cau (-30dB, ramp cosin 30ms) de F5-TTS
       khong hoc tieng trong/nhac nen o dau cham/dau phay."""
    import numpy as np
    if not words or len(c) < SR:
        return c
    mask = np.zeros(len(c), dtype="float32")
    pad_in = int(0.035 * SR)
    pad_out = int(0.055 * SR)
    for w in words:
        a = max(0, int((float(w["start"]) - t0) * SR) - pad_in)
        b = min(len(c), int((float(w["end"]) - t0) * SR) + pad_out)
        if b > a:
            mask[a:b] = 1.0
    win_len = int(0.032 * SR) | 1
    hann = np.hanning(win_len).astype("float32")
    hann /= max(1e-6, float(hann.sum()))
    smooth = np.convolve(mask, hann, mode="same")

    non_speech = c[smooth < 0.12]
    if len(non_speech) > int(0.20 * SR):
        rms_ns = float(np.sqrt(np.mean(non_speech ** 2)))
        rms_sp = float(np.sqrt(np.mean(c[smooth > 0.5] ** 2))) if np.any(smooth > 0.5) else 0.1
        # Neu khoang nghi co nhac nen ro (> -38dB so voi giong noi) -> khu pho nhac nen nhe
        if rms_ns > max(0.004, rms_sp * 0.04):
            try:
                import librosa
                S = librosa.stft(c, n_fft=1024, hop_length=256, win_length=1024)
                mag, phase = np.abs(S), np.angle(S)
                S_ns = np.abs(librosa.stft(non_speech, n_fft=1024, hop_length=256, win_length=1024))
                nf = np.median(S_ns, axis=1, keepdims=True)
                gain_s = np.clip((mag ** 2 - 1.05 * (nf ** 2)) / (mag ** 2 + 1e-8), 0.26, 1.0) ** 0.5
                c = librosa.istft(mag * gain_s * np.exp(1j * phase),
                                  hop_length=256, win_length=1024, length=len(c)).astype("float32")
            except Exception:
                pass
    # Gate khoang nghi giua cac tu/cau xuong -30dB (0.03) de ngat cau sach se
    env = 0.03 + 0.97 * smooth
    return (c * env).astype("float32")


def write_clip(y, win, out_wav, gain):
    import numpy as np
    import soundfile as sf
    a, b = int(win["t0"] * SR), int(win["t1"] * SR)
    c = y[a:b].copy()
    if win.get("words"):
        c = _clean_bgm_with_words(c, float(win["t0"]), win["words"])
    peak = float(np.abs(c).max())
    if peak > 1e-4:
        c = c * (0.89 / peak)
    else:
        c = c * gain
    f = int(0.015 * SR)
    if len(c) > 2 * f:
        c[:f] *= np.linspace(0, 1, f, dtype="float32")
        c[-f:] *= np.linspace(1, 0, f, dtype="float32")
    c = np.clip(c, -0.99, 0.99)
    sf.write(str(out_wav), c, SR, subtype="PCM_16")
