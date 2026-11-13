# -*- coding: utf-8 -*-
"""TAO MAU CAM XUC + CHAM DO GIONG cho cac giong clone tieng Anh — chay tren may GPU.

Voi moi giong Anh CHUA co mau cam xuc (styles/hype, styles/calm):
  * Chatterbox doc 1 doan hao hung + 1 doan tram bang CHINH giong do (moi mau 3 lan),
    giu ban giong giong goc nhat, CHI NHAN khi giong >= 85% (duoi thi bo, giu giong goc);
  * doc 3 cau thu: thuong / [vui] / [buon] -> mau_cam_xuc\\<giong>_*.wav de nghe;
  * cham "giong giong" ban clone thuong so voi giong goc (muc tieu >= 80%).
Ket qua: mau_cam_xuc\\BAO_CAO.csv (mo bang Excel) + in ra man hinh.
    python\\python.exe tao_mau_cam_xuc.py            (moi giong Anh con thieu mau)
    python\\python.exe tao_mau_cam_xuc.py ray adam   (chi vai giong; co the tao lai: --force)
"""
import csv
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HOME", str(ROOT / "data" / "hf_cache"))

from app.core import clone, expressive                     # noqa: E402

OUT = ROOT / "mau_cam_xuc"
DEMO = {
    "thuong": "[normal] Here is a quick update on what is happening this week.",
    "vui": "[happy] We did it! The whole team made it, and I am so proud of everyone!",
    "buon": "[sad] I waited for her all night... but she never came back.",
}


def main(argv):
    force = "--force" in argv
    want = [a for a in argv if not a.startswith("--")]
    names = [clone.safe_name(n) for n in want] or [
        n for n in clone.list_profiles(include_all_en=True)
        if not clone.is_pending(n) and (force or clone.needs_styles(n))]
    if not clone.can_make_styles():
        print("[i] Khong co GPU/Chatterbox -> dung cach BIEN DOI mau goc (cham hon, cam xuc nhe hon).")
    if not names:
        print("Moi giong tieng Anh deu da co mau cam xuc. Dung --force de tao lai.")
        return 0
    OUT.mkdir(exist_ok=True)
    rows = []
    print("=== TAO MAU CAM XUC cho %d giong (GPU ~1-2 phut/giong) ===" % len(names))
    for i, n in enumerate(names):
        t0 = time.time()
        print("\n[%d/%d] %s" % (i + 1, len(names), n), flush=True)
        row = {"giong": n}
        try:
            rep = clone.make_styles_auto(n, force=force, say=lambda m: print(m, flush=True))
            for sty in ("hype", "calm"):
                r = rep.get(sty) or {}
                row[sty] = ("%d%%" % round(r["sim"] * 100)) if r.get("sim") is not None else ""
                row[sty + "_ket_qua"] = r.get("note", "")
        except Exception as e:
            row["loi"] = str(e)[:200]
            print("  loi:", e)
        for k, txt in DEMO.items():
            try:
                wav, _m, _e = expressive.synth_clone(txt, "c:" + n)
                f = OUT / ("%s_%s.wav" % (n, k))
                f.write_bytes(wav)
                if k == "thuong":
                    got = clone._voicesim(clone._sim_refs(n), [f])
                    if got:
                        row["giong_giong"] = "%d%%" % round(got[0] * 100)
                        row["dat_80"] = "DAT" if got[0] >= 0.80 else "CHUA DAT"
            except Exception as e:
                row["loi"] = (row.get("loi", "") + " | demo %s: %s" % (k, e))[:300]
        row["giay"] = round(time.time() - t0)
        print("  => giong giong: %s | hao hung: %s | tram: %s"
              % (row.get("giong_giong", "?"), row.get("hype") or "-", row.get("calm") or "-"))
        rows.append(row)
    cols = ["giong", "giong_giong", "dat_80", "hype", "hype_ket_qua", "calm", "calm_ket_qua", "giay", "loi"]
    with open(OUT / "BAO_CAO.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    ok = sum(1 for r in rows if r.get("dat_80") == "DAT")
    print("\n=== XONG: %d/%d giong dat >= 80%% giong goc. Bao cao: %s ===" % (ok, len(rows), OUT / "BAO_CAO.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
