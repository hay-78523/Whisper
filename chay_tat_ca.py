# -*- coding: utf-8 -*-
"""CHAY TAT CA (goi tu CHAY-TAT-CA.bat): sau khi da cai GPU / Chatterbox / AI local
  1) kiem tra may (GPU, engine giong Anh)
  2) tao mau cam xuc cho moi giong con thieu     (tao_mau_cam_xuc.py)
  3) kiem tra cam xuc + do giong                 (kiem_tra_cam_xuc.py)
  4) gom ket qua vao 1 file KET_QUA_TEST_<ngay gio>.zip de gui ky thuat
Buoc nao loi van chay buoc sau; cuoi cung in tom tat.
"""
import csv
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HOME", str(ROOT / "data" / "hf_cache"))
PY = sys.executable


def step(title):
    print("\n" + "=" * 64 + "\n" + title + "\n" + "=" * 64, flush=True)


def machine_info():
    from app.core import clone
    gpu = clone.gpu_ready()
    eng = clone.engine_for(True)
    print("GPU NVIDIA: %s | engine giong Anh: %s | Chatterbox: %s"
          % ("CO" if gpu else "khong (chay CPU, cham hon)", eng, "co" if clone.cbx_ready() else "thieu"))
    if not gpu:
        print("[!] Khong dung GPU -> mau cam xuc tao bang cach BIEN DOI mau goc (cam xuc nhe hon).")
    return {"gpu": gpu, "engine": eng}


def run_tool(script, log):
    t = time.time()
    with open(log, "w", encoding="utf-8") as f:
        p = subprocess.Popen([PY, "-u", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            f.write(line)
            if not line.lstrip().startswith(("0%", "100%", "gen_text")) and "it/s" not in line:
                print(line, end="", flush=True)
        p.wait()
    print("[%s] %s xong sau %d phut" % ("v" if p.returncode == 0 else "x", script, (time.time() - t) / 60), flush=True)
    return p.returncode


def summary(csv_path, keys):
    if not csv_path.exists():
        return "chua co bao cao"
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ok = sum(1 for r in rows if r.get("dat_80") == "DAT")
    extra = ""
    if "cam_xuc_ro" in keys:
        vals = [int(r["cam_xuc_ro"].rstrip("%")) for r in rows if (r.get("cam_xuc_ro") or "").endswith("%")]
        if vals:
            extra = " | cam xuc ro trung binh %d%%" % (sum(vals) / len(vals))
    return "%d/%d giong dat >= 80%% giong goc%s" % (ok, len(rows), extra)


def collect(stamp):
    out = ROOT / ("KET_QUA_TEST_%s.zip" % stamp)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for d in ("mau_cam_xuc", "kiem_tra_cam_xuc"):
            for p in sorted((ROOT / d).glob("*")) if (ROOT / d).is_dir() else []:
                z.write(p, d + "/" + p.name)
        for p in sorted((ROOT / "data" / "logs").glob("*.log")) if (ROOT / "data" / "logs").is_dir() else []:
            z.write(p, "logs/" + p.name)
        for p in sorted(ROOT.glob("chay_tat_ca_*.log")):
            z.write(p, "logs/" + p.name)
    return out


def main():
    stamp = time.strftime("%Y%m%d_%H%M")
    t0 = time.time()
    step("1/4  KIEM TRA MAY")
    try:
        machine_info()
    except Exception as e:
        print("[x] Loi kiem tra may:", e)
    step("2/4  TAO MAU CAM XUC cho moi giong con thieu (GPU ~1-2 phut/giong)")
    run_tool("tao_mau_cam_xuc.py", ROOT / ("chay_tat_ca_%s_tao_mau.log" % stamp))
    step("3/4  KIEM TRA CAM XUC + DO GIONG (moi giong doc 5 cam xuc)")
    run_tool("kiem_tra_cam_xuc.py", ROOT / ("chay_tat_ca_%s_kiem_tra.log" % stamp))
    step("4/4  GOM KET QUA")
    zp = collect(stamp)
    print("\nTOM TAT (%d phut):" % ((time.time() - t0) / 60))
    print("  Mau cam xuc : " + summary(ROOT / "mau_cam_xuc" / "BAO_CAO.csv", ()))
    print("  Kiem tra    : " + summary(ROOT / "kiem_tra_cam_xuc" / "BAO_CAO.csv", ("cam_xuc_ro",)))
    print("\n>>> GUI FILE NAY CHO KY THUAT: %s (%.1f MB)" % (zp.name, zp.stat().st_size / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
