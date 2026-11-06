# -*- coding: utf-8 -*-
"""AI LOCAL cho "AI bien kich" (tu gan the bieu cam) — Ollama + model qwen2.5:3b.

Chay tu Start-Windows.bat trong cua so rieng "AI local" (khong chan tool):
  1. Da co Ollama (cai san tren may hoac thu muc ollama\\) -> dung luon.
     Chua co -> tai ban portable tu GitHub (~1.5GB, 1 LAN, tai tiep duoc khi dut).
  2. Bat Ollama chay nen (model luu trong data\\ollama_models -> khong mat khi
     may quan net xoa thu muc user).
  3. Chua co model -> tai qwen2.5:3b (~1.9GB, 1 LAN).
Khong co mang / loi -> tool VAN CHAY, "AI bien kich" dung ban bien kich nhanh.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OLL_DIR = ROOT / "ollama"
MODELS = ROOT / "data" / "ollama_models"
HOST = "127.0.0.1:11434"
URL = "http://" + HOST
ZIP_URL = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"
sys.path.insert(0, str(ROOT))
try:
    from app import config
    MODEL = getattr(config, "OLLAMA_MODEL", "") or "qwen2.5:3b"
except Exception:
    MODEL = "qwen2.5:3b"


def say(m):
    print(m, flush=True)


def api(path, data=None, timeout=5):
    req = urllib.request.Request(URL + path, data=json.dumps(data).encode() if data else None,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def running():
    try:
        with api("/api/tags", timeout=2) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None


def find_exe():
    local = OLL_DIR / "ollama.exe"
    if local.exists():
        return str(local)
    w = shutil.which("ollama")
    if w:
        return w
    p = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(p) if p.exists() else None


def download(url, dst):
    """Tai co RESUME (dut mang chay lai la tai tiep)."""
    part = dst.with_suffix(dst.suffix + ".part")
    done = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"Range": "bytes=%d-" % done} if done else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = done + int(r.headers.get("Content-Length") or 0)
        if done and r.status != 206:            # server khong cho resume -> tai lai
            done, total = 0, int(r.headers.get("Content-Length") or 0)
        with open(part, "ab" if done else "wb") as f:
            last = 0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                done += len(b)
                if time.time() - last > 2:
                    last = time.time()
                    say("    tai Ollama: %d%% (%d/%d MB)" % (done * 100 // max(1, total),
                                                         done >> 20, total >> 20))
    part.rename(dst)


def get_ollama():
    exe = find_exe()
    if exe:
        return exe
    say("[*] Chua co Ollama -> tai ban portable (~1.5GB, 1 lan)...")
    z = ROOT / "ollama-windows-amd64.zip"
    if not z.exists():
        download(ZIP_URL, z)
    say("[*] Giai nen Ollama...")
    tmp = ROOT / "ollama.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(z) as zz:
        zz.extractall(tmp)
    shutil.rmtree(OLL_DIR, ignore_errors=True)
    tmp.rename(OLL_DIR)
    try:
        z.unlink()
    except OSError:
        pass
    return find_exe()


def start_server(exe):
    MODELS.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OLLAMA_MODELS=str(MODELS), OLLAMA_HOST=HOST,
               OLLAMA_KEEP_ALIVE="10m")
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x08000000      # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen([exe, "serve"], env=env, creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=str(Path(exe).parent))
    for _ in range(60):
        if running() is not None:
            return True
        time.sleep(1)
    return False


def pull(model):
    say("[*] Tai model AI %s (~1.9GB, 1 lan)..." % model)
    with api("/api/pull", {"model": model, "stream": True}, timeout=3600) as r:
        last = 0
        for line in r:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("error"):
                raise RuntimeError(d["error"])
            if d.get("total") and time.time() - last > 2:
                last = time.time()
                say("    %s: %d%%" % (d.get("status", ""), d.get("completed", 0) * 100 // d["total"]))
            if d.get("status") == "success":
                return True
    return False


def main():
    say("=== AI local (Ollama) cho AI bien kich ===")
    tags = running()
    if tags is None:
        exe = get_ollama()
        if not exe:
            say("[!] Khong co Ollama -> AI bien kich dung ban nhanh.")
            return 0
        if not start_server(exe):
            say("[!] Ollama khong khoi dong duoc -> AI bien kich dung ban nhanh.")
            return 0
        tags = running() or []
    if not any(t == MODEL or t.startswith(MODEL + ":") for t in tags):
        pull(MODEL)
    say("[v] AI local san sang (%s). Cua so nay tu dong sau 5 giay." % MODEL)
    time.sleep(5)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        say("[!] AI local loi: %s" % e)
        say("    Tool van chay binh thuong; AI bien kich dung ban nhanh. Mo lai tool de thu lai.")
        time.sleep(15)
