# -*- coding: utf-8 -*-
"""Cai dat Voice Clone (F5-TTS) bang MOT lenh — an toan de chay lai nhieu lan.

    python3 setup_clone.py              # cai tat ca nhung gi con thieu
    python3 setup_clone.py --check      # chi xem dang thieu gi (khong tai gi)
    python3 setup_clone.py --venv       # cai vao moi truong rieng data/f5env
    python3 setup_clone.py --assets     # chi tai model + vocab
    python3 setup_clone.py --engine     # chi cai torch + f5-tts
    python3 setup_clone.py --force      # tai lai ca nhung file da co
    python3 setup_clone.py --no-test    # bo qua buoc tu kiem tra cuoi

Khac voi cach cai cu:
  - tai co RESUME va KIEM TRA NOI DUNG tung file (khong con canh tai 1.3GB xong
    moi biet la trang loi HTML, hay vocab thuc ra la file config.json);
  - tu chon ban PyTorch CUDA / CPU / MPS dung voi may, thu that truoc khi chot;
  - chay lai bao nhieu lan cung duoc, cai gi xong roi thi bo qua.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from app import config                         # noqa: E402
from app.core import clone, clone_assets       # noqa: E402

IS_WIN = os.name == "nt"
IS_MAC = platform.system() == "Darwin"


# ------------------------------------------------------------------ tien ich

def say(msg=""):
    print(msg, flush=True)


def head(msg):
    say("\n" + "=" * 64)
    say(msg)
    say("=" * 64)


def run(cmd, **kw):
    say("  $ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], **kw)


def bar(done, total, width=34):
    if not total:
        return "%s" % clone_assets.human(done)
    frac = min(1.0, done / float(total))
    fill = int(frac * width)
    return "[%s%s] %5.1f%%  %s / %s" % ("#" * fill, "." * (width - fill), frac * 100,
                                        clone_assets.human(done), clone_assets.human(total))


class Progress:
    """Thanh tien do 1 dong, khong lam tran man hinh."""

    def __init__(self):
        self.last = 0.0

    def __call__(self, done, total):
        now = time.time()
        if now - self.last < 0.25 and done != total:
            return
        self.last = now
        sys.stdout.write("\r  " + bar(done, total) + "   ")
        sys.stdout.flush()
        if total and done >= total:
            sys.stdout.write("\n")


# --------------------------------------------------------------- kiem tra may

def py_version_ok(py):
    try:
        r = subprocess.run([py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                           capture_output=True, text=True, timeout=60)
        major, minor = (int(x) for x in r.stdout.strip().split("."))
        return (major, minor) >= (3, 10), r.stdout.strip()
    except Exception as e:
        return False, str(e)


def find_python310():
    """Tim 1 python >= 3.10 tren may (de tao venv khi python dang chay qua cu)."""
    names = (["py -3.12", "py -3.11", "py -3.10"] if IS_WIN
             else ["python3.12", "python3.11", "python3.10", "python3"])
    for n in names:
        exe = n.split()[0]
        if not shutil.which(exe):
            continue
        cmd = n.split()
        ok, ver = py_version_ok_cmd(cmd)
        if ok:
            return cmd, ver
    return None, None


def py_version_ok_cmd(cmd):
    try:
        r = subprocess.run(cmd + ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                           capture_output=True, text=True, timeout=60)
        major, minor = (int(x) for x in r.stdout.strip().split("."))
        return (major, minor) >= (3, 10), r.stdout.strip()
    except Exception:
        return False, None


def has_nvidia():
    if IS_MAC:
        return False
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=30)
            return r.returncode == 0 and "GPU" in (r.stdout or "")
        except Exception:
            return False
    return False


# ------------------------------------------------------------------- cai dat

def pip(py, args, **kw):
    return run([py, "-m", "pip"] + args, **kw)


def make_venv():
    """Tao moi truong rieng data/f5env (tranh dung cham thu vien he thong)."""
    env = ROOT / "data" / "f5env"
    py = env / ("Scripts/python.exe" if IS_WIN else "bin/python")
    if py.exists():
        say("✓ Đã có môi trường riêng: %s" % env)
        return str(py)
    base, ver = find_python310()
    if not base:
        raise SystemExit("Không tìm thấy Python >= 3.10 trên máy — F5-TTS cần bản này.\n"
                         "Tải tại https://www.python.org/downloads/ rồi chạy lại.")
    say("Tạo môi trường riêng bằng Python %s…" % ver)
    env.parent.mkdir(parents=True, exist_ok=True)
    r = run(base + ["-m", "venv", str(env)])
    if r.returncode != 0 or not py.exists():
        raise SystemExit("Tạo môi trường riêng thất bại.")
    pip(str(py), ["install", "-q", "--upgrade", "pip", "wheel"])
    return str(py)


def install_torch(py):
    """Cai PyTorch dung cho may nay: CUDA neu co card NVIDIA, khong thi CPU/MPS."""
    r = subprocess.run([py, "-c", "import torch; print(torch.__version__, torch.cuda.is_available())"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        say("✓ PyTorch đã có: %s" % r.stdout.strip())
        return True

    if IS_MAC:
        say("Cài PyTorch cho macOS (dùng Apple MPS nếu có)…")
        ok = pip(py, ["install", "torch", "torchaudio"]).returncode == 0
    elif has_nvidia():
        say("Phát hiện card NVIDIA — cài PyTorch bản CUDA (~2.5GB, kiên nhẫn nhé)…")
        ok = pip(py, ["install", "torch", "torchaudio", "--index-url",
                      "https://download.pytorch.org/whl/cu121"]).returncode == 0
        if ok:
            chk = subprocess.run([py, "-c", "import torch; assert torch.cuda.is_available()"],
                                 capture_output=True, text=True)
            if chk.returncode != 0:
                say("⚠ Bản CUDA không chạy được trên máy này — chuyển sang bản CPU.")
                pip(py, ["uninstall", "-y", "torch", "torchaudio"])
                ok = pip(py, ["install", "torch", "torchaudio", "--index-url",
                              "https://download.pytorch.org/whl/cpu"]).returncode == 0
    else:
        say("Không có card NVIDIA — cài PyTorch bản CPU (nhẹ hơn, chạy mọi máy)…")
        ok = pip(py, ["install", "torch", "torchaudio", "--index-url",
                      "https://download.pytorch.org/whl/cpu"]).returncode == 0
    if not ok:
        raise SystemExit("Cài PyTorch thất bại — kiểm tra mạng rồi chạy lại lệnh này.")
    return True


def install_f5(py):
    r = subprocess.run([py, "-c", "import f5_tts; print('ok')"], capture_output=True, text=True)
    if r.returncode == 0:
        say("✓ F5-TTS đã có.")
        return True
    say("Cài F5-TTS…")
    if pip(py, ["install", "f5-tts"]).returncode != 0:
        raise SystemExit("Cài F5-TTS thất bại — kiểm tra mạng rồi chạy lại lệnh này.")
    r = subprocess.run([py, "-c", "import f5_tts; print('ok')"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("Cài xong nhưng vẫn không import được f5_tts:\n%s" % r.stderr[-500:])
    return True


def remember_python(py):
    """Ghi lai Python dung cho worker de server web khoi phai do lai."""
    p = Path("data") / "clone_python.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(py, encoding="utf-8")
    say("✓ Ghi nhớ Python của engine: %s" % py)


# ------------------------------------------------------------------ bao cao

def report():
    head("TÌNH TRẠNG VOICE CLONE")
    st = clone.status(recheck=True)

    say("1) Dữ liệu model (%s)" % clone_assets.model_dir())
    for a in st["assets"]:
        mark = "✓" if a["ok"] else "✗"
        extra = clone_assets.human(a["size"]) if a["exists"] else (a["problem"] or "chưa tải")
        if a["partial"]:
            extra += "  (đang tải dở: %s)" % clone_assets.human(a["partial"])
        say("   %s %-42s %s" % (mark, a["label"], extra))

    e = st["engine"]
    say("\n2) Engine F5-TTS")
    if e["installed"]:
        say("   ✓ dùng Python: %s" % e["python"])
    else:
        say("   ✗ chưa sẵn sàng")
        for line in (e["error"] or "").splitlines()[:10]:
            say("     " + line)

    say("\n3) Giọng đã nhân bản: %d" % len(st["profiles"]))
    for p in st["profiles"]:
        say("   🎤 %-22s %s  %s" % (
            p["name"],
            ("%.1fs" % p["duration"]) if p["duration"] else "?",
            ("· ".join(p["warnings"]) if p["warnings"] else "ok")))

    say("\n4) Chất lượng: nfe_step = %d  (preset: %s)"
        % (st["quality"]["nfe_step"], ", ".join(clone.QUALITY_NFE)))
    say("   thiết bị: %s" % (st["settings"].get("device") or config.CLONE_DEVICE))
    say("\n=> %s" % ("SẴN SÀNG DÙNG." if st["ready"] else
                     "CHƯA SẴN SÀNG — chạy: python3 setup_clone.py"))
    return st["ready"]


def self_test():
    head("TỰ KIỂM TRA (nạp model vào RAM)")
    try:
        info = clone.warm_up(log=say)
        say("✓ Engine chạy tốt trên: %s" % (info.get("device") or "?"))
    except Exception as e:
        say("✗ Không nạp được engine:\n%s" % e)
        return False
    names = clone.list_profiles()
    if not names:
        say("ℹ Chưa có giọng mẫu nào — vào trang /admin tải lên 6–12 giây giọng "
            "của bạn là dùng được ngay.")
        return True
    try:
        say("Đọc thử một câu bằng giọng '%s'…" % names[0])
        out = clone.make_sample(names[0])
        say("✓ Nghe thử tại: %s" % out)
    except Exception as e:
        say("✗ Đọc thử lỗi: %s" % e)
        return False
    finally:
        clone.stop_worker()
    return True


# -------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Cai dat Voice Clone (F5-TTS)")
    ap.add_argument("--check", action="store_true", help="chi xem tinh trang")
    ap.add_argument("--venv", action="store_true", help="cai vao moi truong rieng data/f5env")
    ap.add_argument("--assets", action="store_true", help="chi tai model + vocab")
    ap.add_argument("--engine", action="store_true", help="chi cai torch + f5-tts")
    ap.add_argument("--force", action="store_true", help="tai lai ca file da co")
    ap.add_argument("--no-test", action="store_true", help="bo qua buoc tu kiem tra")
    args = ap.parse_args()

    if args.check:
        return 0 if report() else 1

    do_assets = args.assets or not args.engine
    do_engine = args.engine or not args.assets

    if do_engine:
        head("BƯỚC 1/2 — ENGINE (PyTorch + F5-TTS)")
        py = make_venv() if args.venv else sys.executable
        ok, ver = py_version_ok(py)
        if not ok:
            say("⚠ Python đang dùng là %s — F5-TTS cần >= 3.10." % ver)
            py = make_venv()
        install_torch(py)
        install_f5(py)
        remember_python(py)

    if do_assets:
        head("BƯỚC 2/2 — MODEL GIỌNG VIỆT")
        try:
            clone_assets.ensure(progress=Progress(), log=say, force=args.force)
        except Exception as e:
            say("\n✗ %s" % e)
            say("\nMẹo: mạng chặn huggingface.co thì tool đã tự thử hf-mirror.com. "
                "Nếu vẫn lỗi, tải tay 2 file vào %s rồi chạy lại --check."
                % clone_assets.model_dir())
            return 1

    ready = report()
    if ready and not args.no_test:
        self_test()
    if ready:
        head("XONG — mở tool rồi vào /admin để tải giọng mẫu lên")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
