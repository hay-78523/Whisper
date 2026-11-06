# -*- coding: utf-8 -*-
"""TEST TAT CA GIONG CLONE — chay tren may khach (GPU nhanh: ~5-15 giay/giong).

Moi giong doc cung 1 doan van (co so, cau hoi, cau cam than) -> luu vao thu
muc test_voices\\, roi Whisper nghe lai va cham diem:
  * dung chu (%)      : so tu doc dung so voi van ban
  * ngat dai          : so khoang lang > 0.7 giay
  * tu/phut           : toc do doc
Ket qua: test_voices\\BAO_CAO.csv (mo bang Excel) + in ra man hinh.
    python\\python.exe test_voices.py            (tat ca giong)
    python\\python.exe test_voices.py ray adam   (chi vai giong)
"""
import csv
import difflib
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HOME", str(ROOT / "data" / "hf_cache"))

from app.core import clone, engine, tts                    # noqa: E402
from app.core.expressive import normalize_numbers_en      # noqa: E402

TEXT = ("Two co-workers get the same raise on the same day. $10,000 a year. "
        "Three years later, one of them is about $22,000 ahead, and the other one owes money. "
        "Same job, same raise, same city. So where does a gap that big come from? "
        "Honestly, it starts with one small decision!")
OUT = ROOT / "test_voices"


def words(t):
    return re.findall(r"[a-z']+", normalize_numbers_en(t).lower().replace("-", " "))


def long_pauses(wav_bytes):
    sr, _ch, a = clone._wav_read(wav_bytes)
    peak = max(max(a), -min(a)) or 1
    thr = max(int(0.003 * 32768), int(peak * 0.012))
    win = sr // 100
    run, n = 0, 0
    for k in range(len(a) // win):
        if max(abs(x) for x in a[k * win:(k + 1) * win]) < thr:
            run += 1
        else:
            if run * 0.01 > 0.7:
                n += 1
            run = 0
    return n, len(a) / sr


def main():
    OUT.mkdir(exist_ok=True)
    names = sys.argv[1:] or clone.list_profiles()
    model = engine.get_model("small")
    ref = words(TEXT)
    rows = []
    print("Test %d giong..." % len(names), flush=True)
    for name in names:
        t0 = time.time()
        try:
            data, _mime, _ext = tts.synth(TEXT, "c:" + name, "+0%")
        except Exception as e:
            print("  %-22s LOI: %s" % (name, e), flush=True)
            rows.append({"giong": name, "loi": str(e)[:200]})
            continue
        p = OUT / ("%s.wav" % name)
        p.write_bytes(data)
        heard = engine.transcribe(model, p).get("text") or ""
        match = difflib.SequenceMatcher(None, ref, words(heard)).ratio()
        npause, dur = long_pauses(data)
        wpm = len(ref) / dur * 60 if dur else 0
        ok = match >= 0.95 and npause == 0
        row = {"giong": name, "dung_chu_%": round(match * 100, 1), "ngat_dai": npause,
               "tu_phut": round(wpm), "giay_tao": round(time.time() - t0, 1),
               "danh_gia": "OK" if ok else "CAN NGHE LAI", "whisper_nghe": heard.strip()[:300]}
        rows.append(row)
        print("  %-22s dung chu %5.1f%%  ngat dai %d  %3d tu/phut  -> %s" % (
            name, row["dung_chu_%"], npause, wpm, row["danh_gia"]), flush=True)
    keys = ["giong", "dung_chu_%", "ngat_dai", "tu_phut", "giay_tao", "danh_gia", "whisper_nghe", "loi"]
    with open(OUT / "BAO_CAO.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    bad = [r["giong"] for r in rows if r.get("danh_gia") != "OK"]
    print("\nXONG: %d/%d giong OK. Can nghe lai: %s" % (len(rows) - len(bad), len(rows), ", ".join(bad) or "khong co"))
    print("Bao cao: %s" % (OUT / "BAO_CAO.csv"))
    clone.stop_worker()


if __name__ == "__main__":
    main()
