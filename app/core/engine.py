# -*- coding: utf-8 -*-
"""Loi phien am (Whisper qua faster-whisper) + tao phu de SRT.

Model duoc cache trong bo nho; batched inference + VAD + da luong CPU
duoc bat san de dat toc do tot nhat tren may khong co GPU NVIDIA.
"""

import os
import threading
from pathlib import Path

from ..config import BATCH_SIZE, MODELS

AUDIO_EXTS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".webm", ".mp4", ".mov", ".mkv", ".aiff", ".aif", ".wma", ".amr", ".3gp",
}

_models = {}
_lock = threading.Lock()


def get_model(name):
    """Tra ve model da cache; nap (va tai ve neu can) o lan goi dau."""
    if name not in MODELS:
        raise ValueError("Model khong hop le: %s" % name)
    with _lock:
        if name not in _models:
            from faster_whisper import WhisperModel
            threads = max(4, os.cpu_count() or 4)
            _models[name] = WhisperModel(name, device="cpu",
                                         compute_type="int8", cpu_threads=threads)
        return _models[name]


def loaded_models():
    return sorted(_models.keys())


def downloaded_models():
    """Cac model da co san tren dia (trong cache Hugging Face)."""
    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    found = set()
    if cache.is_dir():
        for d in cache.glob("models--*faster-whisper*"):
            for name in MODELS:
                if d.name.endswith("faster-whisper-" + name):
                    found.add(name)
    return sorted(found)


def transcribe(model, path, language=None, progress=None):
    """Phien am 1 file. `progress(giay_da_xu_ly, tong_giay)` goi theo tung segment.

    Uu tien batched inference + VAD (nhanh 2-4x, bo khoang lang, giam ao giac);
    tu fallback ve che do thuong neu moi truong thieu onnxruntime.
    """
    try:
        from faster_whisper import BatchedInferencePipeline
        segments, info = BatchedInferencePipeline(model).transcribe(
            str(path), language=language, word_timestamps=True, batch_size=BATCH_SIZE)
    except Exception:
        try:
            segments, info = model.transcribe(str(path), language=language,
                                              word_timestamps=True, vad_filter=True)
        except Exception:
            segments, info = model.transcribe(str(path), language=language,
                                              word_timestamps=True)

    words, texts = [], []
    for seg in segments:
        texts.append(seg.text)
        for w in (seg.words or []):
            words.append({"text": w.word, "start": round(w.start, 3),
                          "end": round(w.end, 3), "type": "word"})
        if progress and info.duration:
            progress(min(seg.end, info.duration), info.duration)
    return {
        "text": "".join(texts).strip(),
        "words": words,
        "language_code": info.language,
        "language_probability": info.language_probability,
        "duration": round(info.duration, 2),
    }


def fmt_ts(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def words_to_cues(words, max_chars=84, max_dur=6.0, gap=0.8):
    """Gom words (co start/end) thanh cac doan (cue): cat khi nghi dai, qua dai, het cau.

    Tra ve list dict {start, end, text} — dung cho ca phu de SRT lan long tieng
    can khop thoi gian.
    """
    cues, text, start, end = [], "", None, None
    for w in words:
        t = w.get("text", "")
        ws, we = w.get("start"), w.get("end")
        if ws is None or we is None:
            text += t
            continue
        long_pause = end is not None and ws - end > gap
        too_long = start is not None and we - start > max_dur
        too_wide = len(text) + len(t) > max_chars
        if text.strip() and (long_pause or too_long or too_wide):
            cues.append({"start": start, "end": end, "text": text.strip()})
            text, start = "", None
        if start is None:
            start = ws
        end = we
        text += t
        if text.strip() and text.rstrip()[-1:] in ".!?…" and len(text) > 30:
            cues.append({"start": start, "end": end, "text": text.strip()})
            text, start = "", None
    if text.strip():
        cues.append({"start": start or 0.0, "end": end or 0.0, "text": text.strip()})
    return cues


def words_to_srt(words, max_chars=84, max_dur=6.0, gap=0.8):
    """Xuat phu de SRT tu words (dua tren words_to_cues)."""
    cues = words_to_cues(words, max_chars, max_dur, gap)
    return "\n".join("%d\n%s --> %s\n%s\n" % (i, fmt_ts(c["start"]), fmt_ts(c["end"]), c["text"])
                     for i, c in enumerate(cues, 1))
