# -*- coding: utf-8 -*-
"""KIEM TRA CAM XUC + DO GIONG cua giong clone — chay tren may GPU, gui BAO_CAO.csv cho ky thuat.

Moi giong doc CUNG 1 cau voi 6 the: [binh thuong] [vui] [ngac nhien] [gian du] [buon] [thi tham]
(cung cau -> khac nhau chi do cam xuc). Do tren tung ban doc:
  * giong_giong  : % giong giong goc (muc tieu >= 80%)
  * cao_do (Hz), len_xuong (cung), do_manh (dB), thoi_luong (giay)
  * cam_xuc_ro   : % so phep thu cam xuc DUNG HUONG so voi ban [binh thuong]
                   (vui/gian du cao + manh hon; buon cham + nhe hon; thi tham nhe han...)
Ket qua: kiem_tra_cam_xuc\\BAO_CAO.csv + file wav tung cam xuc de nghe.
    python\\python.exe kiem_tra_cam_xuc.py              (moi giong)
    python\\python.exe kiem_tra_cam_xuc.py adam ray     (vai giong)
"""
import csv
import io
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HOME", str(ROOT / "data" / "hf_cache"))

from app.core import clone, expressive                     # noqa: E402

OUT = ROOT / "kiem_tra_cam_xuc"
EMOS = ["bình thường", "vui", "ngạc nhiên", "giận dữ", "buồn", "thì thầm"]
TEXT = {"en": "I can't believe this is really happening right now, after all this time.",
        "vi": "Tôi không thể tin chuyện này thật sự đang xảy ra, sau ngần ấy thời gian."}
# (cam xuc, chi so, huong so voi ban thuong): +1 = phai lon hon, -1 = phai nho hon
CHECKS = [("vui", "f0", 1), ("vui", "db", 1), ("giận dữ", "db", 1), ("giận dữ", "f0", 1),
          ("buồn", "db", -1), ("buồn", "dur", 1), ("thì thầm", "db", -1), ("buồn", "rng", -1),
          ("ngạc nhiên", "f0", 1), ("ngạc nhiên", "rng", 1)]


def prosody(wav):
    import numpy as np
    import librosa
    import soundfile as sf
    y, sr = sf.read(io.BytesIO(wav), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    y16 = librosa.resample(y, orig_sr=sr, target_sr=16000)
    f0 = librosa.yin(y16, fmin=60, fmax=450, sr=16000, frame_length=1024, hop_length=160)
    rms = librosa.feature.rms(y=y16, frame_length=1024, hop_length=160)[0]
    n = min(len(f0), len(rms))
    f0, rms = f0[:n], rms[:n]
    db = 20 * np.log10(rms + 1e-6)
    voiced = db >= db.max() - 30
    f = f0[voiced]
    st = 12 * np.log2(f / np.median(f))
    st = st[np.abs(st) <= 7]                       # bo khung bat nham quang tam
    return {"f0": float(np.median(f)), "rng": float(np.percentile(st, 80) - np.percentile(st, 20)),
            "db": float(np.mean(db[voiced])), "dur": len(y) / float(sr)}


def main(argv):
    want = [clone.safe_name(a) for a in argv if not a.startswith("--")]
    names = want or [n for n in clone.list_profiles(include_all_en=True) if not clone.is_pending(n)]
    OUT.mkdir(exist_ok=True)
    rows = []
    print("=== KIEM TRA CAM XUC %d giong | engine Anh: %s ===" % (len(names), clone.engine_for(True)))
    for i, n in enumerate(names):
        lang = clone.profile_lang(n)
        t0 = time.time()
        row = {"giong": n, "ngon_ngu": lang, "engine": clone.engine_for(lang == "en"),
               "mau_cam_xuc": ",".join(sorted({"hype", "calm"} & clone.available_styles(n))) or "khong"}
        pro, sims = {}, []
        for e in EMOS:
            try:
                wav, _m, _x = expressive.synth_clone("[%s] %s" % (e, TEXT[lang]), "c:" + n)
                f = OUT / ("%s_%s.wav" % (n, expressive._norm_tag(e).replace(" ", "_")))
                f.write_bytes(wav)
                pro[e] = prosody(wav)
                got = clone._voicesim(clone._sim_refs(n), [f])
                if got:
                    sims.append(got[0])
                    row["giong_" + expressive._norm_tag(e).replace(" ", "_")] = "%d%%" % round(got[0] * 100)
            except Exception as ex:
                row["loi"] = (row.get("loi", "") + " %s: %s" % (e, ex))[:300]
        base = pro.get("bình thường")
        if base:
            ok = sum(1 for e, k, sgn in CHECKS if e in pro and (pro[e][k] - base[k]) * sgn > 0)
            row["cam_xuc_ro"] = "%d%%" % round(100.0 * ok / len(CHECKS))
            for e in EMOS:
                if e in pro:
                    p = pro[e]
                    row["chi_so_" + expressive._norm_tag(e).replace(" ", "_")] = "%dHz %.1fst %.1fdB %.1fs" % (
                        p["f0"], p["rng"], p["db"], p["dur"])
        if sims:
            row["giong_giong_tb"] = "%d%%" % round(100 * sum(sims) / len(sims))
            row["dat_80"] = "DAT" if min(sims) >= 0.80 else "CHUA DAT"
        row["giay"] = round(time.time() - t0)
        print("[%d/%d] %-20s giong giong %s | cam xuc ro %s | mau cam xuc: %s"
              % (i + 1, len(names), n, row.get("giong_giong_tb", "?"), row.get("cam_xuc_ro", "?"), row["mau_cam_xuc"]),
              flush=True)
        rows.append(row)
    cols = ["giong", "ngon_ngu", "engine", "mau_cam_xuc", "giong_giong_tb", "dat_80", "cam_xuc_ro"] + \
           ["giong_" + expressive._norm_tag(e).replace(" ", "_") for e in EMOS] + \
           ["chi_so_" + expressive._norm_tag(e).replace(" ", "_") for e in EMOS] + ["giay", "loi"]
    with open(OUT / "BAO_CAO.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print("\n=== XONG: %d/%d giong dat >= 80%% giong goc. Bao cao: %s ==="
          % (sum(r.get("dat_80") == "DAT" for r in rows), len(rows), OUT / "BAO_CAO.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
