import os
import shutil
import tempfile
import urllib.request
import urllib.error
import time
import subprocess
import json
import sys
import threading
from pathlib import Path

DATA = Path("data")
MODEL = DATA / "f5_vi" / "model.pt"
VOCAB = DATA / "f5_vi" / "vocab.txt"
CLONE_DIR = DATA / "voice_profiles"

def available():
    """Kiem tra co du dieu kien de chay voice clone khong."""
    return MODEL.exists() and VOCAB.exists()

def list_profiles():
    os.makedirs(CLONE_DIR, exist_ok=True)
    return [d.name for d in CLONE_DIR.iterdir() if d.is_dir()]

def _find_python():
    """Tim python phu hop de chay clone_worker."""
    if os.name != "nt":
        f5py = DATA / "f5env" / "bin" / "python"
        if f5py.exists():
            return str(f5py)
    return sys.executable

WORKER = Path("app") / "core" / "clone_worker.py"

_worker_proc = None

def _start_worker_if_needed(device=None):
    global _worker_proc
    # Ping — neu worker da chay roi thi thoi
    try:
        req = urllib.request.Request("http://127.0.0.1:8081/ping")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.read() == b"pong":
                return  # already running
    except (urllib.error.URLError, Exception):
        pass
    
    # Kill old zombie process if exists
    if _worker_proc is not None:
        try:
            _worker_proc.kill()
        except Exception:
            pass
        _worker_proc = None
    
    py = _find_python()
    print("Starting F5TTS Worker with: %s" % py)
    
    # Kiem tra f5_tts co duoc cai trong python nay khong
    check = subprocess.run([py, "-c", "import f5_tts; print('ok')"],
                           capture_output=True, text=True, timeout=30)
    if check.returncode != 0:
        raise RuntimeError(
            "Chua cai dat F5-TTS!\n"
            "Vui long chay lenh sau trong terminal/cmd:\n"
            "  pip install torch f5-tts\n"
            "Hoac chay lai Start-Windows.bat de tu dong cai dat.")
    
    env = os.environ.copy()
    env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    
    _worker_proc = subprocess.Popen([py, str(WORKER)],
                                    stdout=subprocess.DEVNULL, stderr=None,
                                    env=env)
    
    # Wait for ready (120s cho lan dau tien load PyTorch + CUDA kernels)
    start_t = time.time()
    while time.time() - start_t < 120:
        # Check if worker process crashed
        if _worker_proc.poll() is not None:
            raise RuntimeError("F5TTS Worker bi crash khi khoi dong (exit code %d)." % _worker_proc.returncode)
        try:
            req = urllib.request.Request("http://127.0.0.1:8081/ping")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.read() == b"pong":
                    print("F5TTS Worker is ready!")
                    return
        except (urllib.error.URLError, Exception):
            time.sleep(1.5)
            
    raise RuntimeError("Khong the khoi dong F5TTS Worker sau 120s.")

def synth_cues_clone(texts, voice_id, rate=1.0, progress=None):
    vp = DATA / "voice_profiles" / voice_id
    ref_audio = vp / "ref.wav"
    ref_txt = vp / "ref.txt"
    if not ref_audio.exists() or not ref_txt.exists():
        return [b""] * len(texts)
        
    ref_text = ref_txt.read_text(encoding="utf-8").strip()
    
    _start_worker_if_needed()
    
    # Convert rate (e.g. "+0%", "-10%", "+20%") to float multiplier
    try:
        if isinstance(rate, str) and rate.endswith("%"):
            rate_val = float(rate.replace("%", "").replace("+", ""))
            speed = 1.0 + (rate_val / 100.0)
        else:
            speed = float(rate)
    except Exception:
        speed = 1.0

    import re
    fast_tags = ["vui", "hào hứng", "gấp gáp", "tự hào", "wow", "ngạc nhiên"]
    slow_tags = ["buồn", "mệt mỏi", "thở dài", "thì thầm", "bí ẩn", "lạnh lùng", "nghỉ", "nghỉ dài"]
    
    clean_texts = []
    for t in texts:
        t_lower = t.lower()
        
        # Punctuation / Prosody injection based on emotion tags
        if "[nghỉ]" in t_lower or "[nghỉ dài]" in t_lower:
            t += "..."
        if "[vui]" in t_lower or "[hào hứng]" in t_lower or "[tự hào]" in t_lower:
            if not t.endswith("!"): t += "!"
        if "[buồn]" in t_lower or "[mệt mỏi]" in t_lower or "[thở dài]" in t_lower or "[sợ hãi]" in t_lower:
            if not t.endswith("..."): t += "..."
        if "[tức giận]" in t_lower:
            t = t.upper() + "!"
        if "[gấp gáp]" in t_lower:
            t = t.replace(",", "")
            
        # Check for tags to dynamically adjust speed
        if any(f"[{tag}]" in t_lower for tag in fast_tags):
            speed *= 1.15
        elif any(f"[{tag}]" in t_lower for tag in slow_tags):
            speed *= 0.85
            
        t = re.sub(r'\[.*?\]', '', t)
        t = re.sub(r'\*.*?\*', '', t)
        t = t.strip()
        if t:
            clean_texts.append(t)
        
    texts = clean_texts
    if not texts:
        return []

    n = len(texts)
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td) / "chunks"
        out_dir.mkdir()
        
        # Phat hien tieng Viet
        full_text = " ".join(texts).lower()
        vi_chars = set("àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ")
        is_vi = any(c in vi_chars for c in full_text)
        
        from app import config
        req_data = {
            "ref_audio": str(ref_audio.absolute()),
            "ref_text": ref_text,
            "texts": texts,
            "out_dir": str(out_dir.absolute()),
            "ckpt": str(MODEL.absolute()) if is_vi else "", 
            "vocab": str(VOCAB.absolute()) if is_vi else "",
            "nfe_step": getattr(config, "CLONE_NFE_STEP", 16),
            "speed": speed,
            "device": None  # Auto-detect trong worker
        }
        
        post_data = json.dumps(req_data).encode("utf-8")
        http_req = urllib.request.Request("http://127.0.0.1:8081/infer", data=post_data, headers={"Content-Type": "application/json"})
        
        err_msg = [None]
        def _do_req(r_data, r_req, attempt):
            try:
                with urllib.request.urlopen(r_req, timeout=3600) as resp:
                    resp.read()
            except urllib.error.HTTPError as e:
                err_msg[0] = e.read().decode("utf-8")
                if attempt == 0:
                    print("Loi GPU, thu lai voi CPU...")
                    r_data["device"] = "cpu"
                    new_post = json.dumps(r_data).encode("utf-8")
                    new_req = urllib.request.Request("http://127.0.0.1:8081/infer", data=new_post, headers={"Content-Type": "application/json"})
                    try:
                        with urllib.request.urlopen(new_req, timeout=3600) as resp2:
                            resp2.read()
                            err_msg[0] = None  # Clear error on success
                    except urllib.error.HTTPError as e2:
                        err_msg[0] = e2.read().decode("utf-8")
            except Exception as e:
                err_msg[0] = str(e)
                
        t = threading.Thread(target=_do_req, args=(req_data, http_req, 0))
        t.start()
        
        last = 0
        while t.is_alive():
            time.sleep(1.0)
            done = len(list(out_dir.glob("*.wav"))) if out_dir.is_dir() else 0
            if done != last and progress:
                progress(min(done, n), n)
                last = done
                
        t.join()
        if err_msg[0]:
            raise RuntimeError("Loi engine clone:\n" + err_msg[0])
            
        clips = []
        for i in range(1, n + 1):
            p = out_dir / ("%04d.wav" % i)
            clips.append(p.read_bytes() if p.exists() and p.stat().st_size > 100 else b"")
            
        if progress:
            progress(n, n)
        return clips

def add_profile(name, audio_bytes, ref_text=""):
    """Them 1 voice profile moi."""
    d = CLONE_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    ref_audio = d / "ref.wav"
    ref_audio.write_bytes(audio_bytes)
    ref_txt = d / "ref.txt"
    ref_txt.write_text(ref_text or "", encoding="utf-8")

def delete_profile(name):
    """Xoa 1 voice profile."""
    d = CLONE_DIR / name
    if d.exists():
        shutil.rmtree(d)
