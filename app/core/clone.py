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
# Ban giao Windows: Python portable dong goi san torch + f5-tts nam canh tool
# (khong can cai dat, khong can mang) — luon duoc uu tien so 1.
ROOT = Path(__file__).resolve().parents[2]
PORTABLE_PY = ROOT / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
# PyTorch ban CUDA do setup_gpu.py tai ve (may co card NVIDIA). Co file .ok = da
# kiem tra chay that tren GPU. Worker nap no TRUOC ban CPU qua PYTHONPATH.
GPU_DIR = ROOT / "torch_gpu"


def gpu_ready():
    return (GPU_DIR / ".ok").exists()
WORKER_APP = "whisper_stt_clone_worker"

# tuong thich nguoc (cac module khac tung import truc tiep)
MODEL = clone_assets.model_path()
VOCAB = clone_assets.vocab_path()

_SETUP_HINT = ("Chưa cài engine nhân bản giọng.\n"
               "Chạy 1 lệnh này trong terminal tại thư mục tool:\n"
               "    python3 setup_clone.py\n"
               "(Windows: chạy lại Start-Windows.bat — nó tự gọi lệnh trên.)\n"
               "Bản giao Windows có sẵn thư mục python\\ (đã kèm PyTorch + F5-TTS):\n"
               "nếu thiếu thư mục này hãy giải nén lại ĐẦY ĐỦ file zip.")


# ============================================================== cai dat / thiet lap

QUALITY_NFE = {"nhanh": 16, "chuan": 32, "toida": 48}
_DEFAULT_SETTINGS = {
    "quality": None,        # nhanh | chuan | toida ; None = theo config
    "device": None,         # auto | cuda | mps | cpu
    "normalize": None,      # can bang am luong tung doan
    "trim": None,           # cat im lang dau/cuoi
    "lowercase": None,      # ha chu thuong truoc khi doc (model ViVoice)
    "denoise_ref": None,    # loc on mau giong
    "engine": None,         # auto | chatterbox | f5 | voxcpm (voxcpm: ca Anh + Viet)
    "separate": None,       # tach nhac nen (Demucs): auto | always | off
}


def cbx_ready():
    """Co du model Chatterbox (thu muc models_chatterbox) va thu vien chua."""
    for d in (ROOT / "models_chatterbox", ROOT / "data" / "chatterbox"):
        if all((d / f).exists() for f in ("t3_cfg.safetensors", "s3gen.safetensors",
                                          "ve.safetensors", "tokenizer.json")):
            return True
    return False


# VoxCPM2 (Apache-2.0, ca Anh + Viet): ban test dat san trong edition.json "clone_engine".
# Cai bang setup_voxcpm.py / Cai-VoxCPM2.bat (thu vien voxcpm + model ~5GB vao models_voxcpm2).
VOX_DIR = ROOT / "models_voxcpm2"
VOX_FILES = ("model.safetensors", "audiovae.pth", "config.json", "tokenizer.json")
_vox_cache = {"py": None, "ok": False, "at": 0.0}


def vox_model_ready():
    return all((VOX_DIR / f).exists() for f in VOX_FILES)


def vox_ready(recheck=False):
    """Co model VoxCPM2 + Python engine import duoc thu vien voxcpm (kiem tra 1 lan / 10 phut)."""
    if not vox_model_ready():
        return False
    py, _err = engine_python()
    if not py:
        return False
    ttl = 600 if _vox_cache["ok"] else 30     # vua cai xong -> nhan ra trong ~30 giay
    if not recheck and _vox_cache["py"] == py and time.time() - _vox_cache["at"] < ttl:
        return _vox_cache["ok"]
    try:
        r = subprocess.run([py, "-c", "import voxcpm"], capture_output=True, text=True, timeout=300)
        ok = r.returncode == 0
    except Exception:
        ok = False
    _vox_cache.update(py=py, ok=ok, at=time.time())
    return ok


def _want_engine():
    return (get_settings().get("engine") or _cfg("CLONE_ENGINE", "auto") or "auto").lower()


def engine_for(english=True):
    """'voxcpm' (Anh + Viet) khi da chon va da cai; con lai theo _base_engine_for."""
    if _want_engine() == "voxcpm" and vox_ready():
        return "voxcpm"
    return _base_engine_for(english)


def _base_engine_for(english=True):
    """'chatterbox' (tu nhien, co muc cam xuc) cho tieng Anh khi co model; con lai F5."""
    if not english:
        return "f5"
    want = _want_engine()
    if want == "f5" or not cbx_ready():
        return "f5"
    if want == "chatterbox":
        return "chatterbox"
    # auto: Chatterbox can GPU (CPU cham ~5-6 lan thoi gian thuc -> du an 30-40k ky tu qua lau)
    st = get_settings().get("device")
    return "chatterbox" if (gpu_ready() and st != "cpu") else "f5"


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
    if gpu_ready():                       # GPU du nhanh -> chat luong cao
        return int(_cfg("CLONE_NFE_STEP_GPU", 32))
    return int(_cfg("CLONE_NFE_STEP", 16))


# ============================================================== trang thai

def assets_ready():
    return clone_assets.ready()


_engine_cache = {"python": None, "ok": False, "error": None, "at": 0.0}
_engine_lock = threading.Lock()


def _python_candidates():
    win = os.name == "nt"
    sub = ("Scripts", "python.exe") if win else ("bin", "python")
    cands = []
    if PORTABLE_PY.exists():
        cands.append(str(PORTABLE_PY))
    cands.append(sys.executable)
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
        env = os.environ.copy()
        mpl_dir = os.path.join(tempfile.gettempdir(), "whisper_stt_mpl")
        os.makedirs(mpl_dir, exist_ok=True)
        env.setdefault("MPLCONFIGDIR", mpl_dir)
        r = subprocess.run([py, "-c", "import torch, f5_tts; print(torch.__version__)"],
                           capture_output=True, text=True, timeout=400, env=env)
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
        "profiles": [profile_info(p) for p in list_profiles(include_pending=True)],
        "ready": bool(py) and all(a["ok"] for a in assets),
        "can_make_styles": can_make_styles(),
        "vox": {"model": vox_model_ready(), "ready": vox_ready(recheck=recheck),
                "offer": _cfg("CLONE_ENGINE", "auto") == "voxcpm" or vox_model_ready(),
                "active": engine_for(True) == "voxcpm", "default": _cfg("CLONE_ENGINE", "auto")},
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


ENTR_EN_VOICES = ("ray", "adam", "brittney")
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".m4v", ".ts"}


def profile_lang(name):
    """Xac dinh ngon ngu cua giong clone ('vi' hoac 'en')."""
    m = profile_meta(name)
    lang = (m.get("lang") or "").strip().lower()
    if lang in ("vi", "en"):
        return lang
    d = profile_dir(name)
    try:
        txt = (d / "ref.txt").read_text(encoding="utf-8").strip()
        if txt:
            return "vi" if _is_vietnamese(txt) else "en"
    except Exception:
        pass
    user_txt = (m.get("user_text") or "").strip()
    if user_txt:
        return "vi" if _is_vietnamese(user_txt) else "en"
    return "en"


def list_profiles(include_all_en=False, include_pending=False):
    """Danh sach giong. Mac dinh BO giong nhan vien gui len con cho duyet."""
    CLONE_DIR.mkdir(parents=True, exist_ok=True)
    all_names = sorted(d.name for d in CLONE_DIR.iterdir()
                       if d.is_dir() and (d / "ref.wav").exists())
    if include_all_en:
        return all_names
    from .. import config as _c
    only = _c.EDITION.get("voices")                 # ban chi gom mot so giong
    if isinstance(only, list):
        all_names = [n for n in all_names if n in only or profile_meta(n).get("custom_added")]
    if _c.EDITION.get("lang_only"):                 # ban chi dung 1 ngon ngu (Starlight: tieng Viet)
        all_names = [n for n in all_names if profile_lang(n) == _c.EDITION["lang_only"]]
    en_cfg = _c.EDITION.get("en_voices")            # "all" = hien moi giong Anh
    if en_cfg == "all":
        return [n for n in all_names if include_pending or not is_pending(n)]
    keep_en = set(en_cfg if isinstance(en_cfg, list)
                  else (_cfg("ENTERPRISE_EN_VOICES", ENTR_EN_VOICES) or ENTR_EN_VOICES))
    out = []
    for n in all_names:
        if not include_pending and is_pending(n):
            continue
        if n in keep_en or profile_lang(n) == "vi" or profile_meta(n).get("custom_added"):
            out.append(n)
    return out


# ---- duyet giong: nhan vien gui tu may tram -> "pending", Quan tri tren may chu duyet ----

def is_pending(name):
    """Giong dang cho duyet (meta cu khong co 'approval' = da duyet, tuong thich nguoc)."""
    return profile_meta(safe_name(name)).get("approval") == "pending"


def pending_count(owner):
    """So giong 1 nhan vien dang gui cho duyet (chong gui tran o dia)."""
    CLONE_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for d in CLONE_DIR.iterdir():
        if d.is_dir():
            m = profile_meta(d.name)
            if m.get("approval") == "pending" and m.get("created_by") == owner:
                n += 1
    return n


def approve_profile(name, approved_by=None):
    name = safe_name(name)
    if not (profile_dir(name) / "ref.wav").exists():
        raise ValueError("Không có giọng '%s'." % name)
    if not is_pending(name):
        raise ValueError("Giọng '%s' không ở trạng thái chờ duyệt." % name)
    if profile_meta(name).get("state") == "processing":
        raise ValueError("Giọng '%s' đang được xử lý — đợi xong rồi hãy duyệt." % name)
    _save_meta(name, approval="approved", approved_by=approved_by or None,
               approved_at=time.time())
    return profile_info(name)


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
    tmp = p.with_name(p.name + ".tmp")     # ghi tam roi doi ten: tat ngang khong hong file
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
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
    vs = m.get("voice_sim")
    if vs is not None and vs < float(_cfg("CLONE_SIM_WARN", 0.80)):
        warn.append("⚠ giống giọng gốc chỉ %d%% — nên dùng file mẫu DÀI 1-3 phút, sạch, "
                    "1 người nói để tool chọn được đoạn giống nhất" % round(vs * 100))
    sep = m.get("separated") or {}
    if sep.get("used") and m.get("ref_snr") is not None and m["ref_snr"] < 5:   # giong doc nhanh it cho lang: 5-7 dB van sach
        warn.append("mẫu còn sót nhạc nền / méo sau khi tách (SNR %.0f dB) — nên dùng file sạch hơn"
                    % m["ref_snr"])
    sm = m.get("sample_match")
    if sm is not None and 0.85 <= sm < float(_cfg("CLONE_SAMPLE_MATCH_MIN", 0.95)):
        warn.append("đọc thử đúng %d%% (chưa đạt %d%%) — nghe lại bản thử trước khi dùng"
                    % (sm * 100, float(_cfg("CLONE_SAMPLE_MATCH_MIN", 0.95)) * 100))
    if sm is not None and sm < 0.85:
        warn.append("⚠ đọc thử chỉ đúng %d%% — mẫu có thể lẫn nhạc/tạp âm, quá ngắn, "
                    "nhiều người nói hoặc script sai: NÊN THAY MẪU KHÁC (6–11 giây, giọng rõ)"
                    % (sm * 100))
    return {
        "name": name,
        "lang": profile_lang(name),
        "duration": round(dur, 2) if dur else None,
        "ref_text": txt,
        "ref_text_source": m.get("ref_text_source"),
        "state": m.get("state", "ready"),
        "error": m.get("error"),
        "created": m.get("created"),
        "created_by": m.get("created_by"),
        "approval": m.get("approval") or "approved",
        "approved_by": m.get("approved_by"),
        "approved_at": m.get("approved_at"),
        "source": m.get("source"),
        "from_video": bool(m.get("from_video")),
        "trim_range": m.get("trim_range"),
        "sample_match": m.get("sample_match"),
        "voice_sim": m.get("voice_sim"),
        "prosody_match": m.get("prosody_match"),
        "styles_auto": m.get("styles_auto") or {},
        "styles_method": m.get("styles_method") or {},
        "style_labels": STYLE_LABELS,
        "trimmed": bool(m.get("trimmed")),
        "has_sample": (d / "sample.wav").exists() or (d / "test.mp3").exists(),
        "styles": sorted(available_styles(name)),
        "from_long": (d / "source.wav").exists(),
        "stage": m.get("stage"),
        "separated": m.get("separated"),
        "user_text_check": m.get("user_text_check"),
        "quality_ok": m.get("quality_ok"),
        "warnings": warn,
    }


def sample_path(name):
    """File nghe thu cua giong (None neu chua co)."""
    for fn in ("sample.wav", "test.mp3"):
        p = profile_dir(name) / fn
        if p.exists() and p.stat().st_size > 100:
            return p
    return None


def add_profile(name, audio_bytes, ref_text="", filename=None, background=True,
                trim_start=None, trim_end=None, denoise=None, lang=None, created_by=None,
                approval="approved"):
    """Them/ghi de 1 giong mau (tu file Audio hoac Video).

    Mau duoc chuan hoa ngay (24kHz mono, cat theo khoang giay trim_start..trim_end neu co,
    loc nhac nen/tap am neu tu video, bo im lang, toi da 12 giay). Viec cham
    (nghe lai lay script + tach mau cam xuc + tao file nghe thu) chay nen.
    """
    name = safe_name(name)
    d = profile_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    for old in ("source.wav", "sample.wav", "test.mp3", "meta.json", "vocals.wav"):
        try:
            (d / old).unlink()
        except OSError:
            pass
    shutil.rmtree(d / "styles", ignore_errors=True)
    for old in d.glob("raw.*"):
        old.unlink()
    ext = (Path(filename or "").suffix or ".bin").lower()
    from_video = ext in _VIDEO_EXTS
    raw = d / ("raw" + ext)
    raw.write_bytes(audio_bytes)

    use_denoise = bool(from_video) if denoise is None else bool(denoise)
    try:
        dur, trimmed = _prepare_ref(raw, d / "ref.wav", trim_start=trim_start,
                                    trim_end=trim_end, denoise=use_denoise)
    except Exception as e:
        _save_meta(name, state="error", error="Không đọc được file mẫu: %s" % e,
                   created=time.time(), source=filename, custom_added=True,
                   created_by=created_by or None,
                   approval="pending" if approval == "pending" else "approved")
        raise ValueError("Không đọc được file mẫu (%s). Hãy thử file video/audio khác." % e)

    user_text = (ref_text or "").strip()
    norm_lang = (lang or "").strip().lower()
    if norm_lang not in ("vi", "en"):
        norm_lang = "vi" if (not user_text or _is_vietnamese(user_text)) else "en"

    # Mau bi cat ngan thi script do nguoi dung nhap KHONG con khop audio nua ->
    # phai nghe lai dung doan da cat, neu khong giong se bi nhoe/lap tu.
    if user_text and not trimmed:
        (d / "ref.txt").write_text(user_text, encoding="utf-8")
        src = "người dùng"
    else:
        (d / "ref.txt").write_text("", encoding="utf-8")
        src = None

    trim_info = None
    if trim_start is not None or trim_end is not None:
        trim_info = [round(float(trim_start or 0), 1),
                     round(float(trim_end), 1) if trim_end else None]

    _save_meta(name, created=time.time(), created_by=created_by or None,
               approval="pending" if approval == "pending" else "approved",
               lang=norm_lang, custom_added=True, from_video=from_video,
               trim_range=trim_info, duration=dur, trimmed=trimmed,
               source=filename or None, ref_text_source=src, user_text=user_text or None,
               separate_req=denoise, state="processing", error=None,
               stage="đang tách nhạc nền…" if _separate_mode(denoise) != "off" else None)
    if user_text and trimmed:
        _save_meta(name, note="Mẫu dài hơn %.0fs nên đã tự chọn đoạn chuẩn — tool tự nghe lại "
                              "để script khớp đúng đoạn được dùng."
                              % float(_cfg("CLONE_REF_MAX_SEC", 12.0)))

    if background:
        threading.Thread(target=_finish_profile, args=(name,), daemon=True).start()
    else:
        _finish_profile(name)
    return profile_info(name)


# ------------------------------------------------- tach nhac nen (Demucs)

_sep_cache = {"py": None, "ok": False, "at": 0.0}


def _separate_mode(req=None):
    """'off' | 'auto' | 'always'. req: o "Khu nhac nen" luc them giong (False = nguoi dung tat)."""
    if req is False:
        return "off"
    mode = str(_opt("separate", "CLONE_REF_SEPARATE", "auto") or "auto").lower()
    return mode if mode in ("off", "auto", "always") else "auto"


def _engine_env():
    """Env cho tien trinh phu chay bang Python engine (GPU torch neu da bat)."""
    env = os.environ.copy()
    env.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "whisper_stt_mpl"))
    env["PYTHONUNBUFFERED"] = "1"
    if gpu_ready() and (GPU_DIR / "torch").is_dir():
        env["PYTHONPATH"] = str(GPU_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _vocalsep_cmd(*args):
    from . import vocalsep
    py, _err = engine_python()
    if not py:
        return None
    return [py, str(Path(vocalsep.__file__).resolve())] + [str(a) for a in args]


def separator_ready(install=True):
    """Python engine co thu vien demucs chua — chua co thi tu cai 1 lan (--no-deps)."""
    cmd = _vocalsep_cmd("--install" if install else "--check")
    if not cmd:
        return False
    if _sep_cache["py"] == cmd[0] and (_sep_cache["ok"] or time.time() - _sep_cache["at"] < 600):
        return _sep_cache["ok"]
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=_engine_env(), **kw)
        ok = r.returncode == 0
    except Exception:
        ok = False
    _sep_cache.update(py=cmd[0], ok=ok, at=time.time())
    return ok


def _decode(src, out, sr, ch, trim=None):
    ta = []
    if trim:
        ts, te = trim
        if ts:
            ta += ["-ss", "%.2f" % float(ts)]
        if te and float(te) > float(ts or 0):
            ta += ["-to", "%.2f" % float(te)]
    _run_ffmpeg(["-y"] + ta + ["-i", str(src), "-vn", "-ac", str(ch), "-ar", str(sr),
                               "-c:a", "pcm_s16le", str(out)])


def _bg_floor_db(wav):
    """Muc nen trong khoang nghi so voi giong (dB): ~-60 = phong thu sach, > -40 = co nhac/on."""
    import numpy as np
    import soundfile as sf
    y, _sr = sf.read(str(wav), dtype="float32", always_2d=True)
    y = y.mean(1)
    fr = 320
    n = len(y) // fr
    if n < 50:
        return 0.0
    db = 20 * np.log10(np.sqrt((y[:n * fr].reshape(n, fr) ** 2).mean(1)) + 1e-6)
    return float(np.percentile(db, 10) - np.percentile(db, 95))


def _run_vocalsep(src, dst, dur):
    """-> dict ket qua cua vocalsep.py (tien trinh rieng; GPU vai giay, CPU ~3-5x thoi luong)."""
    dev = get_settings().get("device")
    cmd = _vocalsep_cmd("--in", src, "--out", dst, "--sr", int(_cfg("CLONE_SR", 24000)),
                        "--model", _cfg("CLONE_SEPARATE_MODEL", "htdemucs_ft"),
                        "--device", dev if dev in ("cuda", "cpu") else "auto")
    if not cmd:
        raise RuntimeError("chưa có Python engine")
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    timeout = int(min(3600, 300 + 15 * dur))
    last = None
    for env_extra in ({}, {"HF_ENDPOINT": "https://hf-mirror.com"}):   # mang chan HF -> mirror
        env = _engine_env()
        env.update(env_extra)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, **kw)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("{")]
        last = json.loads(lines[-1]) if lines else {
            "ok": False, "error": ((r.stderr or "").strip().splitlines() or ["không rõ"])[-1][:300]}
        if last.get("ok") or not re.search(r"(?i)connect|http|hub|resolve|timeout|offline|entrynotfound",
                                           last.get("error") or ""):
            break
    if last.get("ok"):
        return last
    raise RuntimeError(last.get("error") or "tách nhạc lỗi")


def _separate_music(name):
    """Tach nhac nen khoi file goc (Demucs) roi chuan hoa lai mau tu phan GIONG.

    Chay o buoc nen (sau add_profile). Mau sach (khoang nghi lang nhu phong thu) hoac do
    duoc nhac rat nho thi giu nguyen. Tra ve True neu mau da duoc thay bang ban tach."""
    d = profile_dir(name)
    m = profile_meta(name)
    mode = _separate_mode(m.get("separate_req"))
    raw = next(iter(sorted(d.glob("raw.*"))), None)
    if mode == "off" or raw is None:
        return False
    trim = m.get("trim_range")
    sep_in = d / "sep_in.wav"
    vocals = d / "vocals.wav"
    try:
        _decode(raw, sep_in, 44100, 2, trim)
        dur = _wav_seconds(sep_in)
        if mode == "auto" and not m.get("from_video"):
            try:
                floor = _bg_floor_db(sep_in)
            except Exception:
                floor = 0.0
            if floor < float(_cfg("CLONE_SEPARATE_CLEAN_FLOOR_DB", -55.0)):
                _save_meta(name, separated={"used": False, "floor_db": round(floor, 1),
                                            "why": "mẫu sạch"})
                return False
        if not separator_ready():
            _save_meta(name, separated={"used": False, "why": "chưa cài được thư viện tách nhạc"})
            return False
        info = _run_vocalsep(sep_in, vocals, dur)
    finally:
        try:
            sep_in.unlink()
        except OSError:
            pass
    use = mode == "always" or info["music_db"] > float(_cfg("CLONE_SEPARATE_MIN_MUSIC_DB", -25.0))
    sep = {"used": use, "music_db": info["music_db"], "device": info.get("device"),
           "seconds": info.get("seconds"), "model": info.get("model")}
    if not use:
        _save_meta(name, separated=dict(sep, why="nhạc nền rất nhỏ"))
        return False
    try:
        (d / "source.wav").unlink()
    except OSError:
        pass
    dur2, trimmed = _prepare_ref(vocals, d / "ref.wav", denoise=False)
    user = (m.get("user_text") or "").strip()
    if user and not trimmed:
        (d / "ref.txt").write_text(user, encoding="utf-8")
        src = "người dùng"
    else:
        (d / "ref.txt").write_text("", encoding="utf-8")
        src = None
    _save_meta(name, separated=sep, duration=dur2, trimmed=trimmed, ref_text_source=src)
    return True


def _transcribe_ref(audio_path, name=None, model_name=None):
    """Nghe file mau voi ngon ngu goi y cua profile ('vi' hoac 'en').
    Uu tien large-v3-turbo de bat chinh xac 100% tu tieng Viet ca khi mau co nhac nen.
    Che do 'nghe ky' (engine.transcribe careful): VAD, khong noi ngu canh, bo cau ao."""
    from . import engine
    pref = model_name or str(_cfg("CLONE_REF_TRANSCRIBE_MODEL", "large-v3-turbo"))
    lang_hint = profile_meta(name).get("lang") if name else None
    if lang_hint not in ("vi", "en"):
        lang_hint = None
    cands = [pref] + [m for m in ("large-v3-turbo", "small", "tiny") if m != pref]
    last_err = None
    for m_name in cands:
        try:
            return engine.transcribe(engine.get_model(m_name), audio_path, language=lang_hint,
                                     careful=True)
        except Exception as e:
            last_err = e
    raise last_err


def _check_user_text(name):
    """Script nguoi dung go KHONG khop mau (go nham, dan script cua mau khac) ->
    F5 doc sai / nuot chu / doc lan chu cua mau. Nghe lai bang Whisper; lech nhieu
    thi thay bang ban nghe duoc.

    KHONG ghi de khi ban nghe khong dang tin: la cau ao kinh dien (subscribe / dang ky kenh...),
    nghe ra qua it chu so voi do dai giong, hoac khong khuc nao khop loi go tay (Whisper doan
    bua tren giong doc nhanh / lan nhac) — loi go tay dung bi thay bang cau ao la loi nang nhat."""
    from . import engine
    d = profile_dir(name)
    try:
        user = (d / "ref.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return
    if not user or profile_meta(name).get("ref_text_source") != "người dùng":
        return
    try:
        res = _transcribe_ref(d / "ref.wav", name=name)
    except Exception:
        return
    heard = (res.get("text") or "").strip()
    heard_cmp = heard
    try:
        from .expressive import normalize_numbers_en, normalize_numbers_vi
        heard_cmp = (normalize_numbers_vi if _is_vietnamese(user) else normalize_numbers_en)(heard)
    except Exception:
        pass
    norm = lambda t: re.findall(r"\w+", engine._fold(t))
    a, b = norm(user), norm(heard_cmp)
    dropped = [x["text"] for x in res.get("dropped") or [] if engine.is_hallucination(x["text"], user)]

    def keep(why):
        _save_meta(name, user_text_check=why, user_text_heard=heard[:200] or None)

    if not b:
        return keep("bản nghe là câu ảo — giữ lời gõ tay" if dropped else "không nghe ra chữ")
    if engine.is_hallucination(heard, allow=user):
        return keep("bản nghe là câu ảo — giữ lời gõ tay")
    import difflib
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    if ratio >= 0.7:
        return keep("khớp %.0f%%" % (ratio * 100))
    # nghe ra qua it chu: giong doc nhanh / co nhac -> Whisper bo sot ca doan, KHONG phai go sai
    words = res.get("words") or []
    voiced = sum(max(0.0, float(w["end"]) - float(w["start"])) for w in words)
    dur = float(res.get("duration") or 0) or _wav_seconds(d / "ref.wav")
    if dropped or len(b) < 0.6 * len(a) and voiced < 0.6 * dur:
        return keep("bản nghe thiếu (%d/%d chữ) — giữ lời gõ tay" % (len(b), len(a)))
    # chu Whisper nghe LECH loi go tay ma chinh no cung khong chac (p thap) -> nghe nham, khong phai go sai
    def wnorm(t):
        try:
            from .expressive import normalize_numbers_vi
            t = normalize_numbers_vi(t) if re.search(r"\d", t) else t
        except Exception:
            pass
        return norm(t)
    hw = [(t, float(w.get("p", 1.0))) for w in words for t in wnorm(w["text"])]
    sm = difflib.SequenceMatcher(None, a, [t for t, _p in hw], autojunk=False)
    matched = {j for blk in sm.get_matching_blocks() for j in range(blk.b, blk.b + blk.size)}
    off = [p for j, (_t, p) in enumerate(hw) if j not in matched]
    if off and sum(off) / len(off) < 0.75:
        return keep("bản nghe không chắc ở chỗ lệch (p=%.2f, khớp %.0f%%) — giữ lời gõ tay"
                    % (sum(off) / len(off), ratio * 100))
    # tung khuc ~8 chu cua ban nghe: co khuc nao trung lap loi go tay khong?
    best = 0.0
    for i in range(0, max(1, len(b) - 3), 4):
        chunk = b[i:i + 8]
        sm = difflib.SequenceMatcher(None, a, chunk, autojunk=False)
        best = max(best, sum(blk.size for blk in sm.get_matching_blocks()) / float(len(chunk)))
    if best < 0.5:
        return keep("⚠ lời gõ tay khác hẳn bản nghe (%.0f%%) — giữ lời gõ tay, nên kiểm tra lại"
                    % (ratio * 100))
    (d / "ref.txt").write_text(heard, encoding="utf-8")
    _save_meta(name, ref_text_source="tự nghe (script gõ tay không khớp mẫu, %.0f%%)" % (ratio * 100),
               user_text=user, user_text_check="thay bằng bản nghe")


def _smart_ref(name):
    """File mau DAI (hoac Video): chon doan 6-11 giay hay nhat (dung ranh gioi cau, nhip vua,
    giong ro) lam mau chinh + doan hao hung / tram lam mau cam xuc."""
    d = profile_dir(name)
    src = d / "source.wav"
    if not src.exists():
        return False
    from . import refpick
    res = _transcribe_ref(src, name=name)
    got = refpick.pick(src, res.get("words") or [])
    if not got:
        return False
    y = got["y"]
    chosen = [w for w in (got["main"], got.get("hype"), got.get("calm"), got.get("alt1"),
                          got.get("alt2"), got.get("alt3")) if w]
    peak = max(float(abs(y[int(w["t0"] * refpick.SR):int(w["t1"] * refpick.SR)]).max()) for w in chosen)
    gain = 0.89 / max(peak, 1e-3)              # 1 he so chung -> giu dung chenh lech to/nho tu nhien
    refpick.write_clip(y, got["main"], d / "ref.wav", gain)
    (d / "ref.txt").write_text(got["main"]["text"], encoding="utf-8")
    styles = []
    for k in ("hype", "calm", "alt1", "alt2", "alt3"):
        w = got.get(k)
        if not w:
            continue
        sd = d / "styles" / k
        sd.mkdir(parents=True, exist_ok=True)
        refpick.write_clip(y, w, sd / "ref.wav", gain)
        (sd / "ref.txt").write_text(w["text"], encoding="utf-8")
        styles.append(k)
    m = got["main"]
    lang_det = "vi" if _is_vietnamese(m["text"]) else "en"
    used = [[round(got[k]["t0"], 2), round(got[k]["t1"], 2)] for k in styles if got.get(k)]
    cands = [{"t0": round(w["t0"], 2), "t1": round(w["t1"], 2), "text": w["text"],
              "dur": round(w["dur"], 2), "wpm": round(w["wpm"]), "score": w.get("score"),
              "words": w.get("words")}
             for w in (got.get("cands") or [])
             if not any(w["t0"] < b and w["t1"] > a for a, b in used)]   # khong trung mau cam xuc
    _save_meta(name, lang=lang_det, duration=round(m["dur"], 2),
               ref_text_source="tự chọn đoạn hay nhất từ file %.0fs" % (len(y) / refpick.SR),
               styles=styles, ref_wpm=round(m["wpm"]), ref_snr=round(m.get("snr", 10.0), 1),
               ref_foreign=m.get("foreign", 0),
               ref_window=[round(m["t0"], 2), round(m["t1"], 2)], ref_cands=cands)
    return True


# ------------------------------------------------- do GIONG GIONG (speaker similarity)

def _voicesim(refs, cands=(), windows=None):
    """Chay voicesim.py bang Python engine (co torch) trong tien trinh rieng.
    -> list diem 0..1 (cand truoc, window sau) hoac None neu khong do duoc."""
    from . import voicesim
    if not voicesim.available():
        return None
    py, _err = engine_python()
    if not py:
        return None
    cmd = [py, str(Path(voicesim.__file__).resolve()), "--ref"] + [str(r) for r in refs]
    if cands:
        cmd += ["--cand"] + [str(c) for c in cands]
    if windows:
        cmd += ["--windows", ";".join("%.2f,%.2f" % (a, b) for a, b in windows)]
    try:
        env = os.environ.copy()
        env.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "whisper_stt_mpl"))
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}   # khong bat cua so den
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, **kw)
        line = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("{")][-1]
        return json.loads(line)["sims"]
    except Exception:
        return None


def _sim_refs(name):
    """'Giong that' de so: ca file nguon dai neu co, khong thi mau chinh."""
    d = profile_dir(name)
    return [d / "source.wav"] if (d / "source.wav").exists() else [d / "ref.wav"]


def prosody_signature(name, refresh=False):
    """'Chu ky nhip dieu' cua giong THAT (file nguon dai neu co, khong thi mau chinh): cach nguoi
    do don nhan, ngat cum, len xuong giong. Ban clone nen GIONG chu ky nay (khong ep soi noi)."""
    m = profile_meta(name)
    if m.get("prosody_goc") and not refresh:
        return m["prosody_goc"]
    from . import prosody
    d = profile_dir(name)
    src = next((d / f for f in ("source.wav", "test.mp3", "ref.wav") if (d / f).exists()), None)
    try:
        sig = prosody.measure(src) if src else None
    except Exception:
        sig = None
    if sig:
        _save_meta(name, prosody_goc=sig)
    return sig


def prosody_match(name, wav_path=None):
    """Ban nghe thu (hoac wav_path) giong nhip dieu giong that bao nhieu (0..1); luu meta."""
    from . import prosody
    p = wav_path or sample_path(name)
    if not p:
        return None
    try:
        pc = prosody.closeness(prosody.measure(p), prosody_signature(name))
    except Exception:
        pc = None
    if pc is not None and wav_path is None:
        _save_meta(name, prosody_match=pc)
    return pc


def voice_similarity(name):
    """Do lai do giong giong cua ban nghe thu so voi giong that (luu meta)."""
    smp = sample_path(name)
    if not smp:
        return None
    got = _voicesim(_sim_refs(name), [smp])
    if not got:
        return None
    _save_meta(name, voice_sim=round(got[0], 4))
    return got[0]


# ------------------------------------------- TU TAO MAU CAM XUC (giong thieu mau)
# Giong Anh chi co 1 mau ngan (vd ban nghe thu) -> khong co doan "hao hung"/"tram" that
# de [vui]/[buon] doc bang. Chatterbox (GPU) giu nguyen chat giong goc nhung doc duoc
# co cam xuc -> tao 2 mau cam xuc, CHAM GIONG GIONG, chi nhan mau giong >= nguong.
# Mau luu trong ho so giong -> may CPU (F5) cung dung duoc.
STYLE_TEXTS_EN = {
    "hype": "Oh my gosh, this is absolutely incredible! I can't believe we actually pulled "
            "it off. This is the best day ever!",
    "calm": "Take a slow, deep breath, and let the quiet settle in. There's no rush tonight. "
            "Everything is going to be okay.",
}
STYLE_TEXTS_EN.update({
    # cau co NOI DUNG dung cam xuc -> Chatterbox doc buon / gian / thi tham THAT (khong chinh am)
    "sad": "I kept every letter she ever wrote me. Some nights I still read them... and I just miss "
           "her so much.",
    "angry": "No! I told you a hundred times, and you still did it anyway! I am done with this!",
    "soft": "Shh... come closer. Don't make a sound. Someone is standing right outside the door.",
    "surprise": "Wait... what? No way! You're telling me this actually works? That's incredible!",
    "serious": "Here are the facts. Every year, millions of people lose sleep, and it costs far "
               "more than most of us realize.",
    "warm": "Hey, it's okay. Take a slow breath. I'm right here with you, and everything is "
            "going to be just fine.",
})
STYLE_GEN_PARAMS = {"hype": (0.95, 0.3), "calm": (0.3, 0.5),        # (exaggeration, cfg)
                    "sad": (0.5, 0.5), "angry": (1.1, 0.3), "soft": (0.35, 0.5),
                    "surprise": (0.9, 0.3), "serious": (0.45, 0.5), "warm": (0.4, 0.5)}


def can_make_styles():
    """Can Chatterbox (model + thu vien) va dang dung GPU."""
    return cbx_ready() and _base_engine_for(True) == "chatterbox"


def _clean_style_wav(wav_bytes, out):
    """Bo lang dau/cuoi, chuan dinh 0.89, 24kHz mono — giong cach lam mau chinh."""
    import io
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if sr != 24000:
        import librosa
        y = librosa.resample(y, orig_sr=sr, target_sr=24000)
        sr = 24000
    nz = np.where(np.abs(y) > max(1e-4, float(np.abs(y).max()) * 0.02))[0]
    if len(nz):
        a = max(0, int(nz[0]) - int(0.05 * sr))
        b = min(len(y), int(nz[-1]) + int(0.12 * sr))
        y = y[a:b]
    y = y * (0.89 / max(1e-4, float(np.abs(y).max())))
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), y, sr, subtype="PCM_16")
    return len(y) / float(sr)


def make_emotion_styles(name, force=False, tries=3, say=None):
    """Tao styles/hype + styles/calm cho giong Anh bang Chatterbox, moi mau thu `tries` lan,
    giu ban GIONG GIONG GOC nhat va chi nhan khi >= CLONE_STYLE_MIN_SIM (mac dinh 0.85).
    -> {style: {"sim": x, "ok": bool, "note": "..."}}"""
    say = say or (lambda *_a: None)
    name = safe_name(name)
    d = profile_dir(name)
    if not (d / "ref.wav").exists():
        raise ValueError("Không có giọng '%s'." % name)
    if profile_lang(name) != "en":
        raise ValueError("Tự tạo mẫu cảm xúc chỉ hỗ trợ giọng tiếng Anh (Chatterbox không đọc "
                         "tiếng Việt). Giọng Việt: thêm lại từ file dài 1-3 phút có đoạn sôi "
                         "nổi và đoạn trầm.")
    if not can_make_styles():
        raise RuntimeError("Cần máy có GPU NVIDIA + model Chatterbox (models_chatterbox) để "
                           "tự tạo mẫu cảm xúc.")
    min_sim = float(_cfg("CLONE_STYLE_MIN_SIM", 0.85))
    have = available_styles(name)
    # mau tao bang BIEN DOI (luc may chua co Chatterbox) -> lam lai bang Chatterbox cho cam xuc that
    have = have - set(profile_meta(name).get("styles_method") or {})
    report, made = {}, []
    tmp = Path(tempfile.mkdtemp(prefix="styles_"))
    try:
        for sty, text in STYLE_TEXTS_EN.items():
            if sty in have and not force:
                report[sty] = {"sim": None, "ok": True, "note": "đã có mẫu thật — giữ nguyên"}
                continue
            ex, cfg = STYLE_GEN_PARAMS[sty]
            best = (None, None)
            for k in range(max(1, tries)):
                try:
                    wav = synth_cues_clone([text], name, styles=[""], exaggerations=[ex],
                                           cfgs=[cfg], engine="chatterbox")[0]
                except Exception as e:
                    say("  %s lần %d lỗi: %s" % (sty, k + 1, e))
                    continue
                if not wav:
                    continue
                f = tmp / ("%s_%d.wav" % (sty, k))
                _clean_style_wav(wav, f)
                got = _voicesim(_sim_refs(name), [f])
                sim = got[0] if got else None
                say("  %s lần %d: giống giọng %s" % (sty, k + 1, "?" if sim is None else "%.0f%%" % (sim * 100)))
                if sim is not None and (best[0] is None or sim > best[0]):
                    best = (sim, f)
            sim, f = best
            if sim is not None and sim >= min_sim:
                sd = d / "styles" / sty
                sd.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, sd / "ref.wav")
                (sd / "ref.txt").write_text(text, encoding="utf-8")
                made.append(sty)
                report[sty] = {"sim": round(sim, 4), "ok": True, "note": "đã tạo"}
            else:
                report[sty] = {"sim": None if sim is None else round(sim, 4), "ok": False,
                               "note": "bỏ — giống giọng dưới %d%%, giữ giọng gốc" % round(min_sim * 100)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if made:
        m = profile_meta(name)
        auto = dict(m.get("styles_auto") or {})
        auto.update({k: report[k]["sim"] for k in made})
        meth = {k: v for k, v in (m.get("styles_method") or {}).items() if k not in made}
        synth = sorted(set(m.get("styles_synth") or []) | set(made))
        _save_meta(name, styles=sorted(available_styles(name)), styles_auto=auto, styles_method=meth,
                   styles_synth=synth)
    return report


STYLE_LABELS = {"hype": "Hào hứng / vui", "calm": "Trầm / bình tĩnh", "sad": "Buồn",
                "angry": "Giận dữ", "soft": "Thì thầm / bí ẩn", "surprise": "Ngạc nhiên",
                "serious": "Nghiêm túc", "warm": "Ấm áp / dịu dàng"}


def add_style_sample(name, style, audio_bytes, filename=None, ref_text="", denoise=None):
    """TAI LEN MAU CAM XUC THAT cho 1 giong (nguoi long tieng thu 1 cau vui / buon / gian...).
    Cach chac chan nhat de giong Viet co cam xuc that (khong co Chatterbox tieng Viet).
    Mau phai CUNG NGUOI voi giong goc (>= CLONE_STYLE_UPLOAD_MIN, mac dinh 0.75)."""
    name = safe_name(name)
    d = profile_dir(name)
    if not (d / "ref.wav").exists():
        raise ValueError("Không có giọng '%s'." % name)
    if style not in STYLE_LABELS:
        raise ValueError("Loại mẫu cảm xúc không hợp lệ.")
    work = Path(tempfile.mkdtemp(prefix="style_up_"))
    try:
        ext = (Path(filename or "").suffix or ".bin").lower()
        raw = work / ("raw" + ext)
        raw.write_bytes(audio_bytes)
        out = work / "ref.wav"
        try:
            dur, _trim = _prepare_ref(raw, out, denoise=bool(ext in _VIDEO_EXTS) if denoise is None
                                      else bool(denoise))
        except Exception as e:
            raise ValueError("Không đọc được file mẫu (%s)." % e)
        if dur < 2.5:
            raise ValueError("Mẫu quá ngắn (%.1f giây) — cần 5–12 giây nói rõ cảm xúc." % dur)
        got = _voicesim(_sim_refs(name), [out])
        sim = got[0] if got else None
        floor = float(_cfg("CLONE_STYLE_UPLOAD_MIN", 0.75))
        if sim is not None and sim < floor:
            raise ValueError("Mẫu chỉ giống giọng '%s' %d%% (cần từ %d%%) — có thể là người khác, "
                             "lẫn nhạc nền hoặc quá ngắn." % (name, round(sim * 100), round(floor * 100)))
        txt = (ref_text or "").strip()
        if not txt:
            txt = (_transcribe_ref(out, name=name).get("text") or "").strip()
        if not txt:
            raise ValueError("Không nghe được lời trong mẫu — nhập lời thoại mẫu bằng tay.")
        sd = d / "styles" / style
        sd.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, sd / "ref.wav")
        (sd / "ref.txt").write_text(txt, encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    m = profile_meta(name)
    auto = dict(m.get("styles_auto") or {})
    if sim is not None:
        auto[style] = round(sim, 4)
    meth = dict(m.get("styles_method") or {})
    meth[style] = "tai_len"
    _save_meta(name, styles=sorted(available_styles(name)), styles_auto=auto, styles_method=meth)
    return {"style": style, "sim": None if sim is None else round(sim, 4), "text": txt,
            "duration": round(dur, 2)}


def needs_styles(name):
    """Giong can tao (lai) mau cam xuc: thieu hype/calm, hoac co mau BIEN DOI ma may da co
    Chatterbox (giong Anh) -> lam lai cho cam xuc that."""
    if not ({"hype", "calm"} <= available_styles(name)):
        return True
    if profile_lang(name) == "en" and can_make_styles():
        if set(EMO_STYLES) - available_styles(name):      # thieu mau buon / gian / thi tham
            return True
        meth = profile_meta(name).get("styles_method") or {}
        return any(v == "bien_doi" for v in meth.values())   # mau tai len: giu nguyen
    return False


# ---- cach 2 (MOI MAY, ca tieng Viet): BIEN DOI chinh mau goc thanh ban hao hung / tram.
# F5 bat chuoc cao do + nhip + do manh cua mau -> doc theo cam xuc cua mau da bien doi.
# Do that (adam / giong Viet): hao hung cao +10..23Hz, manh +3..7dB, nhanh hon;
# tram thap + nhe + cham hon. Moi muc CHAM TREN BAN DOC THAT, giu khi giong >= 80%.
# (cung, nhip, loc ffmpeg) — thu muc MANH truoc, khong dat thi muc NHE.
MORPH_LEVELS = {
    "hype": [(0.7, 1.08, ["compand=attacks=0.02:decays=0.15:points=-80/-80|-30/-24|-15/-9|0/-2",
                          "volume=2dB"]),
             (0.4, 1.05, ["compand=attacks=0.02:decays=0.2:points=-80/-80|-30/-26|-15/-11|0/-3",
                          "volume=1.5dB"])],
    "calm": [(-1.2, 0.88, ["volume=-4dB", "lowpass=f=6500"]),
             (-0.7, 0.92, ["volume=-3dB", "lowpass=f=7000"])],
}
MORPH_TEST_TEXT = {"en": "We finally made it, and honestly, it feels like a dream.",
                   "vi": "Cuối cùng chúng ta cũng làm được, cảm giác như một giấc mơ vậy."}


def _morph_ref(src, dst, semitones, tempo, extra):
    flt = []
    if abs(semitones) > 0.01:
        f = 2 ** (semitones / 12.0)
        flt += ["asetrate=%d" % int(24000 * f), "aresample=24000", "atempo=%.5f" % (1.0 / f)]
    if abs(tempo - 1.0) > 0.001:
        flt.append("atempo=%.4f" % tempo)
    flt += list(extra) + ["alimiter=limit=0.95:level=disabled"]
    out = _run_ffmpeg(["-i", str(src), "-af", ",".join(flt), "-ac", "1", "-ar", "24000",
                       "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"])
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(out)


def make_morph_styles(name, force=False, say=None):
    """Tao styles/hype + styles/calm bang cach BIEN DOI mau goc (khong can GPU, ca tieng Viet).
    Moi muc doc thu 1 cau bang mau moi, cham giong giong ban doc; giu khi >= CLONE_SIM_WARN."""
    say = say or (lambda *_a: None)
    name = safe_name(name)
    d = profile_dir(name)
    if not (d / "ref.wav").exists():
        raise ValueError("Không có giọng '%s'." % name)
    floor = float(_cfg("CLONE_SIM_WARN", 0.80))
    text = MORPH_TEST_TEXT["vi" if profile_lang(name) == "vi" else "en"]
    have = available_styles(name)
    report, made = {}, {}
    for sty, levels in MORPH_LEVELS.items():
        if sty in have and not force:
            report[sty] = {"sim": None, "ok": True, "note": "đã có mẫu — giữ nguyên"}
            continue
        sd = d / "styles" / sty
        best = None
        meth = dict(profile_meta(name).get("styles_method") or {})
        _save_meta(name, styles_method=dict(meth, **{sty: "bien_doi"}))   # doc thu dung nhip mau
        for li, (st, tempo, extra) in enumerate(levels):
            try:
                _morph_ref(d / "ref.wav", sd / "ref.wav", st, tempo, extra)
                shutil.copy2(d / "ref.txt", sd / "ref.txt")
                wav = synth_cues_clone([text], name, styles=[sty])[0]
                f = Path(tempfile.mkdtemp(prefix="morph_")) / "t.wav"
                f.write_bytes(wav)
                got = _voicesim(_sim_refs(name), [f])
                shutil.rmtree(f.parent, ignore_errors=True)
                sim = got[0] if got else None
            except Exception as e:
                say("  %s mức %d lỗi: %s" % (sty, li + 1, e))
                sim = None
            say("  %s (biến đổi mức %s): giống giọng %s" % (
                sty, "mạnh" if li == 0 else "nhẹ", "?" if sim is None else "%.0f%%" % (sim * 100)))
            if sim is not None and sim >= floor:
                best = (sim, li)
                break
        if best:
            made[sty] = best[0]
            report[sty] = {"sim": round(best[0], 4), "ok": True,
                           "note": "đã tạo (biến đổi mức %s)" % ("mạnh" if best[1] == 0 else "nhẹ")}
        else:
            shutil.rmtree(sd, ignore_errors=True)
            _save_meta(name, styles_method={k: v for k, v in meth.items() if k != sty})
            report[sty] = {"sim": None, "ok": False,
                           "note": "bỏ — bản đọc dưới %d%% giống giọng, giữ giọng gốc" % round(floor * 100)}
    if made:
        m = profile_meta(name)
        auto = dict(m.get("styles_auto") or {})
        auto.update({k: round(v, 4) for k, v in made.items()})
        _save_meta(name, styles=sorted(available_styles(name)), styles_auto=auto)
    return report


def make_styles_auto(name, force=False, say=None):
    """Tao mau cam xuc bang cach TOT NHAT co tren may: giong Anh + GPU -> Chatterbox;
    con lai (hoac Chatterbox khong dat) -> bien doi mau goc."""
    rep = {}
    if profile_lang(name) == "en" and can_make_styles():
        try:
            rep = make_emotion_styles(name, force=force, say=say)
        except Exception as e:
            (say or (lambda *_a: None))("  Chatterbox lỗi: %s -> dùng cách biến đổi mẫu" % e)
    if not rep or not all(r.get("ok") for r in rep.values()):
        rep2 = make_morph_styles(name, force=False, say=say)
        rep.update({k: v for k, v in rep2.items() if not (rep.get(k) or {}).get("ok")})
    return rep


def _improve_similarity(name, say=None):
    """Tu NANG DO GIONG: ban nghe thu duoi nguong -> thu cac doan mau ung vien khac
    trong file nguon, giu doan cho ban clone GIONG NHAT (ma van doc dung chu)."""
    say = say or (lambda *_a: None)
    d = profile_dir(name)
    base = voice_similarity(name)
    if base is None:
        return None
    target = float(_cfg("CLONE_SIM_TARGET", 0.88))
    p_target = float(_cfg("CLONE_PROSODY_TARGET", 0.75))
    base_pc = prosody_match(name)
    m = profile_meta(name)
    cands = m.get("ref_cands") or []
    m_min = float(_cfg("CLONE_SAMPLE_MATCH_MIN", 0.95))
    sim_ok = float(_cfg("CLONE_SIM_OK", 0.85))
    good = (base >= target and (base_pc is None or base_pc >= p_target)
            and (m.get("sample_match") is None or m["sample_match"] >= m_min))
    if good or not (d / "source.wav").exists() or len(cands) < 2:
        return base
    from . import refpick
    import numpy as np
    cur = m.get("ref_window") or []
    others = [c for c in cands if [c["t0"], c["t1"]] != cur]
    # uu tien doan ma CHINH MAU da giong tam giong nhat
    ws = _voicesim([d / "source.wav"], windows=[(c["t0"], c["t1"]) for c in others]) or []
    for c, sc in zip(others, ws):
        c["ref_sim"] = sc
    others.sort(key=lambda c: -(c.get("ref_sim") or 0))
    best = {"sim": base, "match": m.get("sample_match"), "pc": base_pc}
    # diem tong: giong giong la chinh, giong nhip dieu cong them (cung giong -> chon ban tu nhien hon)
    total = lambda sim, pc: sim + 0.3 * (pc if pc is not None else 0.5)
    y = refpick._load(d / "source.wav")
    bak = Path(tempfile.mkdtemp(prefix="simbak_"))
    try:
        floor = float(_cfg("CLONE_SIM_WARN", 0.80))
        n_try = max(0, int(_cfg("CLONE_SIM_TRIES", 2)))
        for idx, c in enumerate(others):
            # thu toi da n_try doan; con duoi 80% thi thu TIEP moi doan ung vien con lai
            if idx >= n_try and best["sim"] >= floor:
                break
            if (best["sim"] >= target and (best["pc"] or 0) >= p_target
                    and (best["match"] is None or best["match"] >= m_min)):
                break
            for f in ("ref.wav", "ref.txt", "sample.wav"):
                if (d / f).exists():
                    shutil.copy2(d / f, bak / f)
            seg = y[int(c["t0"] * refpick.SR):int(c["t1"] * refpick.SR)]
            refpick.write_clip(y, c, d / "ref.wav", 0.89 / max(1e-3, float(np.abs(seg).max())))
            (d / "ref.txt").write_text(c["text"], encoding="utf-8")
            try:
                make_sample(name)
                _sample_check(name)
                sim = voice_similarity(name)
                pc = prosody_match(name)
            except Exception:
                sim, pc = None, None
            match = profile_meta(name).get("sample_match")
            ok_text = match is None or best["match"] is None or match >= best["match"] - 0.05
            say("Thử đoạn mẫu %.1f-%.1fs: giống giọng %s, nhịp điệu %s" % (
                c["t0"], c["t1"], "?" if sim is None else "%.0f%%" % (sim * 100),
                "?" if pc is None else "%.0f%%" % (pc * 100)))
            # ban doc thu cua doan cu doc SAI chu (< 95%) ma doan nay doc dung + van giong giong -> lay
            fixes_text = (match is not None and match >= m_min and sim is not None and sim >= sim_ok
                          and (best["match"] or 0) < m_min)
            if fixes_text or (sim is not None and sim >= min(best["sim"], floor) and ok_text
                              and total(sim, pc) > total(best["sim"], best["pc"]) + 0.005):
                best = {"sim": sim, "match": match, "pc": pc}
                _save_meta(name, ref_window=[c["t0"], c["t1"]], duration=c["dur"],
                           ref_wpm=c["wpm"], ref_text_source="tự chọn đoạn GIỐNG GIỌNG + NHỊP "
                           "ĐIỆU nhất (%d%% / %s) từ file nguồn" % (
                               round(sim * 100), "?" if pc is None else "%d%%" % round(pc * 100)))
            else:                            # khong hon -> tra lai mau cu
                for f in ("ref.wav", "ref.txt", "sample.wav"):
                    if (bak / f).exists():
                        shutil.copy2(bak / f, d / f)
                _save_meta(name, voice_sim=round(best["sim"], 4), sample_match=best["match"],
                           prosody_match=best["pc"])
    finally:
        shutil.rmtree(bak, ignore_errors=True)
    return best["sim"]


def _finish_profile(name):
    """Viec nen sau khi them giong: lay script mau + tao file nghe thu."""
    err_msg = None
    try:
        _separate_music(name)
    except Exception as e:
        _save_meta(name, separate_error=str(e)[:300])
    _save_meta(name, stage=None)
    try:
        try:
            _smart_ref(name)
        except Exception as e:
            _save_meta(name, smart_ref_error=str(e)[:200])
        _check_user_text(name)
        txt = _ensure_ref_text(name)
        if txt:
            _save_meta(name, lang="vi" if _is_vietnamese(txt) else "en")
    except Exception as e:
        err_msg = str(e)[:300]
    try:
        make_sample(name)
        _auto_speed(name)
        _sample_check(name)
    except Exception as e:
        _save_meta(name, sample_error=str(e)[:300])
    try:
        _improve_similarity(name)          # cham + tu nang do giong giong (khong bat buoc)
    except Exception as e:
        _save_meta(name, sim_error=str(e)[:200])
    m = profile_meta(name)                 # dat = ban nghe thu DO MODEL DOC khop chu + giong giong
    if m.get("sample_match") is not None:
        _save_meta(name, quality_ok=bool(
            m["sample_match"] >= float(_cfg("CLONE_SAMPLE_MATCH_MIN", 0.95))
            and (m.get("voice_sim") is None or m["voice_sim"] >= float(_cfg("CLONE_SIM_OK", 0.85)))))
    try:                                   # chua co mau cam xuc -> tu tao (Chatterbox / bien doi)
        if not ({"hype", "calm"} <= available_styles(name)):
            make_styles_auto(name)
    except Exception as e:
        _save_meta(name, styles_error=str(e)[:200])
    finally:
        _save_meta(name, state="ready", error=err_msg)


def _sample_check(name):
    """Tu KIEM TRA giong moi: nghe lai file nghe thu bang Whisper, so voi cau mau.
    Doc sai nhieu = mau lan nhac/tap am, qua ngan, nhieu nguoi noi hoac script
    khong khop -> canh bao ngay trong /admin (thay vi de khach long ca kich ban)."""
    p = profile_dir(name) / "sample.wav"
    if not p.exists():
        return
    try:
        heard = (_transcribe_ref(p, name=name).get("text") or "").strip()
    except Exception:
        return
    import difflib
    target_txt = _sample_text_for(name)
    if _is_vietnamese(heard):
        try:
            from .expressive import normalize_numbers_vi
            heard_cmp = normalize_numbers_vi(heard)
        except Exception:
            heard_cmp = heard
    else:
        try:
            from .expressive import normalize_numbers_en
            heard_cmp = normalize_numbers_en(heard)
        except Exception:
            heard_cmp = heard
    a = re.findall(r"\w+", target_txt.lower())
    b = re.findall(r"\w+", heard_cmp.lower())
    ratio = difflib.SequenceMatcher(None, a, b).ratio() if b else 0.0
    _save_meta(name, sample_match=round(ratio, 3), sample_heard=heard[:200])


def _auto_speed(name, target_wpm=None):
    """Giong moi them: do toc do doc cua file nghe thu.
    Voi tieng Viet, clone_worker da tinh fix_duration chinh xac theo so am tiet va
    toc do phat am thuc cua mau -> giu speed=1.0 de khong bi keo gian / le the."""
    if "speed" in profile_meta(name):
        return
    is_vi = profile_lang(name) == "vi"
    if is_vi:
        # giong nguon doc QUA NHANH (quang cao) -> cham lai chut de ban clone co cho tho, khong nuot chu
        wpm = float(profile_meta(name).get("ref_wpm") or 0)
        fast = float(_cfg("CLONE_VI_FAST_WPM", 300))
        if wpm >= fast:
            _save_meta(name, speed=0.92, note="Mẫu đọc rất nhanh (%d âm tiết/phút) — tự giảm tốc độ "
                                              "0.92 để bản clone có chỗ thở, đọc rõ chữ." % wpm)
            make_sample(name)
            return
        _save_meta(name, speed=1.0, note="Tự cân nhịp theo âm tiết mẫu tiếng Việt (tốc độ chuẩn 1.00).")
        return
    p = profile_dir(name) / "sample.wav"
    try:
        dur = _wav_seconds(p.read_bytes())
    except Exception:
        return
    if dur <= 0.5:
        return
    s_txt = _sample_text_for(name)
    wpm = len(s_txt.split()) / dur * 60.0
    if target_wpm is None:
        target_wpm = 170.0
    # dich = NHIP THAT cua nguoi noi trong mau
    try:
        d = profile_dir(name)
        rw = len((d / "ref.txt").read_text(encoding="utf-8").split())
        rd = _wav_seconds((d / "ref.wav").read_bytes())
        if rw >= 8 and rd > 2:
            lo, hi = (140.0, 215.0)
            target_wpm = max(lo, min(hi, rw / rd * 60.0))
    except Exception:
        pass
    min_sp = 0.80
    sp = round(max(min_sp, min(1.0, target_wpm / wpm)), 2)
    _save_meta(name, speed=sp, note="Tự đo: đọc thử %d từ/phút, nhịp mẫu %d từ/phút -> tốc độ %.2f."
               % (wpm, target_wpm, sp))
    if sp < 0.97:
        make_sample(name)             # file nghe thu theo toc do moi


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


def _prepare_ref(src, dst, trim_start=None, trim_end=None, denoise=False):
    """Mau giong (Audio hoac Video) -> wav 24kHz mono s16, cat khoang giay neu chon,
    loc nhac nen/tap am, bo im lang, gioi han CLONE_REF_MAX_SEC.

    Tra ve (do_dai_giay, da_bi_cat_ngan).
    """
    sr = int(_cfg("CLONE_SR", 24000))
    max_sec = float(_cfg("CLONE_REF_MAX_SEC", 12.0))
    filters = ["highpass=f=75", "lowpass=f=8500"]
    if denoise or _opt("denoise_ref", "CLONE_REF_DENOISE", False):
        filters.append("afftdn=nf=-22")
    # chi rut ngan khoang lang DAI (> 0.7s con 0.45s) - giu nhip ngat tu nhien cua
    # nguoi noi (truoc day moi khoang lang bi ep con 0.3s -> mau doc doc, nhanh hon that)
    filters.append("silenceremove=start_periods=1:start_silence=0.05:"
                   "start_threshold=-45dB:stop_periods=-1:stop_duration=0.7:"
                   "stop_silence=0.45:stop_threshold=-45dB:detection=peak")
    time_args = []
    try:
        ts = float(trim_start) if trim_start is not None and str(trim_start).strip() != "" else 0.0
    except (TypeError, ValueError):
        ts = 0.0
    try:
        te = float(trim_end) if trim_end is not None and str(trim_end).strip() != "" else 0.0
    except (TypeError, ValueError):
        te = 0.0
    if ts > 0:
        time_args += ["-ss", "%.2f" % ts]
    if te > ts and te > 0:
        time_args += ["-to", "%.2f" % te]

    base = time_args + ["-i", str(src), "-vn", "-af", ",".join(filters),
                        "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", "-f", "wav"]

    try:
        data = _run_ffmpeg(base + ["pipe:1"])
    except RuntimeError:
        # mau la dinh dang la: bo het filter, chi chuyen he
        data = _run_ffmpeg(time_args + ["-i", str(src), "-vn", "-ac", "1", "-ar", str(sr),
                                        "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"])

    dur = _wav_seconds(data)
    trimmed = False
    if dur > max_sec + 0.3:
        # giu ban DAY DU: luc cham nen se chon doan hay nhat (dung ranh gioi cau)
        # + doan hao hung / doan tram cho the cam xuc, thay vi cat bua 11 giay dau
        (Path(dst).parent / "source.wav").write_bytes(data)
        data = _run_ffmpeg(["-f", "wav", "-i", "pipe:0", "-t", "%.2f" % max_sec,
                            "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le",
                            "-f", "wav", "pipe:1"], data=data)
        dur = _wav_seconds(data)
        trimmed = True
    if dur < 0.5:
        raise RuntimeError("mẫu không có tiếng nói nghe được trong khoảng thời gian đã chọn")
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

    errs = []
    src = d / "source.wav"
    for model_name in (str(_cfg("CLONE_REF_TRANSCRIBE_MODEL", "large-v3-turbo")), "small", "tiny"):
        try:
            if src.exists():
                # ref.wav = dau file nguon: nghe CA file (doan ngan giong doc nhanh de bi nghe ao)
                # roi lay cac tu nam tron trong ref theo word timestamps
                res = _transcribe_ref(src, name=name, model_name=model_name)
                ref_dur = _wav_seconds(ref)
                win = profile_meta(name).get("ref_window") or [0.0, ref_dur]
                t0, t1 = float(win[0]), float(win[1])
                words = [dict(w, start=float(w["start"]) - t0, end=float(w["end"]) - t0)
                         for w in res.get("words") or []
                         if float(w["start"]) >= t0 - 0.05 and float(w["end"]) <= t1 + 0.1]
                res = dict(res, text="".join(w["text"] for w in words).strip())
                if t0 == 0 and words and words[-1]["end"] < ref_dur - 0.3:   # bo chu bi cat giua chung
                    data = _run_ffmpeg(["-i", str(ref), "-t", "%.2f" % (float(words[-1]["end"]) + 0.15),
                                        "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"])
                    ref.write_bytes(data)
                    _save_meta(name, duration=round(_wav_seconds(ref), 2))
            else:
                res = _transcribe_ref(ref, name=name, model_name=model_name)
                words = res.get("words") or []
            if words:
                try:
                    from . import refpick
                    text = refpick._join_words_natural(words)
                except Exception:
                    text = (res.get("text") or "").strip()
            else:
                text = (res.get("text") or "").strip()
            if text:
                if _is_vietnamese(text):
                    try:
                        from .expressive import normalize_numbers_vi
                        text = normalize_numbers_vi(text)
                    except Exception:
                        pass
                # Neu la file ngan (khong qua _smart_ref) nhung co nhac nen -> khu nhac nen trong khoang nghi
                if words and not (d / "source.wav").exists():
                    try:
                        import soundfile as sf
                        from . import refpick
                        y, sr = sf.read(str(ref), dtype="float32", always_2d=False)
                        if y.ndim > 1:
                            y = y.mean(axis=1)
                        if sr == refpick.SR:
                            y_clean = refpick._clean_bgm_with_words(y, 0.0, words)
                            sf.write(str(ref), y_clean, sr, subtype="PCM_16")
                    except Exception:
                        pass
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


SAMPLE_TEXT = ("Hello, this is my cloned voice. The audio is clear, smooth, "
               "and ready for your studio projects.")
SAMPLE_TEXT_VI = ("Xin chào mọi người, đây là giọng đọc nhân bản của tôi, "
                  "âm thanh rất rõ ràng và tự nhiên.")


def _sample_text_for(name):
    return SAMPLE_TEXT_VI if profile_lang(name) == "vi" else SAMPLE_TEXT


def make_sample(name, text=None):
    """Tao file nghe thu cho 1 giong (ghi data/voice_profiles/<ten>/sample.wav)."""
    target_txt = text or _sample_text_for(name)
    wav_bytes = None
    try:
        from . import expressive
        wav_bytes, _mime, _ext = expressive.synth_clone(target_txt, "c:" + name)
    except Exception:
        clips = synth_cues_clone([target_txt], name)
        if clips and clips[0]:
            wav_bytes = clips[0]
    if not wav_bytes:
        raise RuntimeError("không tạo được audio mẫu")
    out = profile_dir(name) / "sample.wav"
    out.write_bytes(wav_bytes)
    _save_meta(name, sample_at=time.time(), sample_error=None)
    return out


# ============================================================== worker

WORKER_VERSION = "2.6"
_wlock = threading.RLock()
_W = {"proc": None, "port": None, "started": 0.0}   # = slot 0 (tuong thich nguoc)
_POOL = {0: _W}            # slot -> {"proc", "port", "started"}


def _slot(i):
    return _POOL.setdefault(i, {"proc": None, "port": None, "started": 0.0})


def worker_count():
    """So worker chay song song. Tren GPU (NVIDIA CUDA hoac Apple MPS) luon chay 1 worker
    duy nhat de tranh 2 tien trinh tranh chap VRAM lam nghen GPU."""
    n = int(_cfg("CLONE_WORKERS", 0) or 0)
    if n <= 0 and (gpu_ready() or sys.platform == "darwin"):
        return 1                          # 1 engine tren GPU/MPS la nhanh va muot nhat
    if n <= 0:
        c = os.cpu_count() or 4
        n = 2 if c >= 12 else 1
    return max(1, n)


def _threads_per_worker():
    t = int(_cfg("CLONE_THREADS_PER_WORKER", 0) or 0)
    if t <= 0:
        t = max(2, (os.cpu_count() or 4) // worker_count())
    return t


def _ping(port, timeout=2.0):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/ping" % port, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return d if d.get("app") == WORKER_APP else None
    except Exception:
        return None


def _ping_ok(port, timeout=2.0):
    """Chi nhan worker DA nap duoc torch + f5_tts va dung phien ban WORKER_VERSION."""
    d = _ping(port, timeout)
    return d if (d and d.get("engine_ok") and d.get("version") == WORKER_VERSION) else None


def _kill_stale(port):
    try:
        urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:%d/shutdown" % port, data=b"{}"), timeout=3).read()
    except Exception:
        pass
    for _ in range(20):
        if _port_free(port):
            return
        time.sleep(0.25)


def _ports():
    base = int(_cfg("CLONE_WORKER_PORT", 8081))
    return range(base, base + int(_cfg("CLONE_WORKER_PORT_TRIES", 10)))


def _port_free(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) != 0


def worker_info():
    """Worker dang song? (khong khoi dong gi ca — chi xem)."""
    alive = []
    for port in _ports():
        if not _port_free(port):
            d = _ping(port, 1.0)
            if d:
                alive.append((port, d))
    if not alive:
        return {"running": False, "port": None, "device": None, "workers": 0}
    port, d = alive[0]
    return {"running": True, "port": port, "device": d.get("device"),
            "busy": any(x.get("busy") for _p, x in alive), "pid": d.get("pid"),
            "workers": len(alive)}


def _stop_slot(w):
    port, proc = w["port"], w["proc"]
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
    w.update(proc=None, port=None)


def stop_worker(slot=None):
    """Tat 1 worker (slot) hoac TAT CA (slot=None, bao gom ca worker tu tien trinh cu)."""
    with _wlock:
        slots = list(_POOL) if slot is None else [slot]
        for s_ in slots:
            _stop_slot(_slot(s_))
        if slot is None:
            for p in _ports():
                if not _port_free(p):
                    _kill_stale(p)
        return True


def ensure_worker(log=None, slot=0):
    """Tra ve cong cua worker `slot` dang san sang — tu khoi dong / hoi sinh khi can.

    Nhieu slot khoi dong DONG THOI duoc (khoa chi giu luc chon cong).
    """
    say = log or (lambda *_a: None)
    with _wlock:
        w = _slot(slot)
        if w["port"] and _ping_ok(w["port"]):
            if not w["proc"] or w["proc"].poll() is None:
                return w["port"]
        w.update(proc=None, port=None)
        taken = {x["port"] for k, x in _POOL.items() if k != slot and x["port"]}
        for p in _ports():                      # worker con song tu lan chay truoc
            if p in taken:
                continue
            if _ping_ok(p):
                w["port"] = p
                return p
            if _ping(p, 1.0):                   # worker cu/hong (thieu torch) -> tat
                say("Tắt engine cũ không dùng được ở cổng %d…" % p)
                _kill_stale(p)

        py, err = engine_python()
        if not py:
            raise RuntimeError(err or _SETUP_HINT)

        port = next((p for p in _ports() if p not in taken and _port_free(p)), None)
        if port is None:
            raise RuntimeError("Không còn cổng trống cho worker nhân bản giọng "
                               "(đã thử %s)." % list(_ports())[:3])

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logf = open(LOG_DIR / "clone_worker.log", "ab", buffering=0)
        logf.write(("\n==== khoi dong %s (port %d, worker %d) ====\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), port, slot + 1)).encode())
        env = os.environ.copy()
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        nthr = str(_threads_per_worker())
        env["CLONE_THREADS"] = nthr
        env["OMP_NUM_THREADS"] = nthr
        env["MKL_NUM_THREADS"] = nthr
        use_gpu = gpu_ready()
        if use_gpu:
            if (GPU_DIR / "torch").is_dir():
                env["PYTHONPATH"] = str(GPU_DIR) + os.pathsep + env.get("PYTHONPATH", "")
            env["CLONE_GPU"] = "1"
        idle = float(_cfg("CLONE_WORKER_IDLE_EXIT", 0) or 0)
        if idle > 0:
            env["CLONE_IDLE_EXIT"] = str(idle)
        say("Khởi động engine nhân bản giọng #%d (cổng %d, %s)…"
            % (slot + 1, port, "GPU" if use_gpu else "%s luồng CPU" % nthr))
        proc = subprocess.Popen([py, "-u", str(WORKER), str(port)],
                                stdout=logf, stderr=logf, env=env)
        w.update(proc=proc, port=port, started=time.time())

    # cho khoi dong NGOAI khoa -> cac worker khac khoi dong song song
    timeout = float(_cfg("CLONE_WORKER_BOOT_TIMEOUT", 300))
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            with _wlock:
                w.update(proc=None, port=None)
            if use_gpu and gpu_ready():
                # ban GPU khong khoi dong duoc tren may nay -> tat GPU, chay lai CPU
                try:
                    (GPU_DIR / ".ok").rename(GPU_DIR / ".failed")
                except OSError:
                    pass
                say("Engine GPU lỗi (mã %s) — chuyển sang CPU." % proc.returncode)
                return ensure_worker(log=log, slot=slot)
            raise RuntimeError("Engine nhân bản giọng tắt ngay khi khởi động "
                               "(mã %s).\n%s" % (proc.returncode, _log_hint()))
        if _ping_ok(port, 2.0):
            say("Engine #%d sẵn sàng sau %.0fs." % (slot + 1, time.time() - t0))
            return port
        time.sleep(1.0)
    stop_worker(slot)
    raise RuntimeError("Engine nhân bản giọng không khởi động nổi sau %.0fs.\n%s"
                       % (timeout, _log_hint()))


def _log_hint():
    tail = log_tail(8)
    base = "Xem chi tiết: %s" % (LOG_DIR / "clone_worker.log")
    return (base + "\n" + "\n".join(tail)) if tail else base


def warm_up(log=None):
    """Khoi dong san TAT CA worker (bam tu /admin) — lan long tieng dau se nhanh hon."""
    from concurrent.futures import ThreadPoolExecutor
    n = worker_count()
    with ThreadPoolExecutor(max_workers=n) as ex:
        ports = list(ex.map(lambda k: ensure_worker(log=log, slot=k), range(n)))
    d = _ping(ports[0], 5.0) or {}
    return {"port": ports[0], "device": d.get("device"), "workers": len(ports)}


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
        # nguong thap + chua le rong: khong cat mat phu am cuoi (s, f, th...)
        thr = max(int(0.003 * 32768), int(peak * 0.01))
        pad = int(sr * 0.08)
        i, j = 0, len(a) - 1
        while i < len(a) and abs(a[i]) < thr:
            i += 1
        while j > i and abs(a[j]) < thr:
            j -= 1
        if i < j:
            a = a[max(0, i - pad):min(len(a), j + pad)]
    if not len(a):
        return wav_bytes

    # NEN KHOANG LANG DAI ben trong doan: F5 (nhat la khi doc cham) hay chen
    # lang 0.8-2s giua cau -> nghe "ngat lau", khach phai cat tay. Lang > 0.45s
    # duoc rut con 0.32s (van giu nhip ngat tu nhien).
    # nguoi that ngat dai ngan khac nhau (dau phay ngan, het cau dai hon): chi rut
    # khoang lang > 0.6s ve 0.45s - ep tat ca ve 0.32s nghe deu deu nhu may
    max_sil = float(_cfg("CLONE_MAX_PAUSE_SEC", 0.6))
    keep_sil = float(_cfg("CLONE_KEEP_PAUSE_SEC", 0.45))
    if max_sil > 0:
        peak2 = max(max(a), -min(a)) or 1
        thr2 = max(int(0.003 * 32768), int(peak2 * 0.012))
        win = int(sr * 0.01)
        n_win = len(a) // win
        quiet = [max(abs(x) for x in a[k * win:(k + 1) * win]) < thr2 for k in range(n_win)]
        out, k, last = array("h"), 0, 0
        while k < n_win:
            if quiet[k]:
                j = k
                while j < n_win and quiet[j]:
                    j += 1
                run = (j - k) * win
                if run > max_sil * sr:
                    keep = int(keep_sil * sr)
                    out.extend(a[last:k * win + keep // 2])
                    last = j * win - keep // 2
                k = j
            else:
                k += 1
        out.extend(a[last:])
        a = out

    if _opt("normalize", "CLONE_POST_NORMALIZE", True):
        # CHUAN HOA THEO DO TO GIONG NOI (trung binh phan co tieng), KHONG theo dinh, va chua
        # khoang du -> buoc cam xuc sau do tang/giam to-nho tung cau con tac dung (truoc day
        # moi cau bi ep cung dinh -1.5 dB, tang to cho cau hao hung bi bo gioi han chan het).
        import numpy as np
        x = np.frombuffer(a.tobytes(), dtype=np.int16).astype(np.float32)
        peak = float(np.abs(x).max()) or 1.0
        fr = max(1, int(sr * 0.02))
        nfr = len(x) // fr
        if nfr:
            frms = np.sqrt((x[:nfr * fr].reshape(nfr, fr) ** 2).mean(axis=1))
            voiced = frms[frms > peak * 0.05]
            rms_v = float(np.sqrt((voiced ** 2).mean())) if len(voiced) else peak / 4
        else:
            rms_v = peak / 4
        target = 32768.0 * (10 ** (float(_cfg("CLONE_POST_RMS_DBFS", -20.0)) / 20.0))
        ceil = 32768.0 * (10 ** (float(_cfg("CLONE_POST_PEAK_CEIL_DBFS", -4.0)) / 20.0))
        gain = min(target / max(rms_v, 1.0), ceil / peak,
                   10 ** (float(_cfg("CLONE_POST_MAX_GAIN_DB", 12.0)) / 20.0))
        if abs(gain - 1.0) > 0.02:
            a = array("h", np.clip(x * gain, -32768, 32767).astype(np.int16).tobytes())

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


class EngineStalled(RuntimeError):
    """Engine khong doc xong doan nao trong qua lau -> coi la treo (vd loi MPS/GPU)."""


def _stall_limit(payload):
    """So giay toi da cho 1 doan moi (khong co doan moi = treo). CPU cham hon -> cho gap doi."""
    base = float(_cfg("CLONE_STALL_SEC", 300))
    dev = (payload.get("device") or payload.get("prefer_device") or "").lower()
    if payload.get("engine") == "voxcpm" and not gpu_ready():
        base *= 3                          # VoxCPM2 2B tren Mac/CPU: 1 cau dai co the > 5 phut
    return base * (2 if dev == "cpu" else 1)


def _request(port, payload, out_dir, n_expected, progress=None, timeout=7200, stall=None):
    """Goi worker, vua cho vua bao tien do theo so file da ghi xong.
    Khong co doan moi trong `stall` giay -> nem EngineStalled (ben goi tat worker, doc lai)."""
    result = {}
    err = [None]
    stall = _stall_limit(payload) if stall is None else stall

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
    moved = time.time()                    # lan cuoi co them doan doc xong
    while th.is_alive():
        time.sleep(0.8)
        done = len(list(out_dir.glob("[0-9]*.wav"))) if out_dir.is_dir() else 0
        if done != last:
            last = done
            moved = time.time()
            if progress:
                progress(min(done, n_expected), n_expected)
        elif stall and time.time() - moved > stall:
            raise EngineStalled("Engine giọng không đọc xong đoạn nào trong %d giây — có thể bị "
                                "treo (GPU/MPS). Tự khởi động lại và đọc lại." % stall)
    th.join()
    if err[0]:
        raise RuntimeError(err[0])
    return result


STYLE_NAMES = ("hype", "calm", "sad", "angry", "soft", "surprise", "serious", "warm",
               "alt1", "alt2", "alt3")
# mau cam xuc THAT them (Chatterbox doc cau co noi dung dung cam xuc bang chinh giong do)
EMO_STYLES = ("sad", "angry", "soft", "surprise", "serious", "warm")
ALT_STYLES = ("alt1", "alt2", "alt3")      # mau phu cung giong, cung nhip -> xoay vong cho doc bot don dieu


def available_styles(name):
    d = profile_dir(safe_name(name)) / "styles"
    return {s for s in STYLE_NAMES
            if (d / s / "ref.wav").exists() and (d / s / "ref.txt").exists()}


def synthetic_styles(name):
    """Mau cam xuc KHONG phai giong that: Chatterbox doc (styles_synth / cau mau STYLE_TEXTS_EN)
    hoac bien doi cao do (bien_doi). VoxCPM2 noi tiep mau -> dung mau nay la chep luon chat AI."""
    m = profile_meta(safe_name(name))
    out = set(m.get("styles_synth") or [])
    out |= {k for k, v in (m.get("styles_method") or {}).items() if v == "bien_doi"}
    d = profile_dir(safe_name(name)) / "styles"
    for sty, text in STYLE_TEXTS_EN.items():
        try:
            if (d / sty / "ref.txt").read_text(encoding="utf-8").strip() == text.strip():
                out.add(sty)
        except OSError:
            pass
    return out


def style_ref(name, style):
    d = profile_dir(safe_name(name)) / "styles" / style
    txt = (d / "ref.txt").read_text(encoding="utf-8").strip()
    if txt:
        if _is_vietnamese(txt):
            from .expressive import normalize_numbers_vi
            txt = normalize_numbers_vi(txt)
        else:
            from .expressive import normalize_numbers_en
            txt = normalize_numbers_en(txt)
    return str((d / "ref.wav").absolute()), txt


def _ref_wpm(wav_path, txt_path):
    try:
        n = len(Path(txt_path).read_text(encoding="utf-8").split())
        d = _wav_seconds(Path(wav_path).read_bytes())
        return n / d * 60.0 if n >= 6 and d > 2 else None
    except Exception:
        return None


def style_speed(name, style):
    """Mau cam xuc noi nhanh/cham hon mau chinh thi giong doc theo cung nhanh/cham
    -> bu lai de nhip doc deu giua cac doan (vd mau hao hung 240 tu/phut vs mau
    chinh 200 -> doan vui bi doc voi, nghe gap)."""
    d = profile_dir(safe_name(name))
    # mau BIEN DOI: nhanh/cham la CO Y (mot phan cua cam xuc) -> khong bu lai
    if (profile_meta(safe_name(name)).get("styles_method") or {}).get(style) in ("bien_doi", "tai_len"):
        return 1.0                   # mau bien doi / mau that tai len: nhip la CO Y
    if style in EMO_STYLES:          # buon cham / gian nhanh la CAM XUC -> khong bu toc do
        return 1.0
    a = _ref_wpm(d / "ref.wav", d / "ref.txt")
    b = _ref_wpm(d / "styles" / style / "ref.wav", d / "styles" / style / "ref.txt")
    if not a or not b:
        return 1.0
    return max(0.85, min(1.15, a / b))


def synth_cues_clone(texts, voice_id, rate=1.0, progress=None, log=None, speeds=None,
                     styles=None, exaggerations=None, cfgs=None, engine=None):
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
    # toc do rieng tung giong (meta.json "speed", vd 0.85 = cham 15%): giong mau
    # noi qua nhanh (nhu ray) thi giong clone cung nhanh va nuot chu
    try:
        voice_speed = float(profile_meta(name).get("speed") or 1.0)
    except (TypeError, ValueError):
        voice_speed = 1.0
    plan = _plan(texts, _rate_to_speed(rate) * max(0.5, min(1.5, voice_speed)))
    if speeds:                             # he so toc do rieng tung doan (bieu cam)
        plan = [(t, round(max(0.3, min(2.0, sp * float(speeds[i] if i < len(speeds) else 1.0))), 3))
                for i, (t, sp) in enumerate(plan)]
    need_vi = _is_vietnamese(" ".join(t for t, _s in plan))
    engine = engine or engine_for(not need_vi)
    # mau "tong giong" rieng (styles/hype, styles/calm) neu giong co: cam xuc den tu
    # CHINH giong do noi hao hung / tram -> khong phai bop meo am thanh sau khi doc
    avail = available_styles(name)
    if engine == "voxcpm":
        avail -= synthetic_styles(name)    # chi mau cat tu giong that / nguoi dung tai len
    sfac = {k: (1.0 if need_vi else style_speed(name, k)) for k in avail}
    items = []
    for i, (t, sp) in enumerate(plan):
        if not t:
            continue
        sty = styles[i] if styles and i < len(styles) and styles[i] in avail else ""
        if sty:
            sp = round(max(0.3, min(2.0, sp * sfac[sty])), 3)
        it = {"i": i + 1, "text": t, "speed": sp, "style": sty}
        if exaggerations and i < len(exaggerations) and exaggerations[i]:
            it["exaggeration"] = float(exaggerations[i])
        if cfgs and i < len(cfgs) and cfgs[i]:
            it["cfg"] = float(cfgs[i])          # Chatterbox: do bam nhip rieng tung cam xuc
        items.append(it)
    if not items:
        return [b""] * n

    if need_vi and engine != "voxcpm" and not assets_ready():
        bad = "; ".join("%s: %s" % (a["label"], a["problem"])
                        for a in clone_assets.state() if not a["ok"])
        raise RuntimeError("Thiếu dữ liệu giọng Việt (%s).\nVào /admin bấm "
                           "\"Tải model giọng Việt\", hoặc chạy: python3 setup_clone.py" % bad)

    ref_text = _ensure_ref_text(name)
    if ref_text:
        # script mau co so ("$220", "2026") phai viet thanh chu nhu luc doc that, khong
        # F5 can lech mau <-> chu -> doc sai / nuot chu
        if _is_vietnamese(ref_text):
            from .expressive import normalize_numbers_vi
            ref_text = normalize_numbers_vi(ref_text)
        else:
            from .expressive import normalize_numbers_en
            ref_text = normalize_numbers_en(ref_text)
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
            # chu thuong chi danh cho model ViVoice; tieng Anh giu nguyen hoa/thuong
            "lowercase": need_vi and engine != "voxcpm" and bool(_opt("lowercase", "CLONE_LOWERCASE", True)),
            "remove_silence": False,
            "engine": engine,
            # do tren Ray: cfg 0.3 + cam xuc 0.7 -> dung 100% chu, nhip = ElevenLabs
            "cbx_cfg": float(_cfg("CBX_CFG_WEIGHT", 0.3)),
            "cbx_temp": float(_cfg("CBX_TEMPERATURE", 0.8)),
            "exaggeration": float(_cfg("CBX_EXAGGERATION", 0.65)),
            "vox_dir": str(VOX_DIR.absolute()),
            "vox_cfg": float(_cfg("VOX_CFG", 2.0)),
            "vox_steps": int(_cfg("VOX_STEPS", 10)),
            "prefer_device": st.get("device") or _cfg("CLONE_DEVICE", "auto"),
            "device": (st.get("device") if st.get("device") in ("cpu", "cuda", "mps")
                       else None),
        }

        # Vong doc: doan nao loi thi doc lai RIENG doan do (ha xuong CPU). Worker
        # chet giua duong cung khong mat cong — khoi dong lai roi doc tiep phan con lai.
        pending = list(items)
        retries = max(0, int(_cfg("CLONE_RETRY", 1)))
        last_error = None
        stalled = [False]                  # lan truoc bi TREO (khong phai loi) -> doc lai cung thiet bi
        for attempt in range(retries + 2):    # +1: bi treo -> lan cuoi chay CPU cho chac
            if not pending:
                break
            if attempt > retries and not stalled[0]:
                break                          # loi thuong: giu so lan doc lai nhu cu
            if attempt:
                say("Đọc lại %d đoạn bị lỗi (lần %d, %s)…" % (
                    len(pending), attempt, "khởi động lại engine" if stalled[0] and attempt <= retries
                    else "dùng CPU"))
            # Chia LO nho va chay SONG SONG tren nhieu worker (van ban 30-40k ky tu
            # = ~80 doan). Moi lo ngan -> khong cham timeout; worker sap chi mat 1
            # lo, lo sau tu hoi sinh worker roi doc tiep.
            total_n = len(pending)
            nw = 1 if attempt else min(worker_count(), total_n)
            batch_n = max(1, min(int(_cfg("CLONE_BATCH_ITEMS", 6)),
                                 -(-total_n // nw)))
            batches = []
            for sty in sorted({it.get("style") or "" for it in pending}):
                grp = [it for it in pending if (it.get("style") or "") == sty]
                batches += [grp[k:k + batch_n] for k in range(0, len(grp), batch_n)]
            nw = min(nw, len(batches))
            bad = set()
            qlock = threading.Lock()
            queue = list(batches)
            stop_mon = threading.Event()

            def run_slot(slot, attempt=attempt):
                local_bad, local_err = set(), None
                while True:
                    with qlock:
                        if not queue:
                            break
                        batch = queue.pop(0)
                    req = dict(payload, items=batch)
                    sty = batch[0].get("style") or ""
                    if sty:
                        req["ref_audio"], req["ref_text"] = style_ref(name, sty)
                    if attempt and (not stalled[0] or attempt > retries):
                        req["device"] = "cpu"  # loi that / treo lan 2: CPU cho chac
                    # bi treo: worker moi tren CUNG thiet bi (GPU) thuong chay lai binh thuong
                    try:
                        port = ensure_worker(log=say, slot=slot)
                        res = _request(port, req, out_dir, total_n, progress=None)
                        local_bad |= {f["i"] for f in (res.get("failed") or [])}
                    except Exception as e:
                        local_err = e
                        if isinstance(e, EngineStalled):
                            stalled[0] = True
                        say("Engine #%d gặp sự cố: %s" % (slot + 1, e))
                        stop_worker(slot)      # lo sau tu hoi sinh worker
                        local_bad |= {it["i"] for it in batch
                                      if not (out_dir / ("%04d.wav" % it["i"])).exists()}
                return local_bad, local_err

            def monitor():
                last = -1
                while not stop_mon.wait(1.0):
                    done = len(list(out_dir.glob("[0-9]*.wav")))
                    if progress and done != last:
                        last = done
                        progress(min(done, total_n), total_n)

            mon = threading.Thread(target=monitor, daemon=True)
            if attempt == 0:
                mon.start()
            from concurrent.futures import ThreadPoolExecutor as _TPE
            try:
                with _TPE(max_workers=nw) as ex:
                    for lb, le in ex.map(run_slot, range(nw)):
                        bad |= lb
                        if le:
                            last_error = le
            finally:
                stop_mon.set()
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
