# -*- coding: utf-8 -*-
"""Worker nhan ban giong (F5-TTS) — HTTP server nho chay trong tien trinh RIENG.

Vi sao tach tien trinh: torch + model giong chiem vai GB RAM va co the crash
(GPU het VRAM, driver loi). Tach ra thi server web KHONG BAO GIO chet theo —
worker sap thi tool chi bao loi dung doan do roi tu khoi dong lai worker.

Chay:  python clone_worker.py [port]
API:
  GET  /ping      -> {"app":"whisper_stt_clone_worker", "device":..., "ready":...}
  POST /infer     -> doc tung doan, GHI RA FILE theo dung chi so goc
                     {"ok":true,"written":[...],"failed":[{"i":..,"error":..}]}
  POST /shutdown  -> tat worker

Nguyen tac: 1 doan loi KHONG lam chet ca lenh — ghi vao `failed` roi doc tiep.
Tien trinh chinh se doc lai rieng nhung doan do (tu dong ha xuong CPU).
"""

import inspect
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

APP = "whisper_stt_clone_worker"
VERSION = "2.1"
_lock = threading.Lock()          # 1 lenh suy dien 1 luc (GPU khong chia duoc)
_last_used = [time.time()]


def log(msg):
    sys.stderr.write("[clone-worker %s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


# ---------------------------------------------------------------- ffmpeg/PATH

def _setup_ffmpeg_path():
    """Bao dam co ffmpeg trong PATH (torchaudio/pydub can) — khong chet neu thieu."""
    root = Path(__file__).resolve().parents[2]
    for cand in root.glob("ffmpeg-*/bin"):
        if cand.is_dir():
            os.environ["PATH"] = str(cand) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(str(cand))
                except OSError:
                    pass
            break
    try:
        import imageio_ffmpeg
        os.environ["PATH"] = (os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
                              + os.pathsep + os.environ.get("PATH", ""))
    except Exception:
        pass


_setup_ffmpeg_path()
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


# ------------------------------------------------------------------- device

def detect_device(prefer="auto"):
    """Chon thiet bi chay nhanh nhat MA THUC SU dung duoc (co thu tensor that)."""
    prefer = (prefer or "auto").lower()
    try:
        import torch
    except ImportError:
        return "cpu"
    if prefer in ("cpu",):
        return "cpu"
    if prefer in ("cuda", "mps") and prefer != "auto":
        return prefer
    try:
        if torch.cuda.is_available():
            torch.zeros(1, device="cuda")        # thu that: driver loi se nem o day
            torch.backends.cudnn.benchmark = True
            try:
                torch.set_float32_matmul_precision("high")
            except Exception:
                pass
            log("GPU NVIDIA: %s" % torch.cuda.get_device_name(0))
            return "cuda"
    except Exception as e:
        log("Co CUDA nhung khong dung duoc (%s) -> bo qua." % e)
    try:
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            log("Tang toc Apple MPS.")
            return "mps"
    except Exception:
        pass
    log("Chay bang CPU.")
    return "cpu"


# --------------------------------------------------------------- nap model

class State:
    tts = None
    ckpt = None
    device = None
    infer_kwargs = set()


def _new_f5(ckpt, vocab, device):
    from f5_tts.api import F5TTS
    sig = inspect.signature(F5TTS.__init__)
    kw = {"ckpt_file": ckpt or "", "vocab_file": vocab or "", "device": device}
    # ten tham so doi theo phien ban f5-tts (model / model_type)
    if "model" in sig.parameters:
        kw["model"] = "F5TTS_Base"
    elif "model_type" in sig.parameters:
        kw["model_type"] = "F5TTS_Base"
    kw = {k: v for k, v in kw.items() if k in sig.parameters}
    return F5TTS(**kw)


def get_tts(ckpt, vocab, device):
    """Nap (hoac tai dung) model. Loi tren GPU thi tu ha xuong CPU."""
    if State.tts is not None and State.ckpt == (ckpt, vocab) and State.device == device:
        return State.tts

    if State.tts is not None:
        State.tts = None
        try:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    log("Nap F5-TTS  ckpt=%s  device=%s" % (ckpt or "(mac dinh)", device))
    t0 = time.time()
    try:
        tts = _new_f5(ckpt, vocab, device)
    except Exception as e:
        if device != "cpu":
            log("Nap tren %s loi (%s) -> thu lai bang CPU." % (device, e))
            device = "cpu"
            tts = _new_f5(ckpt, vocab, "cpu")
        else:
            raise
    State.tts, State.ckpt, State.device = tts, (ckpt, vocab), device
    State.infer_kwargs = set(inspect.signature(tts.infer).parameters)
    log("Nap xong sau %.1fs (device=%s)" % (time.time() - t0, device))
    return tts


def _infer_one(tts, req, text, speed, out_path):
    """Doc 1 doan ra file wav. Ghi file tam roi doi ten -> khong ai doc file nua voi."""
    tmp = out_path.with_name(out_path.name + ".part")
    kw = {
        "ref_file": req["ref_audio"],
        "ref_text": req.get("ref_text") or "",
        "gen_text": text,
        "file_wave": str(tmp),
        "nfe_step": int(req.get("nfe_step") or 32),
        "cfg_strength": float(req.get("cfg_strength") or 2.0),
        "sway_sampling_coef": float(req.get("sway", -1.0)),
        "cross_fade_duration": float(req.get("cross_fade", 0.12)),
        "target_rms": float(req.get("target_rms") or 0.1),
        "speed": float(speed or 1.0),
        "remove_silence": bool(req.get("remove_silence")),
        "show_info": lambda *_a, **_k: None,
    }
    seed = req.get("seed")
    if seed is not None and int(seed) >= 0:
        kw["seed"] = int(seed)
        try:
            import torch
            torch.manual_seed(int(seed))
        except Exception:
            pass
    # chi truyen tham so ma phien ban f5-tts nay hieu
    kw = {k: v for k, v in kw.items() if not State.infer_kwargs or k in State.infer_kwargs}
    tts.infer(**kw)
    if not tmp.exists() or tmp.stat().st_size < 200:
        raise RuntimeError("file rỗng")
    os.replace(tmp, out_path)


def handle_infer(req):
    t0 = time.time()
    out_dir = Path(req["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    items = req.get("items")
    if items is None:                      # tuong thich nguoc: danh sach texts phang
        items = [{"i": i, "text": t, "speed": req.get("speed", 1.0)}
                 for i, t in enumerate(req.get("texts") or [], 1)]

    device = req.get("device") or detect_device(req.get("prefer_device", "auto"))
    tts = get_tts(req.get("ckpt") or "", req.get("vocab") or "", device)
    lower = bool(req.get("lowercase", True))

    written, failed = [], []
    for it in items:
        idx = int(it["i"])
        text = (it.get("text") or "").strip()
        out = out_dir / ("%04d.wav" % idx)
        if not text:
            continue                        # doan trong: de tien trinh chinh giu im lang
        gen = text.lower() if lower else text
        try:
            _infer_one(tts, req, gen, it.get("speed", 1.0), out)
            written.append(idx)
        except Exception as e:
            msg = "%s: %s" % (type(e).__name__, e)
            log("doan %d loi -> %s" % (idx, msg))
            failed.append({"i": idx, "error": msg[:300]})
            # het VRAM: don dep ngay de cac doan sau con co co hoi chay
            if "out of memory" in str(e).lower():
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass
    return {"ok": True, "device": State.device, "written": written,
            "failed": failed, "elapsed": round(time.time() - t0, 2)}


# --------------------------------------------------------------- HTTP server

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/ping":
            self._json(200, {"app": APP, "version": VERSION, "ready": True,
                             "device": State.device, "busy": _lock.locked(),
                             "pid": os.getpid()})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/shutdown":
            self._json(200, {"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)),
                             daemon=True).start()
            return
        if self.path != "/infer":
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            return self._json(400, {"error": "JSON loi: %s" % e})
        try:
            with _lock:
                _last_used[0] = time.time()
                res = handle_infer(req)
                _last_used[0] = time.time()
            self._json(200, res)
        except Exception:
            err = traceback.format_exc()
            log("LOI NANG:\n" + err)
            self._json(500, {"error": err[-1500:]})


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
    idle_exit = float(os.environ.get("CLONE_IDLE_EXIT") or 0)
    httpd = Server(("127.0.0.1", port), Handler)
    log("San sang tai cong %d (pid %d)" % (port, os.getpid()))

    if idle_exit > 0:
        def watchdog():
            while True:
                time.sleep(30)
                if not _lock.locked() and time.time() - _last_used[0] > idle_exit:
                    log("Khong dung %.0fs -> tu tat de giai phong RAM." % idle_exit)
                    os._exit(0)
        threading.Thread(target=watchdog, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
