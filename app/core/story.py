# -*- coding: utf-8 -*-
"""Dung VIDEO tu ANH theo loi doc — moi nhan vat / boi canh co nhieu goc anh.

Y tuong: kich ban chia thanh cac CANH bang the [cảnh:tên]. Moi canh la mot bo
anh (nhieu goc chup cua cung nhan vat hoac cung boi canh). Tool doc tung doan,
do DO DAI THAT cua tieng doc, roi trai anh len truc thoi gian do:

    doan dai 12 giay + canh co 4 goc  ->  doi goc moi ~4 giay, khong dung mot
    tam anh chet suot ca doan; lan sau quay lai canh do thi bat dau tu goc ke
    tiep nen khong bi lap y het lan truoc.

Ket qua: mp4 + track tieng khop tung doan + file .srt cung moc thoi gian.

Ba che do hinh:
    cut   — cat thang, nhanh nhat, chac an nhat (mac dinh)
    zoom  — Ken Burns: phong/thu nhe, luan phien vao/ra cho do "tinh"
    + chuyen canh mo dan (crossfade) bat rieng, tu tat khi qua nhieu anh
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .. import config
from . import video as video_mod

DATA = Path("data")
SCENE_DIR = DATA / "story_scenes"
CACHE_DIR = DATA / "story_cache"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".heif", ".tif", ".tiff"}

SCENE_TAG_RE = re.compile(r"\[\s*(?:cảnh|canh|scene)\s*:\s*([^\]]+)\]", re.I)
VOICE_TAG_RE = re.compile(r"\[\s*(?:giọng|giong|voice)\s*:\s*([^\]]+)\]", re.I)


def _cfg(key, default):
    return getattr(config, key, default)


def ratios():
    return dict(_cfg("STORY_RATIOS", {"16:9": (1920, 1080), "9:16": (1080, 1920),
                                      "1:1": (1080, 1080), "4:5": (1080, 1350)}))


def canvas(ratio):
    r = ratios()
    return tuple(r.get(ratio) or r["16:9"])


# ================================================================ bo anh (canh)

def safe_name(name):
    import unicodedata
    s = (name or "").strip().lower().replace("đ", "d")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9_\-]+", "_", s).strip("_")[:32]
    return s or "canh"


def scene_dir(name):
    return SCENE_DIR / safe_name(name)


def _meta_path(name):
    return scene_dir(name) / "meta.json"


def scene_meta(name):
    try:
        return json.loads(_meta_path(name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_meta(name, **patch):
    m = scene_meta(name)
    m.update(patch)
    p = _meta_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return m


def scene_images(name):
    d = scene_dir(name)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def list_scenes():
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(SCENE_DIR.iterdir()):
        if not d.is_dir():
            continue
        m = scene_meta(d.name)
        imgs = scene_images(d.name)
        out.append({"name": d.name, "label": m.get("label") or d.name,
                    "count": len(imgs),
                    "images": [p.name for p in imgs],
                    "kind": m.get("kind") or "canh"})
    return out


def probe_image(path):
    """Tra ve (rong, cao) — cung la cach kiem tra file co phai anh doc duoc khong."""
    r = subprocess.run([video_mod._ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)],
                       capture_output=True)
    txt = (r.stderr or b"").decode("utf-8", "replace")
    m = re.search(r"Stream #\d+:\d+.*?:\s*Video:.*?(\d{2,5})x(\d{2,5})", txt, re.S)
    if not m:
        raise ValueError("không đọc được ảnh (định dạng lạ hoặc file hỏng)")
    return int(m.group(1)), int(m.group(2))


def add_image(scene, image_bytes, filename=None, label=None, kind="canh"):
    """Them 1 anh vao bo anh cua canh/nhan vat. Tra ve thong tin bo anh."""
    name = safe_name(scene)
    d = scene_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    ext = (Path(filename or "").suffix or ".jpg").lower()
    if ext not in IMAGE_EXTS:
        ext = ".jpg"
    idx = 1
    while (d / ("img_%03d%s" % (idx, ext))).exists():
        idx += 1
    dst = d / ("img_%03d%s" % (idx, ext))
    dst.write_bytes(image_bytes)
    try:
        w, h = probe_image(dst)
    except Exception as e:
        dst.unlink(missing_ok=True)
        raise ValueError("%s: %s" % (filename or dst.name, e))
    _save_meta(name, label=label or scene_meta(name).get("label") or scene,
               kind=kind, updated=time.time())
    return {"scene": name, "file": dst.name, "size": [w, h],
            "count": len(scene_images(name))}


def delete_scene(scene):
    d = scene_dir(scene)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def delete_image(scene, filename):
    p = scene_dir(scene) / Path(filename).name
    if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
        p.unlink()
        return True
    return False


# ============================================================ chuan hoa khung hinh

def _cache_key(src, w, h):
    st = src.stat()
    return "%s_%d_%d_%dx%d" % (re.sub(r"[^a-zA-Z0-9]+", "_", str(src))[-60:],
                               int(st.st_mtime), st.st_size, w, h)


def fit_image(src, w, h):
    """Anh bat ky -> khung wxh: giu nguyen ti le, nen mo cung anh do cho day khung.

    Cach nay dep hon vien den va khong cat mat mat/nguoi nhu crop.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / (_cache_key(Path(src), w, h) + ".jpg")
    if out.exists() and out.stat().st_size > 1000:
        return out
    vf = ("[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"
          "gblur=sigma=%d,eq=brightness=-0.06[bg];"
          "[0:v]scale=%d:%d:force_original_aspect_ratio=decrease[fg];"
          "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
          % (w, h, w, h, max(8, min(w, h) // 45), w, h))
    r = subprocess.run([video_mod._ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                        "-nostdin", "-i", str(src), "-filter_complex", vf,
                        "-frames:v", "1", "-q:v", "2", str(out)], capture_output=True)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("Không xử lý được ảnh %s: %s"
                           % (Path(src).name, (r.stderr or b"").decode()[-200:]))
    return out


# ================================================================== kich ban

def _split_sentences(text, max_chars):
    """Cat van ban thanh cac doan ngan theo cau/xuong dong (giu nguyen the bieu cam)."""
    parts = []
    for line in re.split(r"\n\s*\n|\n", text):
        line = line.strip()
        if not line:
            continue
        buf = ""
        for piece in re.split(r"(?<=[.!?…:;])\s+", line):
            if not piece.strip():
                continue
            if buf and len(buf) + len(piece) + 1 > max_chars:
                parts.append(buf.strip())
                buf = piece
            else:
                buf = (buf + " " + piece).strip()
        if buf.strip():
            parts.append(buf.strip())
    return parts


def plan(text, default_voice, max_chars=None):
    """Kich ban -> danh sach SHOT {scene, voice, text}. Giu the bieu cam cho TTS."""
    max_chars = int(max_chars or _cfg("STORY_MAX_CHARS", 220))
    tokens = re.split(r"(\[\s*(?:cảnh|canh|scene|giọng|giong|voice)\s*:[^\]]+\])",
                      text or "", flags=re.I)
    scene, voice = None, default_voice
    shots = []
    for tok in tokens:
        if not tok or not tok.strip():
            continue
        m = SCENE_TAG_RE.fullmatch(tok.strip())
        if m:
            scene = safe_name(m.group(1))
            continue
        m = VOICE_TAG_RE.fullmatch(tok.strip())
        if m:
            voice = m.group(1).strip()
            continue
        for piece in _split_sentences(tok, max_chars):
            shots.append({"scene": scene, "voice": voice, "text": piece})
    return shots


def scenes_used(shots):
    seen = []
    for s in shots:
        if s["scene"] and s["scene"] not in seen:
            seen.append(s["scene"])
    return seen


# =================================================================== doc loi

def synth_shot(shot, rate="+0%", target=None, progress=None):
    """Doc 1 shot bang dung engine cua giong do (giu nguyen the bieu cam)."""
    from . import expressive, translate, tts
    text, voice = shot["text"], shot["voice"]
    has_tags = expressive.has_markup(text)
    if has_tags and voice.startswith("edge:"):
        data, _mime = expressive.synth_expressive(text, voice, rate, target=target,
                                                  progress=progress)
        return data
    if has_tags and voice.startswith("gemini:"):
        data, _mime = expressive.synth_expressive_gemini(text, voice.split(":", 1)[1],
                                                         target=target, progress=progress)
        return data
    if has_tags and voice.startswith("vieneu:"):
        text = expressive.vieneu_script(text)
    clean = expressive.strip_tags(text)
    if target:
        clean = translate.translate_text(clean, target)
    if voice.startswith("edge:"):
        data, _mime = expressive.synth_human(clean, voice, rate, progress=progress)
        return data
    data, _mime, _ext = tts.synth(clean, voice, rate, progress=progress)
    return data


def narrate(shots, rate="+0%", target=None, gap=None, progress=None):
    """Doc het cac shot -> (pcm track, cac shot da co start/seconds).

    Track am thanh dung chung sample rate voi module video (24kHz mono s16).
    """
    gap = float(_cfg("STORY_GAP", 0.35) if gap is None else gap)
    sr = video_mod.SR
    track = bytearray()
    n = len(shots)
    for i, shot in enumerate(shots):
        data = synth_shot(shot, rate=rate, target=target)
        pcm = video_mod._decode_pcm(data) if data else b""
        shot["start"] = len(track) / 2.0 / sr
        shot["seconds"] = len(pcm) / 2.0 / sr
        track += pcm
        pause = gap * (1.6 if (i + 1 < n and shots[i + 1]["scene"] != shot["scene"]) else 1.0)
        track += b"\x00\x00" * int(sr * pause)
        shot["hold"] = shot["seconds"] + pause
        if progress:
            progress(i + 1, n)
    return bytes(track), shots


# ================================================================ trai anh

def slots(shots, angle_every=None, fallback_scene=None):
    """Trai anh len truc thoi gian: doan dai thi doi goc, quay lai canh cu thi doi goc khac."""
    angle_every = float(_cfg("STORY_ANGLE_EVERY", 4.5) if angle_every is None else angle_every)
    pool = {s["name"]: scene_images(s["name"]) for s in list_scenes()}
    everything = [p for imgs in pool.values() for p in imgs]
    cursor = {}
    out = []
    for shot in shots:
        imgs = pool.get(shot["scene"] or "") or []
        if not imgs and fallback_scene:
            imgs = pool.get(safe_name(fallback_scene)) or []
        if not imgs:
            imgs = everything
        if not imgs:
            raise ValueError("Chưa có ảnh nào — hãy tải ảnh lên cho ít nhất một cảnh.")
        dur = max(0.6, float(shot.get("hold") or shot.get("seconds") or 1.0))
        want = max(1, int(round(dur / angle_every)))
        want = min(want, len(imgs))
        each = dur / want
        key = shot["scene"] or "_"
        for k in range(want):
            idx = cursor.get(key, 0)
            cursor[key] = (idx + 1) % len(imgs)
            out.append({"image": imgs[idx], "seconds": each,
                        "scene": shot["scene"], "text": shot["text"]})
    return out


# ================================================================== dung video

def _run(args, timeout=None, input_bytes=None):
    r = subprocess.run([video_mod._ffmpeg(), "-y", "-hide_banner", "-loglevel", "error"]
                       + ([] if input_bytes is not None else ["-nostdin"]) + args,
                       input=input_bytes, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace").strip()[-400:]
                           or "ffmpeg lỗi")
    return r


def _concat_list(path, entries):
    """Viet file danh sach cho concat demuxer — duong dan TUYET DOI, co escape nhay."""
    lines = []
    for item, dur in entries:
        f = Path(item).resolve().as_posix().replace("'", "'\\''")
        lines.append("file '%s'\n" % f)
        if dur is not None:
            lines.append("duration %.3f\n" % dur)
    Path(path).write_text("".join(lines), encoding="utf-8")
    return path


def _write_wav(pcm, path):
    import struct
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
                + struct.pack("<IHHIIHH", 16, 1, 1, video_mod.SR, video_mod.SR * 2, 2, 16)
                + b"data" + struct.pack("<I", len(pcm)) + pcm)
    return path


def _zoom_clip(img, seconds, w, h, fps, out, zoom_in=True):
    """1 anh -> 1 clip co chuyen dong Ken Burns nhe."""
    frames = max(2, int(round(seconds * fps)))
    amp = float(_cfg("STORY_ZOOM", 0.10))
    if zoom_in:
        z = "min(1+%0.6f*on/%d,%0.4f)" % (amp, frames, 1 + amp)
    else:
        z = "max(%0.4f-%0.6f*on/%d,1.0)" % (1 + amp, amp, frames)
    vf = ("scale=%d:%d,zoompan=z='%s':d=%d:s=%dx%d:fps=%d:x='iw/2-(iw/zoom/2)':"
          "y='ih/2-(ih/zoom/2)',format=yuv420p" % (w * 2, h * 2, z, frames, w, h, fps))
    _run(["-loop", "1", "-i", str(img), "-vf", vf, "-t", "%.3f" % seconds,
          "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
          "-an", str(out)])
    return out


def render(slot_list, pcm, out_path, ratio="16:9", motion="cut", transition=0.0,
           fps=None, progress=None, log=None):
    """Lap rap anh + tieng doc thanh mp4."""
    say = log or (lambda *_a: None)
    w, h = canvas(ratio)
    fps = int(fps or _cfg("STORY_FPS", 30))
    xfade_cap = int(_cfg("STORY_XFADE_MAX", 60))
    if transition > 0 and len(slot_list) > xfade_cap:
        say("Quá %d cảnh — tắt chuyển cảnh mờ cho nhẹ máy, dùng cắt thẳng." % xfade_cap)
        transition = 0.0
    n = len(slot_list)
    done = [0]

    def tick():
        done[0] += 1
        if progress:
            progress(done[0], n + 1)

    with tempfile.TemporaryDirectory(prefix="story_") as td:
        td = Path(td)
        audio = _write_wav(pcm, td / "voice.wav")

        fitted = []
        for s in slot_list:
            fitted.append(fit_image(s["image"], w, h))
            tick()

        if motion == "zoom" or transition > 0:
            clips = []
            for i, (s, img) in enumerate(zip(slot_list, fitted)):
                c = td / ("c%04d.mp4" % i)
                dur = s["seconds"] + (transition if i + 1 < n else 0)
                if motion == "zoom":
                    _zoom_clip(img, dur, w, h, fps, c, zoom_in=(i % 2 == 0))
                else:
                    _run(["-loop", "1", "-i", str(img), "-t", "%.3f" % dur, "-r", str(fps),
                          "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                          "-crf", "20", "-an", str(c)])
                clips.append(c)

            if transition > 0 and n > 1:
                args, filt, off, prev = [], [], 0.0, "[0:v]"
                for c in clips:
                    args += ["-i", str(c)]
                # offset = moc thoi gian THAT cua canh ke tiep; moi clip da duoc keo
                # dai them dung bang thoi gian chuyen canh nen tong video = tong tieng
                for i in range(1, n):
                    off += slot_list[i - 1]["seconds"]
                    lbl = "[x%d]" % i
                    filt.append("%s[%d:v]xfade=transition=fade:duration=%.3f:offset=%.3f%s"
                                % (prev, i, transition, off, lbl))
                    prev = lbl
                _run(args + ["-i", str(audio), "-filter_complex", ";".join(filt),
                             "-map", prev, "-map", "%d:a" % n,
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                             "-pix_fmt", "yuv420p", "-r", str(fps),
                             "-c:a", "aac", "-b:a", "160k", "-shortest", str(out_path)])
            else:
                lst = _concat_list(td / "list.txt", [(c, None) for c in clips])
                _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
                      "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
                      str(out_path)])
        else:
            entries = [(img, sl["seconds"]) for sl, img in zip(slot_list, fitted)]
            entries.append((fitted[-1], None))      # concat demuxer can nhac lai anh cuoi
            lst = _concat_list(td / "list.txt", entries)
            _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
                  "-vf", "fps=%d,format=yuv420p" % fps,
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                  "-c:a", "aac", "-b:a", "160k", "-shortest", str(out_path)])
    if progress:
        progress(n + 1, n + 1)
    return Path(out_path)


def encode_audio(pcm, out_path=None):
    """PCM track -> m4a (AAC) de nghe/tai rieng phan tieng doc."""
    args = ["-f", "s16le", "-ac", "1", "-ar", str(video_mod.SR), "-i", "pipe:0",
            "-c:a", "aac", "-b:a", "160k"]
    if out_path:
        _run(args + [str(out_path)], input_bytes=pcm)
        return Path(out_path).read_bytes()
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as f:
        tmp = f.name
    try:
        _run(args + ["-y", tmp], input_bytes=pcm)
        return Path(tmp).read_bytes()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def srt(shots):
    from .engine import fmt_ts
    out = []
    for i, s in enumerate(shots, 1):
        start = s.get("start", 0.0)
        end = start + max(0.5, s.get("seconds", 1.0))
        text = re.sub(r"\[.*?\]", "", s["text"])
        text = re.sub(r"\*(.*?)\*", r"\1", text).strip()
        out.append("%d\n%s --> %s\n%s\n" % (i, fmt_ts(start), fmt_ts(end), text))
    return "\n".join(out)
