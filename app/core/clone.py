# -*- coding: utf-8 -*-
"""Nhan ban giong (Voice Clone) bang F5-TTS — tinh nang CHINH cua tool.

Thiet ke de KHONG GAY va cho ra am thanh dung muc dung duoc that:

* Cai dat / tai model: `clone_assets` tai co resume + kiem tra noi dung, nen
  khong bao gio "tai xong" ma dung thi loi. Thieu gi thi `status()` noi ro thieu gi.
* Chay: model song trong 1 tien trinh RIENG (clone_worker). Worker crash (het
  VRAM, driver loi) thi server web van song, tool tu khoi dong lai worker va
  doc lai RIENG nhung doan bi loi (tu ha xuong CPU) — khong bo ca job dang chay.
* Giu DUNG so doan: doan trong / doan loi van tra ve b"" o DUNG vi tri, nho vay
  giong luon roi dung timestamp cua video (truoc day doan trong bi bo khoi danh
  sach -> lech tieng toan bo phan sau).
* Mau giong (ref) duoc chuan hoa 24kHz mono, cat im lang, gioi han 12 giay —
  dung khoang F5 cho chat luong tot nhat; thieu script mau thi tu nghe va ghi lai
  (dung Whisper co san) vi ref_text SAI la nguyen nhan so 1 lam giong bi nhoe.
* Moi doan sinh ra deu duoc hau ky: cat im lang dau/cuoi, can bang am luong,
  fade 10ms chong tieng "tach" khi lap track -> nghe lien mach, khong to nho that thuong.
"""

import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from array import array
from pathlib import Path

from .. import config
from . import clone_assets

DATA = Path("data")
CLONE_DIR = DATA / "voice_profiles"
LOG_DIR = DATA / "logs"
WORKER = Path("app") / "core" / "clone_worker.py"
SETTINGS_FILE = DATA / "clone_settings.json"
PYTHON_HINT = DATA / "clone_python.txt"
WORKER_APP = "whisper_stt_clone_worker"

# tuong thich nguoc (cac module khac tung import truc tiep)
MODEL = clone_assets.model_path()
VOCAB = clone_assets.vocab_path()

_SETUP_HINT = ("Chưa cài engine nhân bản giọng.\n"
               "Chạy 1 lệnh này trong terminal tại thư mục tool:\n"
               "    python3 setup_clone.py\n"
               "(Windows: chạy lại Start-Windows.bat — nó tự gọi lệnh trên.)")


# ============================================================== cai dat / thiet lap

QUALITY_NFE = {"nhanh": 16, "chuan": 32, "toida": 48}
_DEFAULT_SETTINGS = {
    "quality": None,        # nhanh | chuan | toida ; None = theo config
    "device": None,         # auto | cuda | mps | cpu
    "normalize": None,      # can bang am luong tung doan
    "trim": None,           # cat im lang dau/cuoi
    "lowercase": None,      # ha chu thuong truoc khi doc (model ViVoice)
    "denoise_ref": None,    # loc on mau giong
}


def get_settings():
    """Thiet lap nguoi dung tinh chinh tren /admin (ghi de config khi co)."""
    s = dict(_DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return s


def set_settings(patch):
    s = get_settings()
    for k, v in (patch or {}).items():
        if k in _DEFAULT_SETTINGS:
            s[k] = v
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return s


def _cfg(key, default=None):
    return getattr(config, key, default)


def _opt(name, cfg_key, default):
    """Uu tien thiet lap tren /admin, roi den config.py, roi gia tri mac dinh."""
    v = get_settings().get(name)
    if v is None:
        v = _cfg(cfg_key, default)
    return default if v is None else v


def nfe_step():
    q = get_settings().get("quality")
    if q in QUALITY_NFE:
        return QUALITY_NFE[q]
    return int(_cfg("CLONE_NFE_STEP", 32))


# ============================================================== trang thai

def assets_ready():
    return clone_assets.ready()


_engine_cache = {"python": None, "ok": False, "error": None, "at": 0.0}
_engine_lock = threading.Lock()


def _python_candidates():
    win = os.name == "nt"
    sub = ("Scripts", "python.exe") if win else ("bin", "python")
    cands = []
    hint = None
    try:
        hint = PYTHON_HINT.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    if hint:
        cands.append(hint)
    cands.append(str(DATA / "f5env" / sub[0] / sub[1]))
    if not win:                       # vi tri cu trong huong dan README doi truoc
        cands.append(str(Path.home() / "Library" / "Application Support"
                         / "whisper_stt" / "f5env" / "bin" / "python"))
    cands.append(sys.executable)
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _probe(py):
    """Python nay co f5_tts + torch dung duoc khong?"""
    if py != sys.executable and not Path(py).exists():
        return False, "không có file %s" % py
    try:
        r = subprocess.run([py, "-c", "import torch, f5_tts; print(torch.__version__)"],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        return False, str(e)
    if r.returncode == 0:
        return True, (r.stdout or "").strip()
    return False, ((r.stderr or "").strip().splitlines() or ["không rõ"])[-1][:300]


def engine_python(recheck=False, ttl=300):
    """Tim (va nho) Python co F5-TTS. Tra ve (path, loi)."""
    with _engine_lock:
        fresh = time.time() - _engine_cache["at"] < ttl
        if _engine_cache["ok"] and fresh and not recheck:
            return _engine_cache["python"], None
        if not recheck and fresh and _engine_cache["error"]:
            return None, _engine_cache["error"]

        errs = []
        for py in _python_candidates():
            ok, info = _probe(py)
            if ok:
                _engine_cache.update(python=py, ok=True, error=None, at=time.time())
                try:
                    PYTHON_HINT.parent.mkdir(parents=True, exist_ok=True)
                    PYTHON_HINT.write_text(py, encoding="utf-8")
                except OSError:
                    pass
                return py, None
            errs.append("%s → %s" % (py, info))
        msg = _SETUP_HINT + "\n\nĐã thử:\n  " + "\n  ".join(errs[:4])
        _engine_cache.update(python=None, ok=False, error=msg, at=time.time())
        return None, msg


def engine_installed():
    py, _err = engine_python()
    return bool(py)


def available():
    """Co the dung voice clone ngay bay gio khong (dung cho UI cu)."""
    return assets_ready()


def status(recheck=False):
    """Bao cao day du cho trang /admin — thieu gi, loi gi, dang chay tren gi."""
    py, err = engine_python(recheck=recheck)
    assets = clone_assets.state()
    w = worker_info()
    return {
        "assets": assets,
        "assets_ready": all(a["ok"] for a in assets),
        "engine": {"installed": bool(py), "python": py, "error": err,
                   "hint": None if py else _SETUP_HINT},
        "worker": w,
        "settings": get_settings(),
        "quality": {"nfe_step": nfe_step(), "presets": QUALITY_NFE},
        "profiles": [profile_info(p) for p in list_profiles()],
        "ready": bool(py) and all(a["ok"] for a in assets),
        "log": log_tail(12),
    }


def log_tail(n=20):
    p = LOG_DIR / "clone_worker.log"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []


def ensure_assets(progress=None, log=None, force=False):
    """Tai model + vocab (goi tu /admin hoac setup_clone.py)."""
    return clone_assets.ensure(progress=progress, log=log, force=force)


# ============================================================== profile giong

_NAME_OK = re.compile(r"[^a-z0-9_\-]+")


def safe_name(name):
    """Ten giong -> ten thu muc an toan, giu chu Viet bang cach bo dau.

    "Phạm Nhật Đan" -> "pham_nhat_dan" (khong thanh "ph_m_nh_t_an").
    """
    import unicodedata
    s = (name or "").strip().lower().replace("đ", "d").replace("Đ", "d")
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(c))
    n = _NAME_OK.sub("_", s).strip("_")[:32]
    return n or "giong"


def list_profiles():
    CLONE_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(d.name for d in CLONE_DIR.iterdir()
                  if d.is_dir() and (d / "ref.wav").exists())


def profile_dir(name):
    return CLONE_DIR / name


def _meta_path(name):
    return profile_dir(name) / "meta.json"


def profile_meta(name):
    try:
        return json.loads(_meta_path(name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_meta(name, **patch):
    m = profile_meta(name)
    m.update(patch)
    p = _meta_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return m


def profile_info(name):
    d = profile_dir(name)
    m = profile_meta(name)
    txt = ""
    try:
        txt = (d / "ref.txt").read_text(encoding="utf-8").strip()
    except Exception:
        pass
    warn = []
    dur = m.get("duration") or 0
    if dur and dur < float(_cfg("CLONE_REF_MIN_SEC", 3.0)):
        warn.append("mẫu chỉ %.1f giây — nên 6–12 giây để giọng ổn định" % dur)
    if not txt:
        warn.append("chưa có script mẫu — sẽ tự nghe lại khi dùng lần đầu")
    return {
        "name": name,
        "duration": round(dur, 2) if dur else None,
        "ref_text": txt,
        "ref_text_source": m.get("ref_text_source"),
        "state": m.get("state", "ready"),
        "error": m.get("error"),
        "created": m.get("created"),
        "source": m.get("source"),
        "trimmed": bool(m.get("trimmed")),
        "has_sample": (d / "sample.wav").exists() or (d / "test.mp3").exists(),
        "warnings": warn,
    }


def sample_path(name):
    """File nghe thu cua giong (None neu chua co)."""
    for fn in ("sample.wav", "test.mp3"):
        p = profile_dir(name) / fn
        if p.exists() and p.stat().st_size > 100:
            return p
    return None


def add_profile(name, audio_bytes, ref_text="", filename=None, background=True):
    """Them/ghi de 1 giong mau.

    Mau duoc chuan hoa ngay (24kHz mono, bo im lang, toi da 12 giay). Viec cham
    (nghe lai lay script + tao file nghe thu) chay nen de trang /admin khong treo.
    """
    name = safe_name(name)
    d = profile_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    raw = d / ("raw" + (Path(filename or "").suffix or ".bin"))
    raw.write_bytes(audio_bytes)

    try:
        dur, trimmed = _prepare_ref(raw, d / "ref.wav")
    except Exception as e:
        _save_meta(name, state="error", error="Không đọc được file mẫu: %s" % e,
                   created=time.time(), source=filename)
        raise ValueError("Không đọc được file mẫu (%s). Hãy thử file mp3/wav/m4a khác." % e)

    user_text = (ref_text or "").strip()
    # Mau bi cat ngan thi script do nguoi dung nhap KHONG con khop audio nua ->
    # phai nghe lai dung doan da cat, neu khong giong se bi nhoe/lap tu.
    if user_text and not trimmed:
        (d / "ref.txt").write_text(user_text, encoding="utf-8")
        src = "người dùng"
    else:
        (d / "ref.txt").write_text("", encoding="utf-8")
        src = None

    _save_meta(name, created=time.time(), duration=dur, trimmed=trimmed,
               source=filename or None, ref_text_source=src, user_text=user_text or None,
               state="processing" if src is None else "ready", error=None)
    if user_text and trimmed:
        _save_meta(name, note="Mẫu dài hơn %.0fs nên đã cắt ngắn — tool tự nghe lại "
                              "để script khớp đúng đoạn được dùng."
                              % float(_cfg("CLONE_REF_MAX_SEC", 12.0)))

    if background:
        threading.Thread(target=_finish_profile, args=(name,), daemon=True).start()
    else:
        _finish_profile(name)
    return profile_info(name)


def _finish_profile(name):
    """Viec nen sau khi them giong: lay script mau + tao file nghe thu."""
    try:
        _ensure_ref_text(name)
        _save_meta(name, state="ready")
    except Exception as e:
        _save_meta(name, state="ready", error=str(e)[:300])
    try:
        make_sample(name)
    except Exception as e:
        _save_meta(name, sample_error=str(e)[:300])


def delete_profile(name):
    d = profile_dir(safe_name(name))
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def rename_profile(old, new):
    a, b = profile_dir(safe_name(old)), profile_dir(safe_name(new))
    if not a.exists():
        raise ValueError("Không có giọng '%s'." % old)
    if b.exists():
        raise ValueError("Đã có giọng tên '%s'." % safe_name(new))
    a.rename(b)
    return safe_name(new)


# --------------------------------------------------------- chuan hoa mau giong

def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run_ffmpeg(args, data=None):
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin"] + args,
                       input=data, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace").strip()[-300:]
                           or "ffmpeg lỗi")
    return r.stdout


def _wav_seconds(p):
    b = Path(p).read_bytes() if not isinstance(p, (bytes, bytearray)) else p
    try:
        sr, _ch, samples = _wav_read(b)
        return len(samples) / float(sr or 1)
    except Exception:
        return 0.0


def _prepare_ref(src, dst):
    """Mau giong -> wav 24kHz mono s16, bo im lang, gioi han CLONE_REF_MAX_SEC.

    Tra ve (do_dai_giay, da_bi_cat_ngan).
    """
    sr = int(_cfg("CLONE_SR", 24000))
    max_sec = float(_cfg("CLONE_REF_MAX_SEC", 12.0))
    filters = ["highpass=f=60"]
    if _opt("denoise_ref", "CLONE_REF_DENOISE", False):
        filters.append("afftdn=nf=-25")
    filters.append("silenceremove=start_periods=1:start_silence=0.05:"
                   "start_threshold=-45dB:stop_periods=-1:stop_silence=0.3:"
                   "stop_threshold=-45dB:detection=peak")
    base = ["-i", str(src), "-vn", "-af", ",".join(filters),
            "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", "-f", "wav"]

    try:
        data = _run_ffmpeg(base + ["pipe:1"])
    except RuntimeError:
        # mau la dinh dang la: bo het filter, chi chuyen he
        data = _run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", str(sr),
                            "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"])

    dur = _wav_seconds(data)
    trimmed = False
    if dur > max_sec + 0.3:
        data = _run_ffmpeg(["-f", "wav", "-i", "pipe:0", "-t", "%.2f" % max_sec,
                            "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le",
                            "-f", "wav", "pipe:1"], data=data)
        dur = _wav_seconds(data)
        trimmed = True
    if dur < 0.5:
        raise RuntimeError("mẫu không có tiếng nói nghe được")
    Path(dst).write_bytes(data)
    return dur, trimmed


def _ensure_ref_text(name):
    """Bao dam co script mau dung voi ref.wav — tu nghe lai bang Whisper neu thieu."""
    d = profile_dir(name)
    ref_txt = d / "ref.txt"
    cur = ""
    try:
        cur = ref_txt.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    if cur:
        return cur
    ref = d / "ref.wav"
    if not ref.exists():
        raise RuntimeError("Thiếu file mẫu ref.wav.")

    from . import engine
    errs = []
    for model_name in (str(_cfg("CLONE_REF_TRANSCRIBE_MODEL", "small")), "tiny"):
        try:
            res = engine.transcribe(engine.get_model(model_name), ref)
            text = (res.get("text") or "").strip()
            if text:
                ref_txt.write_text(text, encoding="utf-8")
                _save_meta(name, ref_text_source="tự nghe (Whisper %s)" % model_name)
                return text
            errs.append("%s: không nghe ra chữ nào" % model_name)
        except Exception as e:
            errs.append("%s: %s" % (model_name, e))
    # khong nghe ra duoc: van con duong lui la cau nguoi dung da nhap
    fallback = (profile_meta(name).get("user_text") or "").strip()
    if fallback:
        ref_txt.write_text(fallback, encoding="utf-8")
        _save_meta(name, ref_text_source="người dùng (chưa đối chiếu lại được)")
        return fallback
    raise RuntimeError("Không tự lấy được script mẫu (%s). Vào /admin gõ đúng câu "
                       "trong mẫu rồi tạo lại giọng." % "; ".join(errs[:2]))


SAMPLE_TEXT = ("Xin chào, đây là giọng đọc được nhân bản. "
               "Một, hai, ba, bốn, năm. Chúc bạn một ngày tốt lành.")


def make_sample(name, text=None):
    """Tao file nghe thu cho 1 giong (ghi data/voice_profiles/<ten>/sample.wav)."""
    clips = synth_cues_clone([text or SAMPLE_TEXT], name)
    if not clips or not clips[0]:
        raise RuntimeError("không tạo được audio mẫu")
    out = profile_dir(name) / "sample.wav"
    out.write_bytes(clips[0])
    _save_meta(name, sample_at=time.time(), sample_error=None)
    return out


# ============================================================== worker

_wlock = threading.RLock()
_W = {"proc": None, "port": None, "started": 0.0}


def _ping(port, timeout=2.0):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/ping" % port, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return d if d.get("app") == WORKER_APP else None
    except Exception:
        return None


def _ports():
    base = int(_cfg("CLONE_WORKER_PORT", 8081))
    return range(base, base + int(_cfg("CLONE_WORKER_PORT_TRIES", 10)))


def _port_free(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) != 0


def worker_info():
    """Worker dang song? (khong khoi dong gi ca — chi xem).

    Do ca dai cong de nhan ra worker con song tu lan chay server truoc.
    """
    cands = ([_W["port"]] if _W["port"] else []) + [p for p in _ports() if p != _W["port"]]
    for port in cands:
        if not _port_free(port):
            d = _ping(port, 1.0)
            if d:
                _W["port"] = port
                return {"running": True, "port": port, "device": d.get("device"),
                        "busy": d.get("busy"), "pid": d.get("pid")}
    return {"running": False, "port": None, "device": None}


def stop_worker():
    with _wlock:
        port, proc = _W["port"], _W["proc"]
        if port:
            try:
                urllib.request.urlopen(urllib.request.Request(
                    "http://127.0.0.1:%d/shutdown" % port, data=b"{}"), timeout=3).read()
            except Exception:
                pass
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _W.update(proc=None, port=None)
        return True


def ensure_worker(log=None):
    """Tra ve cong cua worker dang san sang — tu khoi dong / hoi sinh khi can."""
    say = log or (lambda *_a: None)
    with _wlock:
        if _W["port"] and _ping(_W["port"]):
            if not _W["proc"] or _W["proc"].poll() is None:
                return _W["port"]
        for p in _ports():                      # worker con song tu lan chay truoc
            if _ping(p):
                _W["port"] = p
                return p

        py, err = engine_python()
        if not py:
            raise RuntimeError(err or _SETUP_HINT)

        port = next((p for p in _ports() if _port_free(p)), None)
        if port is None:
            raise RuntimeError("Không còn cổng trống cho worker nhân bản giọng "
                               "(đã thử %s)." % list(_ports())[:3])

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logf = open(LOG_DIR / "clone_worker.log", "ab", buffering=0)
        logf.write(("\n==== khoi dong %s (port %d) ====\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), port)).encode())
        env = os.environ.copy()
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        idle = float(_cfg("CLONE_WORKER_IDLE_EXIT", 0) or 0)
        if idle > 0:
            env["CLONE_IDLE_EXIT"] = str(idle)
        say("Khởi động engine nhân bản giọng (cổng %d)…" % port)
        proc = subprocess.Popen([py, "-u", str(WORKER), str(port)],
                                stdout=logf, stderr=logf, env=env)
        _W.update(proc=proc, port=port, started=time.time())

        timeout = float(_cfg("CLONE_WORKER_BOOT_TIMEOUT", 300))
        t0 = time.time()
        while time.time() - t0 < timeout:
            if proc.poll() is not None:
                _W.update(proc=None, port=None)
                raise RuntimeError("Engine nhân bản giọng tắt ngay khi khởi động "
                                   "(mã %s).\n%s" % (proc.returncode, _log_hint()))
            if _ping(port, 2.0):
                say("Engine đã sẵn sàng sau %.0fs." % (time.time() - t0))
                return port
            time.sleep(1.0)
        stop_worker()
        raise RuntimeError("Engine nhân bản giọng không khởi động nổi sau %.0fs.\n%s"
                           % (timeout, _log_hint()))


def _log_hint():
    tail = log_tail(8)
    base = "Xem chi tiết: %s" % (LOG_DIR / "clone_worker.log")
    return (base + "\n" + "\n".join(tail)) if tail else base


def warm_up(log=None):
    """Nap san model vao RAM (bam tu /admin) — lan long tieng dau se nhanh hon."""
    port = ensure_worker(log=log)
    d = _ping(port, 5.0) or {}
    return {"port": port, "device": d.get("device")}


# ============================================================== hau ky audio

def _wav_read(b):
    """wav bytes -> (sample_rate, kenh, array('h') mono int16). Ho tro PCM16/float32."""
    if len(b) < 44 or b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        raise ValueError("không phải WAV")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(b):
        cid = b[pos:pos + 4]
        size = struct.unpack("<I", b[pos + 4:pos + 8])[0]
        body = b[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            data = body
            if size == 0 or pos + 8 + size > len(b):     # header ghi thieu do ghi stream
                data = b[pos + 8:]
            break
        pos += 8 + size + (size & 1)
    if not fmt or data is None:
        raise ValueError("WAV thiếu chunk")
    tag, nch, sr = struct.unpack("<HHI", fmt[:8])
    bits = struct.unpack("<H", fmt[14:16])[0] if len(fmt) >= 16 else 16

    if tag == 1 and bits == 16:
        a = array("h")
        a.frombytes(data[:len(data) - (len(data) % 2)])
    elif tag == 3 and bits == 32:
        f = array("f")
        f.frombytes(data[:len(data) - (len(data) % 4)])
        a = array("h", (max(-32768, min(32767, int(x * 32767.0))) for x in f))
    else:
        raise ValueError("định dạng WAV %d/%dbit chưa hỗ trợ" % (tag, bits))

    if nch > 1:                                  # tron ve mono
        mono = array("h", [0]) * (len(a) // nch)
        for i in range(len(mono)):
            mono[i] = int(sum(a[i * nch:(i + 1) * nch]) / nch)
        a = mono
    return sr, 1, a


def _wav_write(sr, samples):
    raw = samples.tobytes()
    return (b"RIFF" + struct.pack("<I", 36 + len(raw)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
            + b"data" + struct.pack("<I", len(raw)) + raw)


def _post_process(wav_bytes):
    """Cat im lang dau/cuoi + can bang am luong + fade — tra ve wav 24k mono s16."""
    sr_target = int(_cfg("CLONE_SR", 24000))
    try:
        sr, _ch, a = _wav_read(wav_bytes)
    except Exception:
        return wav_bytes
    if sr != sr_target:
        try:
            wav_bytes = _run_ffmpeg(["-f", "wav", "-i", "pipe:0", "-ac", "1",
                                     "-ar", str(sr_target), "-c:a", "pcm_s16le",
                                     "-f", "wav", "pipe:1"], data=wav_bytes)
            sr, _ch, a = _wav_read(wav_bytes)
        except Exception:
            return wav_bytes
    if not len(a):
        return wav_bytes

    peak = max(max(a), -min(a)) or 1
    if _opt("trim", "CLONE_POST_TRIM", True):
        thr = max(int(0.004 * 32768), int(peak * 0.02))
        pad = int(sr * 0.03)
        i, j = 0, len(a) - 1
        while i < len(a) and abs(a[i]) < thr:
            i += 1
        while j > i and abs(a[j]) < thr:
            j -= 1
        if i < j:
            a = a[max(0, i - pad):min(len(a), j + pad)]
    if not len(a):
        return wav_bytes

    if _opt("normalize", "CLONE_POST_NORMALIZE", True):
        peak = max(max(a), -min(a)) or 1
        target = 32768.0 * (10 ** (float(_cfg("CLONE_POST_PEAK_DBFS", -1.5)) / 20.0))
        gain = target / peak
        max_gain = 10 ** (float(_cfg("CLONE_POST_MAX_GAIN_DB", 12.0)) / 20.0)
        gain = min(gain, max_gain)
        if abs(gain - 1.0) > 0.02:
            a = array("h", (max(-32768, min(32767, int(x * gain))) for x in a))

    fade = int(sr * float(_cfg("CLONE_POST_FADE_MS", 10)) / 1000.0)
    if fade > 1 and len(a) > fade * 2:
        for k in range(fade):
            f = k / float(fade)
            a[k] = int(a[k] * f)
            a[len(a) - 1 - k] = int(a[len(a) - 1 - k] * f)
    return _wav_write(sr, a)


# ============================================================== sinh giong

_FAST_TAGS = ("vui", "hào hứng", "gấp gáp", "tự hào", "wow", "ngạc nhiên")
_SLOW_TAGS = ("buồn", "mệt mỏi", "thở dài", "thì thầm", "bí ẩn", "lạnh lùng",
              "nghỉ", "nghỉ dài")


def _rate_to_speed(rate):
    try:
        if isinstance(rate, str):
            r = rate.strip()
            if r.endswith("%"):
                return max(0.3, min(2.0, 1.0 + float(r[:-1].replace("+", "")) / 100.0))
            return max(0.3, min(2.0, float(r)))
        return max(0.3, min(2.0, float(rate)))
    except Exception:
        return 1.0


def _plan(texts, base_speed):
    """Tach the cam xuc -> (van ban sach, toc do rieng tung doan). GIU DUNG chi so."""
    out = []
    for t in texts:
        raw = t or ""
        low = raw.lower()
        speed = base_speed
        if "[nghỉ]" in low or "[nghỉ dài]" in low:
            raw += "..."
        if any(("[%s]" % k) in low for k in ("vui", "hào hứng", "tự hào")):
            if not raw.rstrip().endswith("!"):
                raw = raw.rstrip() + "!"
        if any(("[%s]" % k) in low for k in ("buồn", "mệt mỏi", "thở dài", "sợ hãi")):
            if not raw.rstrip().endswith("..."):
                raw = raw.rstrip() + "..."
        if "[gấp gáp]" in low:
            raw = raw.replace(",", "")
        if any(("[%s]" % k) in low for k in _FAST_TAGS):
            speed *= 1.12
        elif any(("[%s]" % k) in low for k in _SLOW_TAGS):
            speed *= 0.9
        clean = re.sub(r"\[.*?\]", "", raw)
        clean = re.sub(r"\*.*?\*", "", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        out.append((clean, round(max(0.3, min(2.0, speed)), 3)))
    return out


def _is_vietnamese(text):
    vi = set("àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ")
    return any(c in vi for c in (text or "").lower())


def _request(port, payload, out_dir, n_expected, progress=None, timeout=7200):
    """Goi worker, vua cho vua bao tien do theo so file da ghi xong."""
    result = {}
    err = [None]

    def call():
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/infer" % port,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                result.update(json.loads(r.read().decode("utf-8")))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
                err[0] = body.get("error") or str(e)
            except Exception:
                err[0] = str(e)
        except Exception as e:
            err[0] = str(e)

    th = threading.Thread(target=call, daemon=True)
    th.start()
    last = -1
    while th.is_alive():
        time.sleep(0.8)
        done = len(list(out_dir.glob("[0-9]*.wav"))) if out_dir.is_dir() else 0
        if progress and done != last:
            last = done
            progress(min(done, n_expected), n_expected)
    th.join()
    if err[0]:
        raise RuntimeError(err[0])
    return result


def synth_cues_clone(texts, voice_id, rate=1.0, progress=None, log=None):
    """Doc tung doan bang giong nhan ban. Tra ve list bytes wav DUNG so doan.

    Doan trong -> b"" (giu im lang dung cho). Doan loi -> doc lai rieng, con loi
    tiep thi b"" va ghi canh bao, chi bao that bai khi loi qua nhieu.
    """
    say = log or (lambda *_a: None)
    name = safe_name(voice_id)
    d = profile_dir(name)
    ref_audio, ref_file = d / "ref.wav", d / "ref.txt"
    if not ref_audio.exists():
        raise RuntimeError("Không tìm thấy giọng nhân bản '%s'. Vào /admin tạo lại giọng."
                           % voice_id)

    n = len(texts)
    plan = _plan(texts, _rate_to_speed(rate))
    items = [{"i": i + 1, "text": t, "speed": sp}
             for i, (t, sp) in enumerate(plan) if t]
    if not items:
        return [b""] * n

    need_vi = _is_vietnamese(" ".join(t for t, _s in plan))
    if need_vi and not assets_ready():
        bad = "; ".join("%s: %s" % (a["label"], a["problem"])
                        for a in clone_assets.state() if not a["ok"])
        raise RuntimeError("Thiếu dữ liệu giọng Việt (%s).\nVào /admin bấm "
                           "\"Tải model giọng Việt\", hoặc chạy: python3 setup_clone.py" % bad)

    ref_text = _ensure_ref_text(name)
    st = get_settings()

    clips = [b""] * n
    with tempfile.TemporaryDirectory(prefix="clone_") as td:
        out_dir = Path(td)
        payload = {
            "ref_audio": str(ref_audio.absolute()),
            "ref_text": ref_text,
            "items": items,
            "out_dir": str(out_dir.absolute()),
            "ckpt": str(clone_assets.model_path().absolute()) if need_vi else "",
            "vocab": str(clone_assets.vocab_path().absolute()) if need_vi else "",
            "nfe_step": nfe_step(),
            "cfg_strength": float(_cfg("CLONE_CFG_STRENGTH", 2.0)),
            "sway": float(_cfg("CLONE_SWAY_SAMPLING", -1.0)),
            "cross_fade": float(_cfg("CLONE_CROSS_FADE", 0.12)),
            "target_rms": float(_cfg("CLONE_TARGET_RMS", 0.1)),
            "seed": int(_cfg("CLONE_SEED", -1)),
            "lowercase": bool(_opt("lowercase", "CLONE_LOWERCASE", True)),
            "remove_silence": False,
            "prefer_device": st.get("device") or _cfg("CLONE_DEVICE", "auto"),
            "device": (st.get("device") if st.get("device") in ("cpu", "cuda", "mps")
                       else None),
        }

        # Vong doc: doan nao loi thi doc lai RIENG doan do (ha xuong CPU). Worker
        # chet giua duong cung khong mat cong — khoi dong lai roi doc tiep phan con lai.
        pending = list(items)
        retries = max(0, int(_cfg("CLONE_RETRY", 1)))
        last_error = None
        for attempt in range(retries + 1):
            if not pending:
                break
            if attempt:
                say("Đọc lại %d đoạn bị lỗi (lần %d, dùng CPU)…" % (len(pending), attempt))
            req = dict(payload, items=pending)
            if attempt:
                req["device"] = "cpu"          # lan sau chac an hon toc do
            try:
                port = ensure_worker(log=say)
                res = _request(port, req, out_dir, len(pending),
                               progress=progress if attempt == 0 else None)
                bad = {f["i"] for f in (res.get("failed") or [])}
            except Exception as e:
                last_error = e
                say("Engine gặp sự cố: %s" % e)
                stop_worker()                  # hoi sinh o vong sau
                bad = {it["i"] for it in pending
                       if not (out_dir / ("%04d.wav" % it["i"])).exists()}
            pending = [it for it in pending if it["i"] in bad]

        from concurrent.futures import ThreadPoolExecutor

        def load(i):
            p = out_dir / ("%04d.wav" % (i + 1))
            if not p.exists() or p.stat().st_size < 200:
                return i, b""
            try:
                return i, _post_process(p.read_bytes())
            except Exception:
                return i, p.read_bytes()

        with ThreadPoolExecutor(max_workers=4) as ex:
            for i, b in ex.map(load, range(n)):
                clips[i] = b

    got = sum(1 for i, (t, _s) in enumerate(plan) if t and clips[i])
    want = len(items)
    if want and got == 0:
        raise RuntimeError("Giọng nhân bản không đọc được đoạn nào.%s\n%s"
                           % (("\n" + str(last_error)) if last_error else "", _log_hint()))
    if want and (want - got) > want * float(_cfg("CLONE_FAIL_RATIO", 0.35)):
        raise RuntimeError("Giọng nhân bản lỗi %d/%d đoạn — dừng để bạn kiểm tra.\n%s"
                           % (want - got, want, _log_hint()))
    if got < want:
        say("Cảnh báo: %d/%d đoạn không đọc được, giữ im lặng ở các đoạn đó." % (want - got, want))
    if progress:
        progress(n, n)
    return clips
