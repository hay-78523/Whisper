# -*- coding: utf-8 -*-
"""Tao giong doc (text -> audio), mien phi.

2 engine:
  edge:<voice>  giong neural Microsoft Edge — chat luong cao, can mang, khong can key.
                Text dai duoc cat khuc theo cau va tao song song roi ghep lai.
  say:<voice>   giong offline cua macOS (vd: say:Linh).
"""

import asyncio
import io
import re
import subprocess
import tempfile
import time
from pathlib import Path

from ..config import DEFAULT_VOICE, TTS_CHUNK_CHARS, TTS_CONCURRENCY


def split_text(text, max_chars=TTS_CHUNK_CHARS):
    """Cat text thanh khuc theo ranh gioi cau, moi khuc <= max_chars."""
    parts = re.split(r"(?<=[\.\!\?…;])\s+|\n+", text)
    chunks, cur = [], ""
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        if cur and len(cur) + len(p) + 1 > max_chars:
            chunks.append(cur)
            cur = ""
        cur = (cur + " " + p).strip() if cur else p
        while len(cur) > max_chars:  # cau don qua dai, cat cung
            chunks.append(cur[:max_chars])
            cur = cur[max_chars:].strip()
    if cur:
        chunks.append(cur)
    return chunks or [text]


def _synth_edge(text, voice, rate, progress=None, log=None):
    import edge_tts

    name, pitch = _edge_parts(voice)
    chunks = split_text(text)
    done = [0]
    if progress:
        progress(0, len(chunks))

    async def _one(sem, i, chunk):
        async with sem:
            for attempt in range(3):
                try:
                    buf = b""
                    com = edge_tts.Communicate(chunk, name, rate=rate, pitch=pitch)
                    async for msg in com.stream():
                        if msg["type"] == "audio":
                            buf += msg["data"]
                    if not buf:
                        raise RuntimeError("khong nhan duoc audio")
                    done[0] += 1
                    if log:
                        log("khuc %d/%d xong" % (done[0], len(chunks)))
                    if progress:
                        progress(done[0], len(chunks))
                    return i, buf
                except Exception as e:
                    print("EXCEPTION IN SYNTH:", e)
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))

    async def _all():
        sem = asyncio.Semaphore(TTS_CONCURRENCY)
        results = await asyncio.gather(*[_one(sem, i, c) for i, c in enumerate(chunks)])
        return b"".join(b for _, b in sorted(results))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_all())
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _edge_parts(voice):
    """'edge:NAME@-12Hz' -> (NAME, '-12Hz'); khong co @ thi pitch mac dinh."""
    v = voice.split(":", 1)[1] if voice.startswith("edge:") else voice
    if "@" in v:
        name, pitch = v.split("@", 1)
        return name, pitch
    return v, "+0Hz"


def _gtts_one(text, lang):
    from gtts import gTTS
    buf = io.BytesIO()
    for attempt in range(3):
        try:
            gTTS(text, lang=lang).write_to_fp(buf)
            return buf.getvalue()
        except Exception as e:
            print("EXCEPTION IN SYNTH:", e)
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def _synth_gtts(text, lang, progress=None):
    """Giong Google Dich (mien phi, can mang). Khong chinh duoc toc do."""
    chunks = split_text(text)
    if progress:
        progress(0, len(chunks))
    out = b""
    for i, c in enumerate(chunks):
        out += _gtts_one(c, lang)
        if progress:
            progress(i + 1, len(chunks))
    return out


def _gemini_tts_one(text, voice_name, style=None):
    """Goi Gemini TTS 1 lan -> PCM bytes. Retry loi tam thoi (429/5xx)."""
    import base64
    import json as _json
    import urllib.request
    import urllib.error
    from .. import config
    from .translate import _gemini_key

    if style:
        text = style + "\n\n" + text
    body = {"contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}}}}
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"
                % (config.GEMINI_TTS_MODEL, _gemini_key()),
                data=_json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            r = _json.loads(urllib.request.urlopen(req, timeout=180).read())
            part = r["candidates"][0]["content"]["parts"][0]["inlineData"]
            return base64.b64decode(part["data"])
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                last = ValueError(
                    "Giọng Gemini đã chạm giới hạn miễn phí (thường reset sau nửa đêm "
                    "giờ Mỹ). Thử lại sau, thêm key vào GEMINI_API_KEYS, hoặc dùng nhóm "
                    "giọng Tiếng Việt/Đa ngôn ngữ — biểu cảm vẫn hoạt động.")
                if e.code == 429:
                    from .translate import rotate_key
                    if rotate_key():
                        continue           # thu ngay key khac, khong cho
                time.sleep(12 * (attempt + 1))     # free tier RPM thap — cho lau chut
                continue
            raise
        except (KeyError, IndexError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise last


def _concat_clips(clips):
    '''Ghep cac clip audio thanh 1 file wav hoac mp3 (tuy dinh dang dau vao).'''
    clips = [c for c in clips if c]
    if not clips:
        raise ValueError("Khong co audio de ghep.")
        
    is_wav = clips[0].startswith(b'RIFF')
    if is_wav:
        # Pure Python raw WAV concatenation (supports float32, etc.)
        import struct
        out_data = b""
        header = None
        
        for c in clips:
            data_idx = c.find(b'data')
            if data_idx == -1:
                continue
            
            data_size = struct.unpack('<I', c[data_idx+4:data_idx+8])[0]
            out_data += c[data_idx+8:data_idx+8+data_size]
            
            if header is None:
                header = c[:data_idx+4]
                
        if not header:
            raise ValueError("Invalid WAV clips.")
            
        total_data_size = len(out_data)
        final_file = bytearray(header)
        total_file_size = len(header) + 4 + total_data_size
        final_file[4:8] = struct.pack('<I', total_file_size - 8)
        final_file += struct.pack('<I', total_data_size)
        final_file += out_data
        
        return bytes(final_file), "audio/wav", ".wav"
    else:
        # For MP3 (ElevenLabs, Edge-TTS), we can just simply append the bytes!
        # MP3 streams can be concatenated byte-by-byte (ID3 tags might be repeated, but players can handle it)
        return b"".join(clips), "audio/mpeg", ".mp3"


def _pcm_to_wav(pcm):
    import struct
    header = b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVEfmt ' + \
             struct.pack('<IHHIIHH', 16, 1, 1, 24000, 24000 * 2, 2, 16) + \
             b'data' + struct.pack('<I', len(pcm))
    return header + pcm


_gemini_tts_cool = [0.0]     # quota chet -> nghi 10 phut, khoi thu lai tung call


def _gemini_quota_resting():
    import time as _t
    return _t.time() < _gemini_tts_cool[0]


def _gemini_mark_quota_dead():
    import time as _t
    _gemini_tts_cool[0] = _t.time() + 600


def _persona(pid):
    from .. import config
    return (getattr(config, "PERSONA_VOICES", {}) or {}).get(pid)


def _combine_rate(rate, delta):
    """'+0%' + (-10) -> '-10%' — cong toc do nguoi dung voi chat nhan vat."""
    import re as _re
    base = int(_re.sub(r"[^\d+-]", "", rate or "+0%") or 0)
    return "%+d%%" % max(-40, min(60, base + int(delta or 0)))


def _gemini_fallback_voice(name):
    """Giong free gan chat nhat de thay khi AI Studio het quota."""
    from .. import config
    fb = (getattr(config, "GEMINI_TTS_FALLBACK", {}) or {}).get(name)
    return fb or getattr(config, "GEMINI_TTS_FALLBACK_DEFAULT", "edge:vi-VN-HoaiMyNeural")


def _gemini_style(text):
    """Boc chi dan phong cach de doc TRUYEN CAM ca khi khong co the bieu cam."""
    from .. import config
    style = (getattr(config, "GEMINI_TTS_STYLE", "") or "").strip()
    return ("Nói một cách %s: %s" % (style, text)) if style else text


def _synth_gemini(text, voice_name, progress=None):
    """Doc text (co the dai) bang AI Studio TTS — het quota tu doi giong free."""
    from .translate import has_gemini
    if not has_gemini():
        raise ValueError("Giọng AI Studio cần key Gemini trong config (mục Dịch AI của README).")
    chunks = split_text(text, max_chars=3000)
    if progress:
        progress(0, len(chunks))
    try:
        if _gemini_quota_resting():
            raise ValueError("quota dang nghi")
        pcm = b""
        for i, c in enumerate(chunks):
            pcm += _gemini_tts_one(_gemini_style(c), voice_name)
            if progress:
                progress(i + 1, len(chunks))
        return _pcm_to_wav(pcm)
    except ValueError:
        _gemini_mark_quota_dead()
        fb = _gemini_fallback_voice(voice_name)
        try:
            data, _m, _e = synth(text, fb, progress=progress)
        except Exception as e:
            print("EXCEPTION IN SYNTH:", e)
            data, _m, _e = synth(text, "edge:vi-VN-HoaiMyNeural", progress=progress)
        return data



def _eleven_key():
    import os
    from .. import config
    return (os.environ.get("ELEVEN_API_KEY", "")
            or getattr(config, "ELEVEN_API_KEY", "") or "").strip()


def _eleven_one(text, voice_id):
    """Doc 1 khuc bang ElevenLabs — tra ve mp3 bytes. Loi key/quota -> bao ro."""
    import requests
    from .. import config
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/" + voice_id,
        headers={"xi-api-key": _eleven_key(), "Content-Type": "application/json"},
        json={"text": text,
              "model_id": getattr(config, "ELEVEN_MODEL", "eleven_multilingual_v2")},
        timeout=180)
    if r.status_code == 401:
        raise ValueError("Key ElevenLabs sai hoặc hết hạn — kiểm tra ELEVEN_API_KEY trong config.")
    if r.status_code in (402, 429):
        raise ValueError("ElevenLabs hết hạn mức tháng này (gói free ~10k ký tự/tháng) — "
                         "dùng nhóm giọng khác hoặc đợi sang tháng.")
    r.raise_for_status()
    return r.content


_eleven_cache = [0.0, None]


def eleven_voices():
    """Giong trong tai khoan ElevenLabs cua nguoi dung (cache 5 phut).

    Vao elevenlabs.io -> Voice Library -> Add giong nao thich (vd 'Thắm')
    -> giong do TU HIEN trong dropdown cua tool.
    """
    if not _eleven_key():
        return []
    import time as _t
    if _t.time() - _eleven_cache[0] < 300 and _eleven_cache[1] is not None:
        return _eleven_cache[1]
    out = []
    try:
        import requests
        r = requests.get("https://api.elevenlabs.io/v1/voices",
                         headers={"xi-api-key": _eleven_key()}, timeout=15)
        r.raise_for_status()
        for v in r.json().get("voices", []):
            lab = v.get("labels") or {}
            bits = [x for x in (lab.get("gender"), lab.get("age"), lab.get("accent"),
                                lab.get("description")) if x]
            style = ", ".join(bits[:3]) or "ElevenLabs"
            out.append(("eleven:" + v["voice_id"], v.get("name") or v["voice_id"], style))
    except Exception as e:
        print("EXCEPTION IN SYNTH:", e)
        out = []
    _eleven_cache[:] = [_t.time(), out]
    return out


def _synth_say(text, voice):
    with tempfile.TemporaryDirectory() as td:
        aiff = Path(td) / "v.aiff"
        m4a = Path(td) / "v.m4a"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(aiff), str(m4a)],
                       check=True, capture_output=True)
        return m4a.read_bytes()


def synth_cues(texts, voice, rate="+0%", progress=None):
    """Doc TUNG doan rieng (song song) — tra ve list bytes mp3 theo dung thu tu.

    Dung cho long tieng can khop thoi gian. Chi ho tro giong edge.
    """
    import edge_tts

    if voice.startswith("vieneu:"):
        from . import vieneu_engine
        return vieneu_engine.synth_cues_vieneu(texts, voice=voice.split(":", 1)[1], progress=progress)
    if voice.startswith("c:"):
        from . import clone
        return clone.synth_cues_clone(texts, voice.split(":", 1)[1], rate=rate, progress=progress)
    if voice.startswith("persona:"):
        p = _persona(voice.split(":", 1)[1])
        if not p:
            raise ValueError("Nhân vật không tồn tại.")
        from .translate import has_gemini
        rate2 = _combine_rate(rate, p.get("fallback_rate", 0))
        if has_gemini() and not _gemini_quota_resting():
            try:
                clips = []
                if progress:
                    progress(0, len(texts))
                wrap = p.get("wrap") or "Nói một cách %s: %s"
                for i, t in enumerate(texts):
                    if (t or "").strip():
                        styled = wrap % (p["gemini_style"], t)
                        clips.append(_pcm_to_wav(_gemini_tts_one(styled, p["gemini"])))
                    else:
                        clips.append(b"")
                    if progress:
                        progress(i + 1, len(texts))
                return clips
            except ValueError:
                _gemini_mark_quota_dead()
        try:
            return synth_cues(texts, p["fallback"], rate2, progress)
        except Exception as e:
            print("EXCEPTION IN SYNTH:", e)
            return synth_cues(texts, "edge:vi-VN-HoaiMyNeural", rate2, progress)
    if voice.startswith("gemini:"):
        name = voice.split(":", 1)[1]
        clips = []
        if progress:
            progress(0, len(texts))
        try:
            if _gemini_quota_resting():
                raise ValueError("quota dang nghi")
            for i, t in enumerate(texts):     # tuan tu — free tier RPM thap
                clips.append(_pcm_to_wav(_gemini_tts_one(_gemini_style(t), name))
                             if (t or "").strip() else b"")
                if progress:
                    progress(i + 1, len(texts))
            return clips
        except ValueError:                    # het quota -> giong free gan chat nhat
            _gemini_mark_quota_dead()
            fb = _gemini_fallback_voice(name)
            try:
                return synth_cues(texts, fb, rate, progress)
            except Exception as e:
                print("EXCEPTION IN SYNTH:", e)
                return synth_cues(texts, "edge:vi-VN-HoaiMyNeural", rate, progress)
    if voice.startswith("eleven:"):
        vid = voice.split(":", 1)[1]
        clips = []
        if progress:
            progress(0, len(texts))
        for i, t in enumerate(texts):     # tuan tu — tiet kiem han muc free
            clips.append(_eleven_one(t, vid) if (t or "").strip() else b"")
            if progress:
                progress(i + 1, len(texts))
        return clips
    if voice.startswith("say:"):
        raise ValueError("Long tieng can khop thoi gian can giong online (edge/gtts).")
    if voice.startswith("gtts:"):
        lang = voice.split(":", 1)[1]
        clips = []
        if progress:
            progress(0, len(texts))
        for i, t in enumerate(texts):
            clips.append(_gtts_one(t, lang) if (t or "").strip() else b"")
            if progress:
                progress(i + 1, len(texts))
        return clips
    name, pitch = _edge_parts(voice)
    done = [0]
    if progress:
        progress(0, len(texts))

    async def _one(sem, i, t):
        async with sem:
            if not (t or "").strip():
                return i, b""
            for attempt in range(3):
                try:
                    buf = b""
                    com = edge_tts.Communicate(t, name, rate=rate, pitch=pitch)
                    async for msg in com.stream():
                        if msg["type"] == "audio":
                            buf += msg["data"]
                    if not buf:
                        raise RuntimeError("khong nhan duoc audio")
                    done[0] += 1
                    if progress:
                        progress(done[0], len(texts))
                    return i, buf
                except Exception as e:
                    print("EXCEPTION IN SYNTH:", e)
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 * (attempt + 1))

    async def _all():
        sem = asyncio.Semaphore(TTS_CONCURRENCY)
        results = await asyncio.gather(*[_one(sem, i, t) for i, t in enumerate(texts)])
        return [b for _, b in sorted(results)]

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_all())
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def synth_cues_multi(texts, voices, rate="+0%", progress=None):
    """Doc nhieu cau bang NHIEU giong khac nhau (ho tro tron cac engine).
    Gom cac cau lien tiep cung giong de toi uu, tra ve list clips giong synth_cues()."""
    if not texts:
        return []
    if len(texts) != len(voices):
        raise ValueError("Số lượng texts và voices phải bằng nhau.")

    groups = []
    cur_voice = None
    cur_group = []

    for i, (t, v) in enumerate(zip(texts, voices)):
        if v != cur_voice and cur_group:
            groups.append((cur_voice, cur_group))
            cur_group = []
        cur_voice = v
        cur_group.append((i, t))
    if cur_group:
        groups.append((cur_voice, cur_group))

    results = [None] * len(texts)
    done_texts = [0]

    for v, group in groups:
        group_texts = [t for _, t in group]
        
        def group_progress(d, t):
            if progress:
                progress(done_texts[0] + d, len(texts))
                
        clips = synth_cues(group_texts, v, rate, progress=group_progress)
        
        for (i, _), clip in zip(group, clips):
            results[i] = clip
            
        done_texts[0] += len(group)
        
    return results


def synth(text, voice=DEFAULT_VOICE, rate="+0%", out_path=None, progress=None, log=None):
    """Sinh audio tu text. Tra ve (bytes|None neu ghi file, mime, ext)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Van ban rong.")
    if voice.startswith("vieneu:"):
        from . import vieneu_engine as _vn
        chunks = split_text(text, max_chars=600)
        clips = _vn.synth_cues_vieneu(chunks, voice=voice.split(":", 1)[1], progress=progress)
        data, mime, ext = _concat_clips([c for c in clips if c])
        if out_path:
            Path(out_path).write_bytes(data)
            return None, mime, ext
        return data, mime, ext
    if voice.startswith("persona:"):
        p = _persona(voice.split(":", 1)[1])
        if not p:
            raise ValueError("Nhân vật không tồn tại.")
        from .translate import has_gemini
        rate2 = _combine_rate(rate, p.get("fallback_rate", 0))
        data = None
        if has_gemini() and not _gemini_quota_resting() and not p.get("force_fallback"):
            try:
                chunks = split_text(text, max_chars=3000)
                if progress:
                    progress(0, len(chunks))
                pcm = b""
                wrap = p.get("wrap") or "Nói một cách %s: %s"
                for i, ch in enumerate(chunks):
                    styled = wrap % (p["gemini_style"], ch)
                    pcm += _gemini_tts_one(styled, p["gemini"])
                    if progress:
                        progress(i + 1, len(chunks))
                data = _pcm_to_wav(pcm)
            except ValueError:
                _gemini_mark_quota_dead()
        if data is None:
            try:
                if p["fallback"].startswith("edge:vi-"):
                    from . import expressive     # doc "nhu nguoi" — het deu deu robot
                    data, _m = expressive.synth_human(text, p["fallback"], rate2,
                                                      progress=progress)
                else:
                    data, _m, _e = synth(text, p["fallback"], rate2, progress=progress)
            except Exception as e:
                print("EXCEPTION IN SYNTH:", e)
                data, _m, _e = synth(text, "edge:vi-VN-HoaiMyNeural", rate2, progress=progress)
        if out_path:
            Path(out_path).write_bytes(data)
            return None, "audio/wav", ".wav"
        return data, "audio/wav", ".wav"
    if voice.startswith("gemini:"):
        data = _synth_gemini(text, voice.split(":", 1)[1], progress)
        if out_path:
            Path(out_path).write_bytes(data)
            return None, "audio/wav", ".wav"
        return data, "audio/wav", ".wav"
    if voice.startswith("eleven:"):
        vid = voice.split(":", 1)[1]
        chunks = split_text(text, max_chars=2400)
        if progress:
            progress(0, len(chunks))
        clips = []
        for i, c in enumerate(chunks):
            clips.append(_eleven_one(c, vid))
            if progress:
                progress(i + 1, len(chunks))
        data, mime, ext = _concat_clips(clips)
        if out_path:
            Path(out_path).write_bytes(data)
            return None, mime, ext
        return data, mime, ext
    if voice.startswith("c:"):
        from . import clone
        chunks = split_text(text, max_chars=500)   # doan ngan de co tien do + on dinh
        clips = clone.synth_cues_clone(chunks, voice.split(":", 1)[1], rate=rate, progress=progress)

        data, mime, ext = _concat_clips(clips)
    elif voice.startswith("say:"):
        data, mime, ext = _synth_say(text, voice.split(":", 1)[1]), "audio/mp4", ".m4a"
        if progress:
            progress(1, 1)
    elif voice.startswith("gtts:"):
        data, mime, ext = _synth_gtts(text, voice.split(":", 1)[1], progress), "audio/mpeg", ".mp3"
    else:
        v = voice.split(":", 1)[1] if voice.startswith("edge:") else voice
        data, mime, ext = _synth_edge(text, v, rate, progress, log), "audio/mpeg", ".mp3"
    if out_path:
        Path(out_path).write_bytes(data)
        return None, mime, ext
    return data, mime, ext
