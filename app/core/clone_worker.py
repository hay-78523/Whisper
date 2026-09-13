# -*- coding: utf-8 -*-
"""Worker nhan ban giong - Chay ngam duoi dang HTTP Server tren port 8081.

Goi qua: <f5env>/bin/python clone_worker.py
Nhan HTTP POST tai /infer voi JSON.
Tu dong load F5TTS va re-load neu ckpt thay doi.
"""

import json
import sys
import platform
import os
import traceback
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import threading

# Ensure local ffmpeg is in PATH for torchcodec and pydub
ffmpeg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ffmpeg-master-latest-win64-gpl-shared", "bin")
if os.path.exists(ffmpeg_path):
    os.environ["PATH"] = ffmpeg_path + os.pathsep + os.environ["PATH"]
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(ffmpeg_path)

_infer_lock = threading.Lock()

try:
    import torch
    if torch.cuda.is_available():
        torch.backends.cudnn.benchmark = True
        torch.set_float32_matmul_precision("high")
except:
    pass

try:
    import imageio_ffmpeg
    os.environ["PATH"] += os.pathsep + os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
except ImportError:
    pass

def _detect_device():
    """Tu dong phat hien GPU tot nhat, fallback CPU neu can."""
    try:
        import torch
        if torch.cuda.is_available():
            # Thu tao 1 tensor nho tren GPU de kiem tra GPU co thuc su hoat dong
            try:
                t = torch.zeros(1, device="cuda")
                del t
                print("GPU detected: " + torch.cuda.get_device_name(0))
                return "cuda"
            except Exception as e:
                print(f"GPU co nhung khong dung duoc: {e}")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            print("Apple MPS detected. Enabling MPS acceleration!")
            return "mps"
    except ImportError:
        pass
    print("Khong tim thay GPU, su dung CPU.")
    return "cpu"

_auto_device = None

class F5State:
    tts = None
    ckpt = None
    device = None

def get_tts(ckpt_file, vocab_file, device):
    global _auto_device
    # Neu caller khong chi dinh device, tu dong phat hien 1 lan
    if not device:
        if _auto_device is None:
            _auto_device = _detect_device()
        device = _auto_device

    if F5State.tts is not None and F5State.ckpt == ckpt_file and F5State.device == device:
        return F5State.tts
    
    print(f"Loading F5TTS with ckpt={ckpt_file} on device={device}...")
    if F5State.tts is not None:
        del F5State.tts
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
            
    from f5_tts.api import F5TTS
    try:
        F5State.tts = F5TTS(model="F5TTS_Base", ckpt_file=ckpt_file, vocab_file=vocab_file, device=device)
    except Exception as e:
        if device != "cpu":
            print(f"Loi load F5TTS tren {device}: {e}")
            print("Thu lai voi CPU...")
            device = "cpu"
            _auto_device = "cpu"
            F5State.tts = F5TTS(model="F5TTS_Base", ckpt_file=ckpt_file, vocab_file=vocab_file, device="cpu")
        else:
            raise
    F5State.ckpt = ckpt_file
    F5State.device = device
    return F5State.tts

class WorkerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
        
    def do_GET(self):
        if self.path == "/ping":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"pong")
            
    def do_POST(self):
        if self.path == "/infer":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            req = json.loads(post_data.decode("utf-8"))
            
            try:
                with _infer_lock:  # Serialize GPU access — only 1 infer at a time
                    out_dir = Path(req["out_dir"])
                    out_dir.mkdir(parents=True, exist_ok=True)
                    
                    device = req.get("device")
                    if not device:
                        device = "cpu" if platform.system() == "Darwin" else None
                        
                    tts = get_tts(req["ckpt"], req["vocab"], device)
                    
                    for i, text in enumerate(req["texts"], 1):
                        out = out_dir / ("%04d.wav" % i)
                        text = (text or "").strip()
                        if not text:
                            out.write_bytes(b"")
                            continue
                        tts.infer(ref_file=req["ref_audio"], ref_text=req["ref_text"],
                                  gen_text=text.lower(), file_wave=str(out),
                                  nfe_step=req.get("nfe_step", 12),
                                  speed=req.get("speed", 1.0),
                                  remove_silence=False)
                
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))
            except Exception as e:
                err_str = traceback.format_exc()
                print("Loi trong qua trinh tao giong:\n", err_str)
                self.send_response(500)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(err_str.encode("utf-8"))

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def main():
    port = 8081
    server_address = ("127.0.0.1", port)
    httpd = ThreadedHTTPServer(server_address, WorkerHandler)
    print(f"Starting F5TTS Clone Worker on port {port}...")
    httpd.serve_forever()

if __name__ == "__main__":
    main()
