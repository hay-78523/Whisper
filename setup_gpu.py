# -*- coding: utf-8 -*-
"""Bat GPU NVIDIA cho Voice Clone (chay tu Start-Windows.bat).

Ban dong goi kem PyTorch ban CPU (chay moi may). May co card NVIDIA thi file nay
tai PyTorch ban CUDA (~3GB, 1 LAN) vao thu muc rieng torch_gpu\\ — KHONG dung
vao ban CPU, nen tai loi / card loi thi tool van chay CPU nhu thuong.

    python setup_gpu.py            # tu kiem tra, chi tai khi can
    python setup_gpu.py --force    # tai lai
    python setup_gpu.py --off      # tat GPU, quay ve CPU
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GPU_DIR = ROOT / "torch_gpu"
OK = GPU_DIR / ".ok"
FAILED = GPU_DIR / ".failed"
TORCH_VER = "2.8.0"
VARIANTS = ["cu128", "cu126"]     # cu128: GTX 16xx/RTX 20-50; cu126: card doi cu hon (GTX 9xx/10xx)
INDEX = "https://download.pytorch.org/whl/%s"
TEST = ("import torch; assert torch.cuda.is_available(), 'cuda khong san sang'; "
        "x = torch.randn(1024, 1024, device='cuda'); (x @ x).sum().item(); "
        "print(torch.cuda.get_device_name(0))")


def say(m):
    print(m, flush=True)


def gpu_name():
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    try:
        r = subprocess.run([exe, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None


def test_dir(d):
    env = dict(os.environ, PYTHONPATH=str(d), PYTHONNOUSERSITE="1")
    try:
        r = subprocess.run([sys.executable, "-c", TEST], capture_output=True, text=True,
                           timeout=300, env=env)
    except Exception as e:
        return False, str(e)
    if r.returncode == 0:
        return True, r.stdout.strip()
    return False, ((r.stderr or "").strip().splitlines() or ["?"])[-1][:200]


def rmtree(p):
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


def main():
    args = sys.argv[1:]
    if "--off" in args:
        if OK.exists():
            OK.rename(GPU_DIR / ".off")
        say("[*] Da tat GPU - Voice Clone chay CPU.")
        return 0
    if (GPU_DIR / ".off").exists() and "--force" not in args:
        return 0
    name = gpu_name()
    if not name:
        say("[*] Khong thay card NVIDIA - Voice Clone chay CPU (3 engine song song).")
        return 0
    if OK.exists() and "--force" not in args:
        say("[v] GPU san sang: %s (%s)" % (name, OK.read_text(encoding="utf-8").strip()))
        return 0
    # Neu Python hien tai da co san PyTorch CUDA -> kich hoat ngay khong can tai 3GB
    ok_native, info_native = test_dir(GPU_DIR if (GPU_DIR / "torch").is_dir() else "")
    if ok_native and "--force" not in args:
        GPU_DIR.mkdir(exist_ok=True)
        OK.write_text("cuda | %s" % info_native, encoding="utf-8")
        say("[v] GPU NVIDIA san sang ngay: %s" % info_native)
        return 0
    if FAILED.exists() and "--force" not in args:
        say("[!] GPU %s da thu va loi truoc do -> chay CPU. (Thu lai: python\\python.exe setup_gpu.py --force)" % name)
        return 0

    say("[*] Phat hien card NVIDIA: %s" % name)
    say("[*] Tai PyTorch ban GPU (~3GB, chi 1 lan, can mang). Dong cua so = bo qua, lan sau tai tiep.")
    errs = []
    for cu in VARIANTS:
        tmp = ROOT / ("torch_gpu_" + cu + ".tmp")
        rmtree(tmp)
        say("[*] Dang tai ban %s ..." % cu)
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
               "--no-deps", "--target", str(tmp), "--index-url", INDEX % cu,
               "torch==%s" % TORCH_VER, "torchaudio==%s" % TORCH_VER]
        t0 = time.time()
        if subprocess.run(cmd).returncode != 0:
            errs.append("%s: tai/cai loi" % cu)
            rmtree(tmp)
            continue
        ok, info = test_dir(tmp)
        if ok:
            rmtree(GPU_DIR)
            tmp.rename(GPU_DIR)
            OK.write_text("%s | %s" % (cu, info), encoding="utf-8")
            say("[v] GPU OK: %s (%s, %.0f giay)" % (info, cu, time.time() - t0))
            return 0
        errs.append("%s: %s" % (cu, info))
        say("[!] Ban %s khong chay duoc tren card nay: %s" % (cu, info))
        rmtree(tmp)
    if all(e.endswith("tai/cai loi") for e in errs):
        # chi la loi mang -> KHONG danh dau hong, lan mo sau tu thu tai lai
        say("[!] Chua tai duoc ban GPU (mang?) -> lan nay chay CPU, lan sau tu thu lai.")
        return 0
    GPU_DIR.mkdir(exist_ok=True)
    FAILED.write_text("\n".join(errs), encoding="utf-8")
    say("[!] Card nay khong chay duoc ban GPU (%s) -> Voice Clone chay CPU." % "; ".join(errs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
