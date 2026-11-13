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
VERSION = "2.6"
_lock = threading.Lock()          # 1 lenh suy dien 1 luc (GPU khong chia duoc)
_last_used = [time.time()]


def log(msg):
    sys.stderr.write("[clone-worker %s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


# ---------------------------------------------------------------- ffmpeg/PATH

def _setup_env_and_paths():
    """Bao dam co ffmpeg trong PATH, MPLCONFIGDIR ghi duoc, va HF_HOME tro vao cache local."""
    import tempfile
    mpl_dir = os.path.join(tempfile.gettempdir(), "whisper_stt_mpl")
    try:
        os.makedirs(mpl_dir, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", mpl_dir)
    except Exception:
        pass
    root = Path(__file__).resolve().parents[2]
    for hf_cand in (root / "data" / "hf_cache", root / "_build_v4" / "hf_cache"):
        if hf_cand.is_dir():
            os.environ.setdefault("HF_HOME", str(hf_cand))
            os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(hf_cand / "hub"))
            break
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


_setup_env_and_paths()
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


# ------------------------------------------------------------ kiem tra engine

ENGINE = {"ok": False, "torch": None, "error": None}


def _patch_torchaudio():
    """torchaudio.load tren Windows hay vo (thieu backend / torchcodec / ffmpeg DLL).

    F5-TTS chi dung no de doc file wav mau -> boc lai: thu ban goc truoc, loi thi
    doc bang soundfile (thuan Python + libsndfile, chay moi may).
    """
    try:
        import torchaudio
    except Exception as e:
        log("Khong nap duoc torchaudio (%s) -> dung soundfile." % e)
        return
    if getattr(torchaudio.load, "_whisper_stt_patched", False):
        return
    orig = torchaudio.load

    def load(uri, *a, **kw):
        try:
            return orig(uri, *a, **kw)
        except Exception as e:
            import numpy as np
            import soundfile as sf
            import torch
            data, sr = sf.read(str(uri), dtype="float32", always_2d=True)
            log("torchaudio.load loi (%s) -> doc bang soundfile." % type(e).__name__)
            return torch.from_numpy(np.ascontiguousarray(data.T)), sr

    load._whisper_stt_patched = True
    torchaudio.load = load


VOCOS_REPO = "charactr/vocos-mel-24khz"
_VOCOS = {"dir": None, "checked": False}


def _vocoder_dir():
    """Thu muc chua vocoder (config.yaml + pytorch_model.bin) de F5 nap OFFLINE.

    Thu tu: data/vocos-mel-24khz  ->  snapshots trong data/hf_cache  ->  cache HF
    ->  tai tu huggingface.co  ->  hf-mirror.com.
    """
    if _VOCOS["checked"]:
        return _VOCOS["dir"]
    _VOCOS["checked"] = True
    need = ("config.yaml", "pytorch_model.bin")
    root = Path(__file__).resolve().parents[2]
    cands = [root / "data" / "vocos-mel-24khz"]
    for hf_base in (root / "data" / "hf_cache", root / "_build_v4" / "hf_cache",
                    Path.home() / ".cache" / "huggingface"):
        snap_root = hf_base / "hub" / "models--charactr--vocos-mel-24khz" / "snapshots"
        if snap_root.is_dir():
            cands.extend(sorted(snap_root.iterdir(), reverse=True))
    for cand in cands:
        if cand.is_dir() and all((cand / f).is_file() for f in need):
            _VOCOS["dir"] = str(cand)
            return _VOCOS["dir"]
    try:
        from huggingface_hub import hf_hub_download, try_to_load_from_cache
    except Exception:
        return None
    try:
        got = [try_to_load_from_cache(VOCOS_REPO, f) for f in need]
        if all(isinstance(g, str) and os.path.isfile(g) for g in got):
            _VOCOS["dir"] = os.path.dirname(got[0])
            return _VOCOS["dir"]
    except Exception:
        pass
    for ep in (None, "https://hf-mirror.com"):
        try:
            got = [hf_hub_download(VOCOS_REPO, f, endpoint=ep, etag_timeout=5) for f in need]
            _VOCOS["dir"] = os.path.dirname(got[0])
            return _VOCOS["dir"]
        except Exception as e:
            log("Tai vocoder tu %s loi: %s" % (ep or "huggingface.co", str(e)[:150]))
    return None


EN_REPO, EN_FILE = "SWivid/F5-TTS", "F5TTS_Base/model_1200000.safetensors"


def _english_ckpt():
    """Model F5 goc (tieng Anh) — ban giao da kem san trong data/hf_cache.
    Tim thay thi nap thang tu file (khong goi mang); khong co thi tra "" de F5
    tu tai tu HuggingFace nhu cu."""
    root = Path(__file__).resolve().parents[2]
    local = root / "data" / "f5_en" / "model_1200000.safetensors"
    if local.is_file():
        return str(local)
    for hf_base in (root / "data" / "hf_cache", root / "_build_v4" / "hf_cache",
                    Path.home() / ".cache" / "huggingface"):
        snap_root = hf_base / "hub" / "models--SWivid--F5-TTS" / "snapshots"
        if snap_root.is_dir():
            for sdir in sorted(snap_root.iterdir(), reverse=True):
                p = sdir / EN_FILE
                if p.is_file():
                    return str(p)
    try:
        from huggingface_hub import try_to_load_from_cache
        got = try_to_load_from_cache(EN_REPO, EN_FILE)
        if isinstance(got, str) and os.path.isfile(got):
            return got
    except Exception:
        pass
    return ""


def _patch_f5_vietnamese():
    """Sua 3 loi nghiem trong cua thu vien f5_tts goc khi doc tieng Viet (ViVoice):
    1) convert_char_to_pinyin dung rjieba.cut() de tach tu tieng Trung -> cat doi am tiet
       tieng Viet o nguyen am co dau va chen dau cach vao giua tu ('đang' -> 'đ ang',
       'cùng tính năng' -> 'cù ng tí nh nă ng', 'hệ thống' -> 'hệ thố ng') lam giong doc vap!
    2) chunk_text tinh max_chars theo UTF-8 byte -> tu cat cau tieng Viet o dau phay ','
       roi cross-fade giua cau.
    3) preprocess_ref_audio_text cat mat khoang lang 0.25s cuoi ref.wav xuong con 50ms ->
       am cuoi cua mau dinh vao am dau cua cau moi."""
    import re
    try:
        import f5_tts.model.utils as f5_utils
        import f5_tts.infer.utils_infer as f5_infer
        import f5_tts.api as f5_api
    except Exception as e:
        log("Khong patch duoc f5_tts (%s)" % e)
        return

    if not getattr(f5_utils.convert_char_to_pinyin, "_vi_patched", False):
        orig_pinyin = f5_utils.convert_char_to_pinyin
        custom_trans = str.maketrans(
            {";": ",", "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"}
        )

        def _vi_safe_pinyin(text_list, polyphone=True):
            # Neu khong co chu Han (CJK) -> giu nguyen tung ky tu UTF-8, TUYET DOI khong goi rjieba.cut()
            if not any(re.search(r"[\u3100-\u9fff]", t or "") for t in text_list):
                is_vi_batch = bool(getattr(State, "ckpt", None) and State.ckpt[0]) or any(
                    c in _VI_CHARS for t in text_list for c in (t or "").lower()
                )
                out = []
                for t in text_list:
                    s = (t or "").translate(custom_trans)
                    if is_vi_batch:
                        # Model ViVoice chi duoc huan luyen tren chu thuong (a-z, à-ỹ, đ) va dau '.' ',' '?' '!'
                        s = s.lower()
                        s = re.sub(r"(?:\.\.\.|…)+\s*$", ".", s)
                        s = re.sub(r"(?:\.\.\.|…)+", ",", s)
                    clean = re.sub(r" +", " ", s).strip()
                    out.append(list(clean))
                return out
            return orig_pinyin(text_list, polyphone=polyphone)

        _vi_safe_pinyin._vi_patched = True
        f5_utils.convert_char_to_pinyin = _vi_safe_pinyin
        f5_infer.convert_char_to_pinyin = _vi_safe_pinyin

    if not getattr(f5_infer.chunk_text, "_vi_patched", False):
        orig_chunk = f5_infer.chunk_text

        def _vi_safe_chunk_text(text, max_chars=135):
            t = (text or "").strip()
            # expressive.py da chia cau chuan (< 220 ky tu) -> khong de f5_tts cat doi cau o dau phay
            if len(t) <= 245:
                return [t] if t else []
            return orig_chunk(t, max_chars=max(max_chars, 260))

        _vi_safe_chunk_text._vi_patched = True
        f5_infer.chunk_text = _vi_safe_chunk_text

    if not getattr(f5_infer.preprocess_ref_audio_text, "_vi_patched", False):
        orig_pre = f5_infer.preprocess_ref_audio_text

        def _vi_safe_preprocess(ref_audio_orig, ref_text, show_info=print, **kw):
            # File mau da duoc _padded_ref chuan hoa <= 12.5s va dem san 0.24s im lang o cuoi:
            # giu nguyen file de khong bi pydub cat mat khoang lang cuoi hoac cat nham nhac nen.
            rt = (ref_text or "").strip()
            if rt:
                try:
                    import soundfile as sf
                    dur = sf.info(str(ref_audio_orig)).duration
                    if 1.0 <= dur <= 12.8:
                        rt_clean = rt.rstrip()
                        if not rt_clean.endswith((".", "!", "?", "。")):
                            rt_clean += "."
                        # De infer_batch_process tu them 1 dau cach sau dau cham -> ". "
                        return str(ref_audio_orig), rt_clean
                except Exception:
                    pass
            return orig_pre(ref_audio_orig, ref_text, show_info=show_info, **kw)

        _vi_safe_preprocess._vi_patched = True
        f5_infer.preprocess_ref_audio_text = _vi_safe_preprocess
        f5_api.preprocess_ref_audio_text = _vi_safe_preprocess


def preflight():
    """Nap torch + f5_tts NGAY khi khoi dong. Thieu thi thoat luon voi loi ro rang
    (truoc day worker van chay, tra loi /ping binh thuong, den luc doc moi chet
    voi 'No module named torch')."""
    try:
        import torch
        nthr = int(os.environ.get("CLONE_THREADS") or 0)
        if nthr > 0:
            torch.set_num_threads(nthr)
            try:
                torch.set_num_interop_threads(1)
            except Exception:
                pass
        _patch_torchaudio()
        from f5_tts.api import F5TTS  # noqa: F401
        _patch_f5_vietnamese()
        ENGINE.update(ok=True, torch=torch.__version__)
        log("Engine OK: torch %s, %d luong, python %s"
            % (torch.__version__, torch.get_num_threads(), sys.executable))
    except Exception as e:
        ENGINE.update(ok=False, error="%s: %s" % (type(e).__name__, e))
        log("ENGINE HONG - Python nay khong chay duoc F5-TTS: %s\n  python = %s\n%s"
            % (ENGINE["error"], sys.executable, traceback.format_exc()))
        sys.exit(3)
    vd = _vocoder_dir()
    log("Vocoder: %s" % (vd or "KHONG CO SAN - F5 se tu tai tu HuggingFace (can mang)"))


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
    if not ckpt:                           # van ban tieng Anh -> model F5 goc
        ckpt = _english_ckpt()
        vocab = ""                         # dung bang ky tu mac dinh cua F5
        log("Model tieng Anh: %s" % (ckpt or "(chua co san - F5 tu tai)"))
    kw = {"ckpt_file": ckpt or "", "vocab_file": vocab or "", "device": device}
    vd = _vocoder_dir()
    if vd:
        kw["vocoder_local_path"] = vd      # nap vocoder offline, khong goi mang
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


_PADDED = {}
_REF_TAIL_SEC = 0.24


def _file_sig(path):
    try:
        st = os.stat(path)
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), 0, 0)


def _padded_ref(path, tail=_REF_TAIL_SEC):
    """Mau giong phai KET THUC bang ~0.24s im lang sach: F5 sinh tiep ngay sau mau roi cat
    o ranh gioi -> mau het dot ngot hoac con duoi nhac nen thi am dau cua cau moi bi lech."""
    sig = _file_sig(path)
    if sig in _PADDED and os.path.exists(_PADDED[sig]):
        return _PADDED[sig]
    out = path
    try:
        import numpy as np
        import soundfile as sf
        import tempfile
        y, sr = sf.read(path, dtype="float32", always_2d=False)
        if y.ndim > 1:
            y = y.mean(axis=1)
        a = np.abs(y)
        thr = max(0.005, float(a.max()) * 0.015)
        nz = np.where(a > thr)[0]
        if len(nz):
            end_idx = min(len(y), int(nz[-1]) + int(0.035 * sr))
            y = y[:end_idx].copy()
            fade = min(len(y) // 4, int(0.015 * sr))
            if fade > 1:
                y[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=y.dtype)
        y = np.concatenate([y, np.zeros(int(sr * tail), dtype=y.dtype)])
        fd, out = tempfile.mkstemp(suffix=".wav", prefix="ref_pad_")
        os.close(fd)
        sf.write(out, y, sr, subtype="PCM_16")
    except Exception as e:
        log("Khong dem duoc mau (%s) -> dung mau goc." % e)
        out = path
    _PADDED[sig] = out
    return out


_VI_CHARS = set("àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ")
_REF_SPEECH_CACHE = {}


def _measure_ref_speech(ref_audio_path):
    """Do chinh xac tong thoi gian thuc su co tieng noi (khong tinh khoang nghi > 0.14s
    va duoi im lang 0.24s) trong file mau ref_audio_path."""
    sig = _file_sig(ref_audio_path)
    if sig in _REF_SPEECH_CACHE:
        return _REF_SPEECH_CACHE[sig]
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(str(ref_audio_path), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    total = float(len(y)) / float(sr or 24000)
    win = max(1, int(0.02 * sr))
    n_win = len(y) // win
    if n_win < 5:
        return total, max(1.0, total - _REF_TAIL_SEC)
    frames = y[:n_win * win].reshape(n_win, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    thr = max(0.006, float(np.max(rms)) * 0.04)
    active = rms >= thr
    # Mo rong 1 khung (20ms) hai ben va lap cac khe ngan <= 120ms giua cac am tiet lien nhau
    pad = np.convolve(active.astype(np.int32), np.ones(3, dtype=np.int32), mode="same") > 0
    i = 0
    while i < n_win:
        if not pad[i]:
            j = i
            while j < n_win and not pad[j]:
                j += 1
            if 0 < i and j < n_win and (j - i) * 0.02 <= 0.12:
                pad[i:j] = True
            i = j
        else:
            i += 1
    voiced_sec = max(1.2, float(np.sum(pad)) * 0.02)
    _REF_SPEECH_CACHE[sig] = (total, voiced_sec)
    return total, voiced_sec


def _calc_vi_fix_duration(ref_audio_path, ref_text, gen_text, speed):
    """Tinh thoi luong (fix_duration) cho tieng Viet theo SO AM TIET + NGAN NGAT DAU CAU.
    Khac phuc triet de loi cua f5_tts goc:
      - f5_tts tinh ty le theo byte UTF-8 (chu co dau 3 byte gap 3 lan chu khong dau)
      - f5_tts nhan ca thoi gian nghi giua 5-6 cau ngan trong mau vao cau dai 1 mach ->
        thua 2-3 giay mel frame khien model tu chen tieng vap / keo dai giua tu!
      - Dong thoi chua du khong gian nhip tho o dau phay / dau cham de model nhan nha tu nhien."""
    import re
    ref_words = re.findall(r"\w+", ref_text or "")
    gen_words = re.findall(r"\w+", gen_text or "")
    if not ref_words or not gen_words:
        return None
    ref_total, ref_voiced = _measure_ref_speech(ref_audio_path)
    if ref_total <= 1.0:
        return None

    ref_body = (ref_text or "").rstrip().rstrip(".?!…,:;—- ")
    ref_maj = len(re.findall(r"[.?!…]+", ref_body))
    ref_min = len(re.findall(r"[,;:—\-]+", ref_body))
    ref_pause = min(ref_total * 0.32, ref_maj * 0.28 + ref_min * 0.16)
    ref_speech_est = max(1.2, ref_total - _REF_TAIL_SEC - ref_pause)
    ref_speech = 0.6 * ref_voiced + 0.4 * ref_speech_est
    # Thoi gian phat am 1 am tiet tieng Viet tu nhien: ~0.182s .. 0.215s (tranh du frame gay ngat giua cum tu)
    sec_per_word = max(0.182, min(0.215, 0.92 * ref_speech / len(ref_words)))

    gen_body = (gen_text or "").rstrip().rstrip(".?!…,:;—- ")
    gen_maj = len(re.findall(r"[.?!…]+", gen_body))
    gen_min = len(re.findall(r"[,;:—\-]+", gen_body))
    gen_speech = len(gen_words) * sec_per_word
    # Chua nhip ngat tu nhien o dau phay (0.15s) va giua cau (0.24s)
    gen_pause = gen_maj * 0.24 + gen_min * 0.15
    gen_dur = (gen_speech + gen_pause) / max(0.6, float(speed or 1.0)) + 0.15
    return ref_total + max(0.50, gen_dur)


def _infer_one(tts, req, text, speed, out_path):
    """Doc 1 doan ra file wav. Ghi file tam roi doi ten -> khong ai doc file nua voi."""
    # duoi phai la .wav: soundfile doan dinh dang tu duoi file (".wav.part" -> loi)
    tmp = out_path.with_name("tmp_" + out_path.name)   # khong bat dau bang so -> khong bi dem nham tien do
    ref_pad = _padded_ref(req["ref_audio"])
    ref_txt = req.get("ref_text") or ""
    is_vi = bool(req.get("ckpt")) or any(c in _VI_CHARS for c in (text + ref_txt).lower())
    if is_vi and bool(req.get("lowercase", True)):
        ref_txt = ref_txt.lower()
        text = text.lower()
    kw = {
        "ref_file": ref_pad,
        "ref_text": ref_txt,
        "gen_text": text,
        "file_wave": str(tmp),
        "nfe_step": int(req.get("nfe_step") or 32),
        "cfg_strength": float(req.get("cfg_strength") or (2.2 if is_vi else 2.0)),
        "sway_sampling_coef": float(req.get("sway", -1.0)),
        "cross_fade_duration": float(req.get("cross_fade", 0.12)),
        "target_rms": float(req.get("target_rms") or 0.1),
        "speed": float(speed or 1.0),
        "remove_silence": bool(req.get("remove_silence")),
        "show_info": lambda *_a, **_k: None,
    }
    if is_vi:
        try:
            fdur = _calc_vi_fix_duration(ref_pad, ref_txt, text, speed)
            if fdur is not None:
                kw["fix_duration"] = fdur
        except Exception:
            pass
    elif len(text.encode("utf-8")) < 10:
        # Doan tieng Anh qua ngan (< 10 byte, vd 1 tu nhan manh "Suddenly", "Haha!"): F5 tu ep
        # toc do 0.3 -> keo dai le the. Tinh san do dai hop ly va truyen fix_duration.
        try:
            import soundfile as sf
            ref_dur = min(12.0, sf.info(ref_pad).duration)
            rt = ref_txt.encode("utf-8")
            if rt:
                gen_dur = ref_dur * len(text.encode("utf-8")) / len(rt) / float(speed or 1.0)
                kw["fix_duration"] = ref_dur + max(0.35, gen_dur * 1.15)
        except Exception:
            pass
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


# ------------------------------------------------------------ Chatterbox (EN)

class CBX:
    model = None
    device = None
    ref = None


def cbx_dir():
    base = Path(__file__).resolve().parents[2]
    for d in (base / "models_chatterbox", base / "data" / "chatterbox"):
        if (d / "t3_cfg.safetensors").exists() and (d / "s3gen.safetensors").exists():
            return d
    return None


def _ensure_pkg_resources():
    """setuptools >= 81 da BO module pkg_resources (ban dong goi kem setuptools 84) ->
    thu vien dong dau AI cua Chatterbox (perth) nap loi, PerthImplicitWatermarker = None
    -> 'NoneType object is not callable'. Tao ban thay the toi thieu (chi cac ham tim file
    trong thu vien) de bo dong dau THAT van chay — khong tat dong dau."""
    try:
        import pkg_resources  # noqa: F401
        return
    except Exception:
        pass
    import importlib
    import types
    m = types.ModuleType("pkg_resources")

    def resource_filename(package, resource):
        mod = importlib.import_module(package) if isinstance(package, str) else package
        return os.path.join(os.path.dirname(os.path.abspath(mod.__file__)), *resource.split("/"))

    def resource_stream(package, resource):
        return open(resource_filename(package, resource), "rb")

    def resource_string(package, resource):
        with resource_stream(package, resource) as f:
            return f.read()

    def resource_exists(package, resource):
        return os.path.exists(resource_filename(package, resource))

    m.resource_filename, m.resource_stream = resource_filename, resource_stream
    m.resource_string, m.resource_exists = resource_string, resource_exists
    sys.modules["pkg_resources"] = m
    old = sys.modules.get("perth")              # perth da nap loi truoc do -> nap lai
    if old is not None and getattr(old, "PerthImplicitWatermarker", None) is None:
        for k in [k for k in sys.modules if k == "perth" or k.startswith("perth.")]:
            del sys.modules[k]
    log("Thieu pkg_resources (setuptools moi) -> dung ban thay the cho bo dong dau perth.")


def get_cbx(device):
    if CBX.model is not None and CBX.device == device:
        return CBX.model
    d = cbx_dir()
    if d is None:
        raise RuntimeError("Thieu model Chatterbox (thu muc models_chatterbox)")
    _patch_torchaudio()
    _ensure_pkg_resources()
    import perth
    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        raise RuntimeError("Bo dong dau AI cua Chatterbox (perth) khong nap duoc — gui "
                           "data/logs/clone_worker.log cho ky thuat.")
    from chatterbox.tts import ChatterboxTTS
    log("Nap Chatterbox tu %s tren %s ..." % (d, device))
    t = time.time()
    if device == "cpu":
        import torch
        _orig = torch.load
        torch.load = lambda *a, **k: _orig(*a, **dict(k, map_location="cpu"))
    CBX.model = ChatterboxTTS.from_local(d, device)
    CBX.device, CBX.ref = device, None
    log("Chatterbox san sang (%.1fs)" % (time.time() - t))
    return CBX.model


def _infer_cbx(model, req, it, text, out_path):
    import soundfile as sf
    tmp = out_path.with_name("tmp_" + out_path.name)
    ex = float(it.get("exaggeration") or req.get("exaggeration") or 0.5)
    ref = req["ref_audio"]
    if CBX.ref != ref:
        model.prepare_conditionals(ref, exaggeration=ex)
        CBX.ref = ref
    seed = req.get("seed")
    if seed is not None and int(seed) >= 0:
        import torch
        torch.manual_seed(int(seed))
    wav = model.generate(text, exaggeration=ex,
                         cfg_weight=float(it.get("cfg") or req.get("cbx_cfg") or 0.5),
                         temperature=float(req.get("cbx_temp") or 0.8))
    sf.write(str(tmp), wav.squeeze(0).cpu().numpy(), model.sr, subtype="PCM_16")
    if not tmp.exists() or tmp.stat().st_size < 200:
        raise RuntimeError("file rong")
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
    use_cbx = req.get("engine") == "chatterbox"
    if use_cbx:
        tts = get_cbx(device if device in ("cuda", "cpu") else "cpu")
        lower = False
    else:
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
            if use_cbx:
                _infer_cbx(tts, req, it, gen, out)
            else:
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
    return {"ok": True, "device": CBX.device if use_cbx else State.device, "written": written,
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
                             "engine_ok": ENGINE["ok"], "torch": ENGINE["torch"],
                             "python": sys.executable,
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
    preflight()                     # thieu torch/f5 -> thoat ma 3, KHONG mo cong
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
