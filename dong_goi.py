# -*- coding: utf-8 -*-
"""DONG GOI THEO TUNG BAN — 1 lenh cho moi ban, code app/ dung chung.

Moi ban la 1 thu muc editions/<ten>/edition.json:
    whisper         -> BAN_GIAO_KHACH/1_Whisper_BanCaNhan.zip       (ca nhan, giao dien goc)
    starlight       -> BAN_GIAO_KHACH/2_StarlightVoice_DoanhNghiep.zip
    starlight_test  -> BAN_GIAO_KHACH/3_StarlightVoice_BanTest.zip   (it giong, test nhanh)

    python3 dong_goi.py whisper              # cap nhat code moi vao zip cua ban do (vai giay)
    python3 dong_goi.py all                  # ca 3 ban
    python3 dong_goi.py starlight --tu BAN_GIAO_KHACH/1_Whisper_BanCaNhan.zip
                                             # chua co zip -> chep tu zip khac roi cap nhat

Cap nhat TAI CHO (khong chep lai 7.9GB): ghi code moi vao cuoi zip, viet lai muc luc chi
tro ban moi. Luu du phong muc luc truoc -> loi giua chung thi khoi phuc nguyen trang.
Dong goi lai TU DAU (can python/ ban Windows): python3 export_for_client.py --edition <ten>
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "BAN_GIAO_KHACH"
TOP_FILES = ["HUONG-DAN.txt", "BANG-THE-BIEU-CAM.txt", "run.py", "Start-Windows.bat",
             "setup_gpu.py", "setup_ai.py", "setup_chatterbox.py", "setup_clone.py",
             "test_voices.py", "Test-Voices.bat", "tao_mau_cam_xuc.py", "Tao-Mau-Cam-Xuc.bat",
             "kiem_tra_cam_xuc.py", "Kiem-Tra-Cam-Xuc.bat", "chay_tat_ca.py", "CHAY-TAT-CA.bat"]
STORE = (".safetensors", ".pt", ".mp3", ".zip", ".dll", ".pyd", ".exe", ".bin", ".so",
         ".whl", ".png", ".jpg")
SKIP = {"__pycache__", ".DS_Store"}


def editions():
    return sorted(d.name for d in (ROOT / "editions").iterdir() if (d / "edition.json").is_file())


def load(name):
    f = ROOT / "editions" / name / "edition.json"
    if not f.is_file():
        sys.exit("[x] Khong co ban '%s'. Cac ban: %s" % (name, ", ".join(editions())))
    return json.loads(f.read_text(encoding="utf-8"))


def walk(d, prefix, out):
    for root, dirs, files in os.walk(d):
        dirs[:] = sorted(x for x in dirs if x not in SKIP)
        for f in sorted(files):
            if f in SKIP or f.endswith((".pyc", ".part")):
                continue
            p = Path(root) / f
            out[prefix + p.relative_to(d).as_posix()] = p


def plan(name, ed, tmp):
    """-> (file can ghi {ten trong zip: file}, ham 'giu ban ghi cu khong')."""
    repl = {}
    walk(ROOT / "app", "app/", repl)
    for f in TOP_FILES:
        if (ROOT / f).is_file():
            repl[f] = ROOT / f
    walk(ROOT / "editions" / name, "editions/%s/" % name, repl)
    if ed.get("brand_from"):
        walk(ROOT / "editions" / ed["brand_from"], "editions/%s/" % ed["brand_from"], repl)
    act = Path(tmp) / "ACTIVE"
    act.write_text(name, encoding="utf-8")
    repl["editions/ACTIVE"] = act
    only = ed.get("voices")
    if isinstance(only, list):                    # ban chi gom mot so giong: them giong con thieu
        for v in only:
            d = ROOT / "data" / "voice_profiles" / v
            if d.is_dir():
                walk(d, "data/voice_profiles/%s/" % v, repl)

    def keep(n):
        if n in repl or n == "PERSONAL" or n.startswith(("app/", "editions/")):
            return False                          # thay bang ban moi / dung dung cau hinh ban nay
        if isinstance(only, list) and n.startswith("data/voice_profiles/"):
            parts = n.split("/")
            return len(parts) < 4 or parts[2] in only
        return True
    return repl, keep


def patch(zp, repl, keep):
    with zipfile.ZipFile(zp) as z:
        start = z.start_dir
    size0 = zp.stat().st_size
    bak = zp.with_name(zp.name + ".muc_luc_du_phong")
    with open(zp, "rb") as f:
        f.seek(start)
        bak.write_bytes(f.read())
    try:
        z = zipfile.ZipFile(zp, "a", allowZip64=True)
        last = {}
        for i in z.filelist:
            last[i.filename] = i                  # ten trung -> ban ghi sau cung
        kept = [i for n, i in last.items() if keep(n)]
        z.filelist = kept
        z.NameToInfo = {i.filename: i for i in kept}
        for arc, p in sorted(repl.items()):
            z.write(p, arc, compress_type=zipfile.ZIP_STORED if p.suffix.lower() in STORE
                    else zipfile.ZIP_DEFLATED)
        z.close()
        bak.unlink()
        return len(z.filelist), (zp.stat().st_size - size0) / 1e6
    except BaseException:
        with open(zp, "r+b") as f:
            f.seek(start)
            f.write(bak.read_bytes())
            f.truncate(size0)
        bak.unlink()
        print("[x] Loi -> da khoi phuc zip nguyen trang.")
        raise


def verify(zp, name, ed):
    z = zipfile.ZipFile(zp)
    n = z.namelist()
    assert len(n) == len(set(n)), "con ten trung"
    assert z.read("editions/ACTIVE").decode().strip() == name
    for a in n:
        if a.startswith("app/") and (ROOT / a).is_file():
            assert z.read(a) == (ROOT / a).read_bytes(), "lech code: " + a
    for i in z.infolist():
        if i.file_size < 5e6:
            z.read(i)                             # doc het = kiem CRC file nho
    voices = sorted({x.split("/")[2] for x in n if x.startswith("data/voice_profiles/") and x.count("/") >= 3})
    return len(n), voices


def run(name, src=None):
    ed = load(name)
    zp = OUT / ed["zip"]
    if not zp.exists():
        if not src:
            sys.exit("[x] Chua co %s. Them --tu <zip ban khac> de chep roi cap nhat, hoac dong goi "
                     "tu dau: python3 export_for_client.py --edition %s" % (zp.name, name))
        free = shutil.disk_usage(OUT).free
        if free < Path(src).stat().st_size * 1.05:
            sys.exit("[x] O dia khong du cho de chep %s." % src)
        print("[*] Chep %s -> %s ..." % (src, zp.name), flush=True)
        shutil.copyfile(src, zp)
    with tempfile.TemporaryDirectory() as tmp:
        repl, keep = plan(name, ed, tmp)
        total, mb = patch(zp, repl, keep)
    count, voices = verify(zp, name, ed)
    print("[v] %-34s ban %-15s %s | %d file (+%.1f MB) | %d giong | kiem tra OK"
          % (zp.name, name, "CA NHAN" if ed.get("personal") else "doanh nghiep", count, mb, len(voices)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ban", help="ten ban (%s) hoac all" % ", ".join(editions()))
    ap.add_argument("--tu", default=None, help="chua co zip cua ban nay -> chep tu zip nay")
    a = ap.parse_args()
    for n in (editions() if a.ban == "all" else [a.ban]):
        run(n, a.tu)


if __name__ == "__main__":
    main()
