# -*- coding: utf-8 -*-
"""Giong doc BIEU CAM — "bien dich" van ban co the tag thanh giong len xuong,
nhan nha, ngap ngung, cuoi... roi ghep muot thanh 1 file.

Cu phap trong van ban:
    [vui] [buon] [hao hung] [nghiem tuc] [ke chuyen] [thi tham] [binh thuong]
        -> doi tong giong tu vi tri do (giu den khi doi tiep)
    *tu can nhan*      -> nhan manh: cham lai + cao giong + nghi nhe 2 ben
    [nghi] [nghi dai]  -> khoang lang 0.6s / 1.2s;  "..." / "…" -> 0.45s
    [um] [a]           -> am u ngap ngung tu nhien
    [cuoi] [cuoi nhe] [tho dai] -> bieu cam
Chi ho tro giong edge (co chinh rate/pitch). Giong khac -> strip_tags() doc thuong.
"""

import asyncio
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from ..config import TTS_CONCURRENCY
from .tts import _edge_parts, split_text
from .video import SR, _decode_pcm

# (rate %, pitch Hz) theo tong giong
EMOTIONS = {
    "binh thuong": (0, 0), "vui": (10, 14), "hao hung": (20, 23),
    "buon": (-17, -15), "nghiem tuc": (-9, -5), "ke chuyen": (-6, 3),
    "thi tham": (-15, -13),
    "gian du": (16, -5), "so hai": (12, 16), "ngac nhien": (8, 20),
    "diu dang": (-10, 5), "nung niu": (4, 18), "met moi": (-18, -10),
    "bi an": (-13, -7), "gap gap": (25, 6), "lanh lung": (-8, -12),
    "tu hao": (-4, 7), "cham biem": (-6, 10),
}
# tag -> (chu doc len, rate %, pitch Hz, nghi truoc ms, nghi sau ms)
EVENTS = {
    "cuoi": ("ha ha ha!", 12, 16, 150, 250),
    "cuoi nhe": ("hì hì.", 8, 10, 120, 200),
    "um": ("ừmm…", -28, -8, 250, 350),
    "a": ("àa…", -22, -6, 200, 300),
    "tho dai": ("haiz…", -25, -10, 250, 400),
    "khoc": ("hu hu hu…", -15, -6, 250, 400),
    "wow": ("oa!", 10, 18, 100, 200),
    "hang giong": ("khụ khụ.", -10, -5, 150, 250),
    "suyt": ("suỵt…", -22, -10, 150, 300),
}
PAUSES = {"nghi ngan": 250, "nghi": 450, "nghi dai": 800}

# --- The tieng Anh (kich ban tieng Anh viet [happy], [pause], [laugh]...) ---
_EN_EMO = {"normal": "binh thuong", "neutral": "binh thuong", "happy": "vui",
           "cheerful": "vui", "excited": "hao hung", "sad": "buon", "serious": "nghiem tuc",
           "storytelling": "ke chuyen", "narrator": "ke chuyen", "whisper": "thi tham",
           "whispering": "thi tham", "angry": "gian du", "scared": "so hai", "afraid": "so hai",
           "surprised": "ngac nhien", "gentle": "diu dang", "soft": "diu dang",
           "tender": "nung niu", "tired": "met moi", "mysterious": "bi an",
           "urgent": "gap gap", "hurried": "gap gap", "cold": "lanh lung", "proud": "tu hao",
           "sarcastic": "cham biem"}
_EN_EVT = {"laugh": "cuoi", "laughs": "cuoi", "laughing": "cuoi", "chuckle": "cuoi nhe",
           "giggle": "cuoi nhe", "um": "um", "umm": "um", "uh": "a", "hmm": "um",
           "sigh": "tho dai", "sighs": "tho dai", "cry": "khoc", "sob": "khoc", "wow": "wow",
           "cough": "hang giong", "ahem": "hang giong", "shh": "suyt", "shush": "suyt"}
_EN_PAUSE = {"short pause": "nghi ngan", "pause": "nghi", "long pause": "nghi dai",
             "beat": "nghi ngan"}
for _k, _v in _EN_EMO.items():
    EMOTIONS.setdefault(_k, EMOTIONS[_v])
for _k, _v in _EN_EVT.items():
    EVENTS.setdefault(_k, EVENTS[_v])
for _k, _v in _EN_PAUSE.items():
    PAUSES.setdefault(_k, PAUSES[_v])
_EMO_CANON = dict(_EN_EMO)      # ten the tieng Anh -> ten goc (dung cho giong clone)
_EVT_CANON = dict(_EN_EVT)

# ===================== BIEU CAM MO RONG (ban nang cap) =====================
# Tong giong moi: ten goc -> (rate %, pitch Hz cho edge)
_NEW_EMO = {
    "binh tinh": (-8, -2), "tu tin": (3, 2), "lo lang": (8, 6), "ngai ngung": (-10, 4),
    "that vong": (-12, -8), "lang man": (-12, 2), "kich tinh": (-15, -6),
    "hoi hop": (-18, -6), "het": (15, 18), "nghen ngao": (-15, -6), "buon ngu": (-25, -12),
    "quang cao": (12, 8), "nhe nhom": (-5, 0), "kho chiu": (5, -4), "tram ngam": (-15, -6),
    "phan dien": (-15, -20), "tre con": (8, 30), "gia": (-20, -10), "nghi ngo": (-10, -3),
    "tinh nghich": (8, 12),
}
_NEW_EMO_EN = {
    "calm": "binh tinh", "confident": "tu tin", "nervous": "lo lang", "anxious": "lo lang",
    "shy": "ngai ngung", "disappointed": "that vong", "romantic": "lang man",
    "dramatic": "kich tinh", "suspense": "hoi hop", "tense": "hoi hop", "shout": "het",
    "shouting": "het", "yell": "het", "choked": "nghen ngao", "crying voice": "nghen ngao",
    "tearful": "nghen ngao", "sleepy": "buon ngu", "announcer": "quang cao",
    "advertising": "quang cao", "commercial": "quang cao", "relieved": "nhe nhom",
    "annoyed": "kho chiu", "irritated": "kho chiu", "thoughtful": "tram ngam",
    "pensive": "tram ngam", "villain": "phan dien", "evil": "phan dien", "child": "tre con",
    "kid": "tre con", "old": "gia", "elderly": "gia", "suspicious": "nghi ngo",
    "playful": "tinh nghich", "teasing": "tinh nghich",
}
# Su kien moi: ten goc -> (chu tieng Viet, rate, pitch, nghi truoc, nghi sau)
_NEW_EVT = {
    "ha hoc": ("hớ!", 10, 15, 100, 200), "het len": ("á á á!", 15, 20, 120, 250),
    "ngap": ("oáp…", -25, -8, 200, 350), "hum": ("hừm…", -20, -6, 150, 250),
    "o": ("ồ!", 8, 12, 100, 180), "oi": ("ối!", 10, 12, 100, 180),
    "yeah": ("yeah!", 12, 15, 100, 200), "ha": ("hả?", 5, 15, 100, 200),
    "phu": ("phù…", -15, -5, 150, 300), "hu": ("hừ.", -5, -8, 100, 200),
    "ui": ("úi!", 10, 15, 100, 180), "oi chao": ("ôi…", -12, 5, 150, 250),
    "u hu": ("ừ hử.", -5, 0, 100, 180),
}
_NEW_EVT_EN = {
    "gasp": "ha hoc", "scream": "het len", "yawn": "ngap", "hmm": "hum", "oh": "o",
    "ouch": "oi", "yay": "yeah", "huh": "ha", "phew": "phu", "ugh": "hu", "oops": "ui",
    "aww": "oi chao", "mm hmm": "u hu", "uh huh": "u hu",
}
# Hieu ung am thanh (lop rieng, giu den [khong hieu ung] / [binh thuong])
FX = {"dien thoai", "radio", "vang", "hoi truong", "hang dong", "robot", "loa",
      "noi tam", "xa"}
_FX_EN = {"phone": "dien thoai", "telephone": "dien thoai", "radio": "radio",
          "echo": "vang", "hall": "hoi truong", "reverb": "hoi truong", "cave": "hang dong",
          "robot": "robot", "megaphone": "loa", "inner voice": "noi tam",
          "thought": "noi tam", "thinking": "noi tam", "distant": "xa", "far": "xa"}
FX_OFF = {"khong hieu ung", "no effect", "no fx", "clean", "het hieu ung"}
# Bo dieu chinh (cong don voi tong giong, giu den [binh thuong])
MODS = {"cham": (-18, 0), "rat cham": (-32, 0), "nhanh": (15, 0), "rat nhanh": (28, 0),
        "to": (0, 4), "nho": (0, -6)}
_MODS_EN = {"slow": "cham", "slower": "cham", "very slow": "rat cham", "fast": "nhanh",
            "faster": "nhanh", "very fast": "rat nhanh", "loud": "to", "louder": "to",
            "quiet": "nho", "softly": "nho", "quietly": "nho"}

EMOTIONS.update(_NEW_EMO)
EVENTS.update(_NEW_EVT)
for _k, _v in _NEW_EMO_EN.items():
    EMOTIONS.setdefault(_k, EMOTIONS[_v])
    _EMO_CANON.setdefault(_k, _v)
for _k, _v in _NEW_EVT_EN.items():
    EVENTS.setdefault(_k, EVENTS[_v])
    _EVT_CANON.setdefault(_k, _v)
for _k, _v in _FX_EN.items():
    FX.add(_k)
_FX_CANON = dict(_FX_EN)
for _k, _v in _MODS_EN.items():
    MODS.setdefault(_k, MODS[_v])

_TOKEN_RE = re.compile(r"(\[[^\[\]\n]{1,20}\])|(\*[^*\n]{1,80}\*)|(\.{3,}|…)")
_VOICE_TAG_RE = re.compile(r"\[giọng:([^\]]+)\]", flags=re.I)


def _norm_tag(s):
    """'Hào hứng' -> 'hao hung' (bo dau, thuong, gon cach)."""
    s = unicodedata.normalize("NFD", s.lower().strip())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.replace("đ", "d"))


def has_markup(text):
    return bool(_TOKEN_RE.search(text or ""))


def strip_tags(text):
    """Bo het the bieu cam -> van ban tron (cho giong khong ho tro)."""
    text = _VOICE_TAG_RE.sub("", text or "")
    def repl(m):
        if m.group(2):
            return m.group(2)[1:-1]        # *tu* -> tu
        if m.group(3):
            return "… "
        return " "                          # [tag] -> bo
    return re.sub(r"\s{2,}", " ", _TOKEN_RE.sub(repl, text)).strip()

def extract_speakers(text):
    return list(dict.fromkeys(_VOICE_TAG_RE.findall(text or "")))

def has_multi_voice(text):
    return len(extract_speakers(text)) >= 2


def parse_script(text):
    """-> list segment: {'text', 'rate', 'pitch', 'emo', 'emph'?, 'event'?, 'speaker'?}
    hoac {'pause': ms, 'speaker'?}. Default speaker=None."""
    segs = []
    cur_emotion, cur_name = (0, 0), "binh thuong"
    cur_speaker = None
    cur_fx = None                       # hieu ung am thanh dang bat
    cur_mod = [0, 0]                    # [rate %, gain dB] cong don

    parts = _VOICE_TAG_RE.split(text or "")
    for i, part in enumerate(parts):
        if i % 2 == 1:
            cur_speaker = part.strip()
            continue
            
        if not part:
            continue

        pos = 0
        def add_text(s, extra_rate=0, extra_pitch=0, emph=False):
            s = s.strip()
            if not s:
                return
            if not re.search(r"\w", s):
                # Doan chi co dau cau (vd '!' hoac ',' ngay sau *tu nhan*): gan vao cuoi cau truoc
                punct = re.sub(r"[^,.;:!?…]", "", s)
                if punct:
                    for prev in reversed(segs):
                        if "text" in prev and not prev.get("event"):
                            prev["text"] = prev["text"].rstrip() + punct
                            break
                return
            for chunk in split_text(s, max_chars=1200):   # doan qua dai thi cat theo cau
                seg = {"text": chunk, "emo": cur_name, "emph": emph,
                       "rate": cur_emotion[0] + extra_rate + cur_mod[0],
                       "mrate": cur_mod[0],
                       "pitch": cur_emotion[1] + extra_pitch,
                       "gain": cur_mod[1], "fx": cur_fx,
                       "speaker": cur_speaker}
                if emph:
                    seg["emph_phrases"] = [chunk.strip()]
                segs.append(seg)

        for m in _TOKEN_RE.finditer(part):
            add_text(part[pos:m.start()])
            pos = m.end()
            if m.group(1):                                 # [tag]
                tag = _norm_tag(m.group(1)[1:-1]).replace("-", " ")
                if tag in ("binh thuong", "normal", "neutral"):
                    cur_emotion, cur_name = (0, 0), "binh thuong"
                    cur_fx, cur_mod = None, [0, 0]
                elif tag in FX_OFF:
                    cur_fx = None
                elif tag in FX:
                    cur_fx = _FX_CANON.get(tag, tag)
                elif tag in MODS:
                    cur_mod = [cur_mod[0] + MODS[tag][0], cur_mod[1] + MODS[tag][1]]
                elif tag in EMOTIONS:
                    cur_emotion, cur_name = EMOTIONS[tag], tag
                elif tag in PAUSES:
                    segs.append({"pause": PAUSES[tag], "speaker": cur_speaker})
                elif tag in EVENTS:
                    t, r, p, b, a = EVENTS[tag]
                    segs.append({"pause": b, "speaker": cur_speaker})
                    segs.append({"text": t, "emo": cur_name, "event": tag,
                                 "rate": cur_emotion[0] + r + cur_mod[0],
                                 "pitch": cur_emotion[1] + p,
                                 "gain": cur_mod[1], "fx": cur_fx,
                                 "speaker": cur_speaker})
                    segs.append({"pause": a, "speaker": cur_speaker})
                # tag la -> bo qua em ai
            elif m.group(2):                               # *nhan manh*
                segs.append({"pause": 120, "speaker": cur_speaker, "emph_pause": True})
                add_text(m.group(2)[1:-1], extra_rate=-15, extra_pitch=8, emph=True)
                segs.append({"pause": 120, "speaker": cur_speaker, "emph_pause": True})
            else:                                          # ... / …
                segs.append({"pause": 450, "speaker": cur_speaker, "ellipsis": True})
        add_text(part[pos:])
    return segs


def synth_expressive(text, voice, base_rate="+0%", progress=None, target=None):
    """Tao giong bieu cam (chi giong edge). Tra ve (mp3_bytes, 'audio/mpeg').

    Co `target`: DICH TUNG DOAN truoc khi doc (giu nguyen the/cau truc bieu cam).
    Doan nao giong khong doc duoc thi bo qua; hong het -> bao loi lech ngon ngu.
    """
    segs = parse_script(text)
    speak = [(i, s) for i, s in enumerate(segs) if "text" in s]
    if not speak:
        raise ValueError("Văn bản không có gì để đọc.")

    if target:  # dich tung doan, so doan giu nguyen -> the bieu cam khong doi cho
        from . import translate
        texts = translate.translate_cues([s["text"] for _, s in speak], target)
        for (_, s), tr in zip(speak, texts):
            if tr.strip():
                s["text"] = tr
    return _render_segs(segs, voice, base_rate, progress)


def synth_human(text, voice, base_rate="+0%", progress=None):
    """Edge doc "NHU NGUOI": moi cau mot nhip hoi khac (jitter rate/pitch nhe,
    sinh tat dinh tu noi dung cau) + vi nghi ngau nhien giua cau —
    pha the doc deu deu robot cua TTS thuong."""
    sents = [x.strip() for x in re.split(r"(?<=[.!?…;,:\-])\s+", strip_tags(text))
             if x.strip()]
    if not sents:
        raise ValueError("Văn bản trống.")
    segs = []
    for i, sen in enumerate(sents):
        h = 0
        for ch in sen[:48]:
            h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        
        # English and US voices need gentler pitch/rate changes, otherwise they sound weird
        rate_jitter = (h % 5) - 2 
        pitch_jitter = ((h >> 3) % 5) - 2

        segs.append({"text": sen, "emo": "binh thuong", "emph": False,
                     "rate": rate_jitter, "pitch": pitch_jitter})
        if i < len(sents) - 1:
            segs.append({"pause": 110 + (h % 150)})
    return _render_segs(segs, voice, base_rate, progress)


def _render_segs(segs, voice, base_rate="+0%", progress=None):
    """Doc danh sach segment bang edge (song song) roi rap PCM -> mp3."""
    import edge_tts
    import imageio_ffmpeg

    name, base_pitch = _edge_parts(voice)
    base_r = int(re.sub(r"[^\d+-]", "", base_rate) or 0)
    base_p = int(re.sub(r"[^\d+-]", "", base_pitch) or 0)
    speak = [(i, s) for i, s in enumerate(segs) if "text" in s]
    if not speak:
        raise ValueError("Văn bản không có gì để đọc.")

    done = [0]
    fails = [0]
    if progress:
        progress(0, len(speak))

    async def _one(sem, idx, seg):
        async with sem:
            rate = "%+d%%" % max(-40, min(60, base_r + seg["rate"]))
            pitch = "%+dHz" % max(-40, min(40, base_p + seg["pitch"]))
            for attempt in range(3):
                try:
                    buf = b""
                    com = edge_tts.Communicate(seg["text"], name, rate=rate, pitch=pitch)
                    async for msg in com.stream():
                        if msg["type"] == "audio":
                            buf += msg["data"]
                    if not buf:
                        raise RuntimeError("khong nhan duoc audio")
                    done[0] += 1
                    if progress:
                        progress(done[0], len(speak))
                    return idx, buf
                except Exception:
                    if attempt == 2:
                        fails[0] += 1          # bo doan hong, doc tiep phan con lai
                        return idx, b""
                    await asyncio.sleep(2 * (attempt + 1))

    async def _all():
        sem = asyncio.Semaphore(TTS_CONCURRENCY)
        res = await asyncio.gather(*[_one(sem, i, s) for i, s in speak])
        return dict(res)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        clips = loop.run_until_complete(_all())
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    if fails[0] >= len(speak):
        raise ValueError(
            "Giọng này không đọc được văn bản (thường do giọng lệch ngôn ngữ — "
            "ví dụ giọng Việt đọc chữ Hán). Chọn 'Dịch sang' cho khớp giọng, "
            "hoặc đổi giọng đúng ngôn ngữ văn bản.")

    # ghep PCM: doan doc + khoang lang dung cho
    pcm = bytearray()
    for i, seg in enumerate(segs):
        if "pause" in seg:
            pcm += b"\x00" * (int(seg["pause"] / 1000.0 * SR) * 2)
        elif i in clips and clips[i]:
            pcm += _decode_pcm(clips[i])
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.mp3"
        r = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "s16le", "-ac", "1", "-ar", str(SR), "-i", "pipe:0",
             "-b:a", "128k", str(out)],
            input=bytes(pcm), capture_output=True)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg encode loi")
        return out.read_bytes(), "audio/mpeg"


# Chi dan phong cach cho Gemini TTS (dinh dang "Say cheerfully: ..." ma model hieu)
_GEMINI_STYLE = {
    "vui": "Nói một cách vui tươi: ",
    "hao hung": "Nói hào hứng, tràn đầy năng lượng: ",
    "buon": "Nói bằng giọng buồn, chậm rãi: ",
    "nghiem tuc": "Nói bằng giọng nghiêm túc, chắc chắn: ",
    "ke chuyen": "Nói bằng giọng kể chuyện ấm áp, lôi cuốn: ",
    "thi tham": "Nói thì thầm nhẹ nhàng: ",
    "gian du": "Nói giận dữ, gằn giọng: ",
    "so hai": "Nói bằng giọng sợ hãi, run rẩy: ",
    "ngac nhien": "Nói đầy ngạc nhiên, sửng sốt: ",
    "diu dang": "Nói dịu dàng, trìu mến: ",
    "nung niu": "Nói nũng nịu, đáng yêu: ",
    "met moi": "Nói uể oải, mệt mỏi: ",
    "bi an": "Nói bằng giọng bí ẩn, đầy ám chỉ: ",
    "gap gap": "Nói gấp gáp, khẩn trương: ",
    "lanh lung": "Nói lạnh lùng, dửng dưng: ",
    "tu hao": "Nói đầy tự hào, kiêu hãnh: ",
    "cham biem": "Nói với giọng châm biếm, mỉa mai: ",
}
_GEMINI_EVENT = {
    "cuoi": "Bật cười sảng khoái tự nhiên: ha ha ha!",
    "cuoi nhe": "Cười khúc khích nhẹ nhàng: hì hì.",
    "um": "Nói ngập ngừng như đang suy nghĩ: ừmm…",
    "a": "Nói ngập ngừng: àa…",
    "tho dai": "Thở dài mệt mỏi: haiz…",
    "khoc": "Nói như đang khóc nức nở: hu hu…",
    "wow": "Thốt lên đầy kinh ngạc: oa!",
    "hang giong": "Hắng giọng: khụ khụ.",
    "suyt": "Suỵt khẽ để giữ im lặng: suỵt…",
}


def synth_expressive_gemini(text, voice_name, progress=None, target=None):
    """Doc bieu cam bang giong Gemini — THE KHONG BAO GIO bi doc thanh loi:
    tool tu tach doan, moi doan goi TTS voi chi dan phong cach rieng,
    roi ghep PCM tat dinh (khoang lang dat bang code, khong nho model)."""
    from .tts import _gemini_tts_one, _pcm_to_mp3
    from .video import SR

    segs = parse_script(text)
    speak = [(i, s) for i, s in enumerate(segs) if "text" in s]
    if not speak:
        raise ValueError("Văn bản không có gì để đọc.")

    if target:  # dich tung doan, giu nguyen cau truc
        from . import translate
        texts = translate.translate_cues(
            [s["text"] for _, s in speak if not s.get("event")], target)
        it = iter(texts)
        for _, s in speak:
            if not s.get("event"):
                tr = next(it, "")
                if tr.strip():
                    s["text"] = tr

    if progress:
        progress(0, len(speak))
    clips = {}
    for k, (i, s) in enumerate(speak):        # tuan tu — free tier RPM thap
        if s.get("event"):
            prompt = _GEMINI_EVENT.get(s["event"], s["text"])
        else:
            style = _GEMINI_STYLE.get(s["emo"], "")
            if s.get("emph"):
                style = "Nói chậm rãi và nhấn mạnh từng từ: "
            prompt = style + s["text"] if style else s["text"]
        clips[i] = _gemini_tts_one(prompt, voice_name)
        if progress:
            progress(k + 1, len(speak))

    pcm = bytearray()
    for i, s in enumerate(segs):
        if "pause" in s:
            pcm += b"\x00" * (int(s["pause"] / 1000.0 * SR) * 2)
        elif i in clips and clips[i]:
            pcm += clips[i]
    return _pcm_to_mp3(bytes(pcm)), "audio/mpeg"



def vieneu_script(text):
    """Chuyen kich ban co the sang dang VieNeu hieu: GIU the goc cua no
    ([cười], [thở dài]), doi [nghỉ] thanh cham lung, luoc the con lai."""
    def repl(m):
        if m.group(2):
            return m.group(2)[1:-1]                # *tu* -> tu
        if m.group(3):
            return "… "
        tag = _norm_tag(m.group(1)[1:-1])
        if tag in ("cuoi", "cuoi nhe"):
            return " [cười] "
        if tag == "tho dai":
            return " [thở dài] "
        if tag in PAUSES:
            return " … "
        return " "                                  # emotion/khac -> bo (giong da co tinh cach)
    return re.sub(r"\s{2,}", " ", _TOKEN_RE.sub(repl, text or "")).strip()


# ---------------- AI bien kich (auto_annotate) ----------------

# ten gan dung AI hay tu che -> the chuan (da _norm_tag)
_TAG_ALIAS = {
    "hao huc": "hao hung", "hung khoi": "hao hung", "phan khich": "hao hung",
    "soi noi": "hao hung", "excited": "hao hung",
    "vui ve": "vui", "vui tuoi": "vui", "happy": "vui",
    "buon ba": "buon", "sad": "buon", "tram buon": "buon",
    "nghiem trang": "nghiem tuc", "trang trong": "nghiem tuc", "serious": "nghiem tuc",
    "tham thi": "thi tham", "nhe nhang": "diu dang", "whisper": "thi tham",
    "tram am": "ke chuyen", "narration": "ke chuyen", "tu su": "ke chuyen",
    "tu nhien": "binh thuong", "normal": "binh thuong", "neutral": "binh thuong",
    "tam dung": "nghi", "ngat": "nghi", "pause": "nghi",
    "angry": "gian du", "tuc gian": "gian du", "gian": "gian du", "cau": "gian du",
    "scared": "so hai", "so": "so hai", "hoang so": "so hai",
    "surprised": "ngac nhien", "bat ngo": "ngac nhien", "sung sot": "ngac nhien",
    "gentle": "diu dang", "triu men": "diu dang",
    "cute": "nung niu", "de thuong": "nung niu",
    "tired": "met moi", "ue oai": "met moi",
    "mysterious": "bi an", "huyen bi": "bi an",
    "urgent": "gap gap", "khan truong": "gap gap", "voi vang": "gap gap",
    "cold": "lanh lung", "dung dung": "lanh lung",
    "proud": "tu hao", "kieu hanh": "tu hao",
    "sarcastic": "cham biem", "mia mai": "cham biem",
    "cry": "khoc", "crying": "khoc", "nuc no": "khoc",
    "im lang": "nghi dai", "long pause": "nghi dai",
    "cuoi lon": "cuoi", "cuoi to": "cuoi", "laugh": "cuoi", "haha": "cuoi",
    "cuoi khe": "cuoi nhe", "cuoi mim": "cuoi nhe", "chuckle": "cuoi nhe",
    "sigh": "tho dai", "uhm": "um", "uh": "um", "er": "a", "ah": "a",
}
# dang co dau de ghi lai vao van ban
_TAG_DISPLAY = {
    "vui": "vui", "buon": "buồn", "hao hung": "hào hứng", "nghiem tuc": "nghiêm túc",
    "ke chuyen": "kể chuyện", "thi tham": "thì thầm", "binh thuong": "bình thường",
    "nghi": "nghỉ", "nghi dai": "nghỉ dài", "um": "ừm", "a": "à",
    "cuoi": "cười", "cuoi nhe": "cười nhẹ", "tho dai": "thở dài",
    "gian du": "giận dữ", "so hai": "sợ hãi", "ngac nhien": "ngạc nhiên",
    "diu dang": "dịu dàng", "nung niu": "nũng nịu", "met moi": "mệt mỏi",
    "bi an": "bí ẩn", "gap gap": "gấp gáp", "lanh lung": "lạnh lùng",
    "tu hao": "tự hào", "cham biem": "châm biếm", "nghi ngan": "nghỉ ngắn",
    "khoc": "khóc", "wow": "wow", "hang giong": "hắng giọng", "suyt": "suỵt",
    # --- moi ---
    "binh tinh": "bình tĩnh", "tu tin": "tự tin", "lo lang": "lo lắng",
    "ngai ngung": "ngại ngùng", "that vong": "thất vọng", "lang man": "lãng mạn",
    "kich tinh": "kịch tính", "hoi hop": "hồi hộp", "het": "hét", "nghen ngao": "nghẹn ngào",
    "buon ngu": "buồn ngủ", "quang cao": "quảng cáo", "nhe nhom": "nhẹ nhõm",
    "kho chiu": "khó chịu", "tram ngam": "trầm ngâm", "phan dien": "phản diện",
    "tre con": "trẻ con", "gia": "già", "nghi ngo": "nghi ngờ", "tinh nghich": "tinh nghịch",
    "ha hoc": "há hốc", "het len": "hét lên", "ngap": "ngáp", "hum": "hừm", "o": "ồ",
    "oi": "ối", "yeah": "yeah", "ha": "hả", "phu": "phù", "hu": "hừ", "ui": "úi",
    "oi chao": "ôi chao", "u hu": "ừ hử",
}
for _k, _v in list(_NEW_EMO_EN.items()) + list(_NEW_EVT_EN.items()):
    _TAG_ALIAS.setdefault(_k, _v)
_PREAMBLE_RE = re.compile(
    r"^(đây là|day la|dưới đây|duoi day|văn bản|van ban|kịch bản|kich ban|kết quả|ket qua)",
    re.I)


def _sanitize_script(s):
    """Don dep ket qua AI: bo loi dan, quy doi the la ve the chuan, bo the bia,
    can bang dau *, gop the trung."""
    s = (s or "").strip().strip('"').strip()
    lines = s.splitlines()
    while lines and (_PREAMBLE_RE.match(lines[0].strip())
                     and lines[0].strip().endswith(":")):
        lines.pop(0)
    s = "\n".join(lines).strip()

    def fix_tag(m):
        t = _norm_tag(m.group(1))
        t = _TAG_ALIAS.get(t, t)
        return "[%s]" % _TAG_DISPLAY[t] if t in _TAG_DISPLAY else ""
    s = re.sub(r"\[([^\[\]\n]{1,25})\]", fix_tag, s)

    # giu *cum tu* hop le, bo dau * mo coi
    parts = re.split(r"(\*[^*\n]{1,80}\*)", s)
    s = "".join(p if i % 2 else p.replace("*", "") for i, p in enumerate(parts))

    s = re.sub(r"(\[[^\]]+\])(\s*\1)+", r"\1", s)     # [vui] [vui] -> [vui]
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def _core_len(s):
    return len(re.sub(r"[\W_]", "", strip_tags(s), flags=re.U))


def _split_paras(text, limit=2500):
    """Chia van ban theo doan (doan qua dai cat theo cau) thanh cac khuc <= limit."""
    out, cur = [], ""
    for p in re.split(r"\n{2,}", text.strip()):
        p = p.strip()
        if not p:
            continue
        for sub in (split_text(p, max_chars=limit) if len(p) > limit else [p]):
            if len(cur) + len(sub) + 2 <= limit:
                cur = (cur + "\n\n" + sub) if cur else sub
            else:
                if cur:
                    out.append(cur)
                cur = sub
    if cur:
        out.append(cur)
    return out or [text]


_TONE_SET = set(EMOTIONS) - {"binh thuong"}


def _fast_prompt(sents, target, ctx=""):
    from ..config import TRANSLATE_LANGS
    want = (" Sau do dich cau sang %s? KHONG — chi gan the, khong dich." %
            TRANSLATE_LANGS.get(target, target)) if False else ""
    numbered = "\n".join("%d|%s" % (i + 1, x) for i, x in enumerate(sents))
    return (
        "Bạn là đạo diễn lồng tiếng. " + (ctx + "\n" if ctx else "") + "Dưới đây là các câu đánh số. "
        "Với câu cần biểu cảm, trả về MỘT dòng dạng: số|các thẻ cách nhau bằng khoảng trắng|từ cần nhấn (bỏ trống nếu không).\n"
        "Thẻ tông giọng (đặt trước câu): vui, buồn, hào hứng, nghiêm túc, kể chuyện, thì thầm, "
        "dịu dàng, giận dữ, sợ hãi, ngạc nhiên, nũng nịu, mệt mỏi, bí ẩn, gấp gáp, lạnh lùng, tự hào, châm biếm, "
        "bình tĩnh, tự tin, lo lắng, ngại ngùng, thất vọng, lãng mạn, kịch tính, hồi hộp, hét, nghẹn ngào, "
        "buồn ngủ, quảng cáo, nhẹ nhõm, khó chịu, trầm ngâm, phản diện, trẻ con, già, nghi ngờ, tinh nghịch.\n"
        "Thẻ ngắt (tuỳ chọn, thêm sau thẻ tông): nghỉ, nghỉ dài.\n"
        "LUẬT: đổi tông theo MẠCH CẢM XÚC của bài — khoảng mỗi 1-2 câu một thẻ tông, KHÔNG để quá "
        "3 câu liền nhau cùng một tông (nghe đều đều như máy). Tối đa 1 thẻ tông mỗi câu. "
        "Từ nhấn: cứ 2-3 câu chọn 1 cụm 1-3 từ quan trọng nhất (tên sản phẩm, con số, lợi ích chính, "
        "chi tiết then chốt) — chép ĐÚNG nguyên văn cụm đó trong câu. "
        "Thẻ [nghỉ]: thêm sau câu mở đầu gây tò mò và ngay trước câu chốt / tiết lộ; KHÔNG đặt ở câu cuối. "
        "[ngạc nhiên] CHỈ cho câu hỏi gây tò mò hoặc chi tiết thật sự bất ngờ (con số ấn tượng, điều không ngờ); "
        "câu kể / gợi hình dung ('Imagine…', 'Hãy tưởng tượng…') dùng [dịu dàng] hoặc [kể chuyện]. "
        "KHÔNG viết lại câu, không giải thích.\n"
        "Ví dụ dòng trả về: 3|bí ẩn nghỉ|chiếc ghe ma   hoặc   5|hào hứng|30%\n\nCÂU:\n" + numbered)


def _parse_tag_field(field):
    """'hào hứng nghỉ dài' -> ['hao hung', 'nghi dai']: ghep tham lam toi da 3 tu
    (the nhieu chu nhu 'hao hung', 'ke chuyen' khong bi cat lam doi)."""
    words = [w for w in re.split(r"[,\s\[\]]+", field or "") if w]
    tags, i = [], 0
    while i < len(words):
        for n in (3, 2, 1):
            t = _norm_tag(" ".join(words[i:i + n]))
            t = _TAG_ALIAS.get(t, t)
            if i + n <= len(words) and t in _TAG_DISPLAY:
                tags.append(t)
                i += n
                break
        else:
            i += 1
    return tags


def _annotate_chunk_fast(chunk, call, ctx=""):
    """AI chi tra BANG GAN THE (it token ~2x nhanh hon), may rap vao van ban goc
    -> noi dung nguoi dung duoc giu nguyen 100%% may moc (ca xuong dong).
    Goi theo LO 12 cau — prompt ngan, model nho (Ollama) cung theo kip.
    call(prompt, temperature) -> chuoi tra loi cua AI."""
    # tach theo dong de rap lai dung cau truc doan/xuong dong cua nguoi dung
    pieces = re.split(r"(\n+)", chunk)
    sents, where = [], []          # where[i] = (chi so piece, thu tu cau trong piece)
    for pi, piece in enumerate(pieces):
        if pi % 2:
            continue
        for x in re.split(r"(?<=[\.\!\?…])\s+", piece):
            if x.strip():
                where.append(pi)
                sents.append(x.strip())
    if not sents:
        return chunk
    plan = {}
    LO = 12
    for base in range(0, len(sents), LO):
        batch = sents[base:base + LO]
        raw = call(_fast_prompt(batch, None, ctx), 0.5)
        for ln in raw.splitlines():
            m = re.match(r"^\s*(\d+)\s*\|([^|]*)(?:\|(.*))?$", ln.strip())
            if not m:
                continue
            n = int(m.group(1))
            if not (1 <= n <= len(batch)):
                continue
            tags = _parse_tag_field(m.group(2))
            emph = (m.group(3) or "").strip()
            if tags or emph:
                plan[base + n] = (tags[:2], emph)
    if not plan and len(sents) >= 8:              # AI khong tra ve gi -> coi nhu fail
        raise ValueError("AI gan the qua it")
    out = {}
    cur_tone = None
    for i, sen in enumerate(sents):
        tags, emph = plan.get(i + 1, ([], ""))
        tones = [t for t in tags if t in _TONE_SET][:1]
        if tones and tones[0] == cur_tone:        # tong khong doi -> khong gan lai
            tones = []
        elif tones:
            cur_tone = tones[0]
        # chi giu the NGAT; the am thanh ([wow] [cuoi] [ha]...) khong doc duoc tu nhien
        events = [t for t in tags if t not in _TONE_SET and t in PAUSES][:1]
        if emph and emph in sen and "*" not in sen:
            sen = sen.replace(emph, "*%s*" % emph, 1)
        piece = " ".join("[%s]" % _TAG_DISPLAY[t] for t in tones)
        piece = (piece + " " if piece else "") + sen
        if events:
            piece += " " + " ".join("[%s]" % _TAG_DISPLAY[t] for t in events)
        out.setdefault(where[i], []).append(piece)
    return "".join(p if pi % 2 else " ".join(out.get(pi, [p.strip()] if p.strip() else []))
                   for pi, p in enumerate(pieces))


def _annotate_prompt(chunk, target, strict, ctx=""):
    from ..config import TRANSLATE_LANGS
    want = ("Dịch sang %s rồi " % TRANSLATE_LANGS.get(target, target)) if target else ""
    extra = ("\nLẦN TRƯỚC BẠN LÀM SAI: đã thay đổi nội dung. "
             "Lần này tuyệt đối giữ nguyên từng chữ, chỉ chèn thẻ.") if strict else ""
    return (
        "Bạn là đạo diễn lồng tiếng chuyên nghiệp. " + (ctx + "\n" if ctx else "") +
        "%schèn thẻ biểu cảm vào văn bản.\n"
        "CHỈ được dùng đúng các thẻ sau, KHÔNG tự chế thẻ khác:\n"
        "- [vui] [buồn] [hào hứng] [nghiêm túc] [kể chuyện] [thì thầm] [dịu dàng] "
        "[giận dữ] [sợ hãi] [ngạc nhiên] [nũng nịu] [mệt mỏi] [bí ẩn] [gấp gáp] "
        "[lạnh lùng] [tự hào] [châm biếm] [bình tĩnh] [tự tin] [lo lắng] [ngại ngùng] "
        "[thất vọng] [lãng mạn] [kịch tính] [hồi hộp] [hét] [nghẹn ngào] [buồn ngủ] "
        "[quảng cáo] [nhẹ nhõm] [khó chịu] [trầm ngâm] [phản diện] [trẻ con] [già] "
        "[nghi ngờ] [tinh nghịch] [bình thường]: đặt TRƯỚC câu cần đổi tông\n"
        "- *từ nhấn* (1-3 từ quan trọng)\n"
        "- [nghỉ] [nghỉ dài]: khoảng lặng\n"
        "LUẬT: giữ nguyên 100%% từ ngữ%s, chỉ CHÈN thẻ. Chỉ đặt thẻ tông ở ĐẦU câu khi tông "
        "giọng THAY ĐỔI (khoảng 1 trong 4-5 câu), không bao giờ đặt giữa câu. *nhấn* rất hiếm. "
        "KHÔNG dùng thẻ âm thanh như [cười] [wow] [thở dài]. "
        "Trả về DUY NHẤT văn bản đã gắn thẻ — không lời dẫn, không markdown.%s\n"
        "Ví dụ: 'Hôm nay trời đẹp quá. Để mình kể chuyện này nhé.' ->\n"
        "'[vui] Hôm nay trời *đẹp quá*. [kể chuyện] Để mình kể chuyện này nhé.'\n\n"
        "VĂN BẢN:\n%s" % (want, " (sau khi dịch)" if target else "", extra, chunk))


def _heuristic_annotate(text):
    """Bien kich nhanh KHONG can AI: the co ban tu dau cau/tu cuoi."""
    s = re.sub(r"\b(ha\s?ha(?:\s?ha)*|hihi+|hehe+|kk+k*)\b[.!]*", "[cười]", text, flags=re.I)
    s = re.sub(r":\)+|:D+|=\)+", " [cười nhẹ] ", s)
    s = re.sub(r"\.{3,}|…", " [nghỉ] ", s)
    lines = []
    for i, para in enumerate(s.split("\n")):
        p = para.strip()
        if p and not p.startswith("["):
            if "!" in p:
                p = ("[hào hứng] " if p.count("!") >= 2 else "[vui] ") + p
            elif i == 0:
                p = "[bình thường] " + p
        lines.append(p)
    return re.sub(r"[ \t]{2,}", " ", "\n".join(lines)).strip()


def _fallback_annotate(text, target, note):
    from . import translate
    plain = strip_tags(text)
    if target:
        try:
            plain = translate.translate_text(plain, target)
        except Exception:
            note += " Mạng lỗi nên chưa dịch được — văn bản giữ ngôn ngữ gốc."
    return _safe_heuristic(plain), note


def _safe_heuristic(text):
    """Ban bien kich nhanh — tang CUOI, khong bao gio duoc loi: loi thi tra nguyen van."""
    try:
        return _heuristic_annotate(text)
    except Exception:
        return text


# ---- bao loi AI bien kich: ghi log cho ky thuat + giu loi gan nhat cho /admin ----
_AI_LOG = Path(__file__).resolve().parents[2] / "data" / "logs" / "ai_bien_kich.log"
AI_LAST = {"ok_at": None, "error_at": None, "error": None, "kind": None, "model": None}

# cach khac phuc hien cho NGUOI DUNG theo tung loai loi
AI_FIX = {
    "down": "AI local (Ollama) không chạy — mở lại cửa sổ \"AI local\" (hoặc chạy lại "
            "Start-Windows.bat) rồi bấm lại ✨ AI biên kịch.",
    "no_model": "AI local chưa tải xong model — chờ cửa sổ \"AI local\" tải xong (cần mạng, "
                "1 lần) hoặc chạy setup_ai.py.",
    "memory": "Máy không đủ RAM/VRAM để chạy model AI — đóng bớt ứng dụng nặng rồi thử lại.",
    "timeout": "AI trả lời quá lâu (máy yếu hoặc đang bận tạo giọng) — thử lại khi máy rảnh.",
    "other": "AI trả kết quả không dùng được ở vài phần — bấm lại để AI thử lần nữa.",
}


def _ai_log(kind, msg, part=None, model=None):
    import time as _t
    AI_LAST.update(error_at=_t.time(), error=msg, kind=kind, model=model)
    try:
        _AI_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_AI_LOG, "a", encoding="utf-8") as f:
            f.write("%s  [%s] phan=%s model=%s  %s\n" % (
                _t.strftime("%Y-%m-%d %H:%M:%S"), kind, part if part is not None else "-",
                model or "-", msg))
    except Exception:
        pass


def ai_status():
    """Trang thai AI bien kich cho /admin."""
    from . import translate
    try:
        info = translate.ollama_info()
    except Exception:
        info = {"online": False, "model": None, "installed": [], "skipped": []}
    info.update(last_ok=AI_LAST["ok_at"], last_error=AI_LAST["error"],
                last_error_at=AI_LAST["error_at"], last_error_kind=AI_LAST["kind"],
                fix=AI_FIX.get(AI_LAST["kind"]) if AI_LAST["error"] else None,
                log_file=str(_AI_LOG))
    return info


def _make_ai_call(translate, rep):
    """Ham goi AI cho bien kich: thieu RAM -> tu ha xuong model NHO HON va thu lai."""
    def call(prompt, temperature):
        while True:
            try:
                return translate.ollama_call(prompt, temperature, timeout=180)
            except translate.AIError as e:
                if e.kind == "memory" and e.model:
                    smaller = translate.ollama_fallback_model(e.model)
                    if smaller:
                        _ai_log("memory", "thiếu RAM cho %s -> tự chuyển sang %s"
                                % (e.model, smaller), model=e.model)
                        rep["switched"] = "%s -> %s" % (e.model, smaller)
                        continue
                raise
    return call


def _annotate_one(chunk, target, call, ctx=""):
    """Bien kich 1 phan bang AI. Loi -> nem (AIError / ValueError)."""
    if not target:                       # duong TOC HANH: chi gan the, khong viet lai
        try:
            return _sanitize_script(_annotate_chunk_fast(chunk, call, ctx))
        except ValueError:
            pass                         # AI tra kem -> thu duong day du ben duoi
    got = _sanitize_script(call(_annotate_prompt(chunk, target, False, ctx), 0.6))
    # AI lam mat/che them noi dung? (chi so duoc khi khong dich)
    if not target:
        ratio = _core_len(got) / max(1, _core_len(chunk))
        if ratio < 0.6 or ratio > 1.7:
            got = _sanitize_script(call(_annotate_prompt(chunk, target, True, ctx), 0.3))
            ratio = _core_len(got) / max(1, _core_len(chunk))
            if ratio < 0.6 or ratio > 1.7:
                raise ValueError("AI đổi nội dung sau 2 lần thử")
    return got


_GENRE_KW = {             # du phong khi khong co AI: doan the loai theo tu khoa
    "quang_cao": r"giảm giá|ưu đãi|mua ngay|khuyến mãi|đặt hàng|freeship|chỉ còn|sale|inbox|hotline"
                 r"|order (now|today)|buy now|shop now|discount|% off|limited (time|offer)|free shipping"
                 r"|official website|early bird|gift set|upgrade your|get yours",
    "tin_tuc": r"theo (thông tin|báo cáo)|cho biết|ghi nhận|bộ trưởng|chính phủ|tỉnh|thống kê",
    "kinh_di": r"ma |bóng tối|rợn|kinh hoàng|tiếng động lạ|nửa đêm|biến mất|xác "
               r"|haunted|ghost|midnight|creepy|whisper(ed)? my name|vanished|blood",
    "review": r"đánh giá|trải nghiệm|ưu điểm|nhược điểm|so sánh|giá bán|thiết kế|pin |màn hình"
              r"|pros and cons|downside|verdict|worth it|compared to|battery life|in my testing",
    "tam_su": r"mình đã từng|tâm sự|cảm thấy|cô đơn|thương|nhớ|chia sẻ với các bạn",
    "ke_chuyen": r"ngày xửa|ngày ấy|một hôm|câu chuyện|kể rằng|năm ấy|chàng|nàng",
}


def detect_genre_kw(text):
    low = (text or "").lower()
    best, hits = None, 0
    for g, pat in _GENRE_KW.items():
        n = len(re.findall(pat, low))
        if n > hits:
            best, hits = g, n
    return best if hits >= 2 else None


def _macro_analyze(text, call):
    """BUOC 1 (doc toan bai): the loai video + tong giong chu dao. Loi -> (None, '')."""
    import json as _json
    keys = "; ".join("%s = %s" % (k, GENRE_ARC[k][0]) for k in GENRES)
    head = text[:3000]
    prompt = ("Đọc kịch bản lồng tiếng dưới đây và xác định:\n"
              "1) the_loai: CHỈ MỘT trong các mã: %s\n"
              "2) tong: 1 câu ngắn mô tả tông giọng chủ đạo và nhịp điệu nên đọc.\n"
              "Trả về DUY NHẤT JSON dạng {\"the_loai\": \"...\", \"tong\": \"...\"}.\n\n"
              "KỊCH BẢN:\n%s" % (keys, head))
    raw = call(prompt, 0.2)
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None, ""
    try:
        d = _json.loads(m.group(0))
    except Exception:
        return None, ""
    g = str(d.get("the_loai") or "").strip().lower()
    g = g if g in GENRES else None
    # AI nho hay nham quang cao <-> review: co keu goi mua / giam gia ro rang -> quang cao
    if g in (None, "review") and detect_genre_kw(text) == "quang_cao":
        g = "quang_cao"
    return g, str(d.get("tong") or "").strip()[:200]


def _genre_ctx(genre, tone):
    if not genre:
        return ""
    g = GENRES[genre]
    ex = GENRE_EXAMPLE.get(genre)
    return ("BỐI CẢNH TOÀN BÀI: thể loại %s — %s.%s\nMẠCH CẢM XÚC NÊN THEO: %s.\n"
            "Gắn thẻ đúng tinh thần thể loại, giữ mạch cảm xúc liền giữa các đoạn.%s" % (
                g["label"], g["goi_y"], (" Tông chủ đạo: %s." % tone) if tone else "",
                GENRE_ARC.get(genre, ("", ""))[1],
                ("\nVÍ DỤ GẮN ĐÚNG (chỉ để học cách gắn, KHÔNG gắn cho các câu ví dụ này):\n"
                 "CÂU:\n%s\nTRẢ VỀ:\n%s" % ex) if ex else ""))


def _report_note(rep):
    """Ghi chu cho nguoi dung: phan nao dung ban nhanh, vi sao, cach khac phuc."""
    if not rep["fallback"]:
        return ("Đã tự chuyển model AI (%s) vì máy thiếu RAM." % rep["switched"]
                if rep.get("switched") else "")
    n, bad = rep["total"], rep["fallback"]
    if len(bad) == n:
        head = "AI biên kịch không chạy được nên cả bài dùng bản biên kịch nhanh (gắn thẻ cơ bản)."
    else:
        head = ("AI biên kịch xong %d/%d phần; phần %s dùng bản biên kịch nhanh."
                % (n - len(bad), n, ", ".join(str(i + 1) for i in bad)))
    fixes = []
    for k in ("down", "no_model", "memory", "timeout", "other"):
        if k in rep["kinds"]:
            fixes.append(AI_FIX[k])
    if rep.get("switched"):
        fixes.append("Đã tự chuyển model AI (%s) vì máy thiếu RAM." % rep["switched"])
    return head + " " + " ".join(fixes)


def auto_annotate(text, target=None, progress=None, report=None, genre=None):
    """AI 'bien kich': (dich neu can roi) gan the bieu cam tu nhien.

    CHONG SAP nhieu tang:
      * moi PHAN chay rieng: loi -> thu lai 1 lan -> van loi thi CHI phan do dung
        ban bien kich nhanh, cac phan sau van chay AI;
      * Ollama tat / chua co model -> khong cho doi vo ich, phan con lai dung ban nhanh;
      * thieu RAM -> tu ha xuong model nho hon da cai;
      * moi loi ghi data/logs/ai_bien_kich.log + hien tren /admin;
      * tang cuoi: loi khong luong truoc -> tra nguyen van ban, KHONG bao gio nem loi.
    -> (van_ban_gan_the, ghi_chu)  — ghi_chu rong = AI chay ngon.
    report (dict, tuy chon) duoc dien: total, ai, fallback, kinds, switched, genre, tone.
    genre: nguoi dung da chon the loai -> bo qua buoc doan the loai.
    """
    rep = report if report is not None else {}
    rep.update(total=0, ai=0, fallback=[], kinds=[], switched=None,
               genre=genre if genre in GENRES else None, tone="")
    try:
        return _auto_annotate(text, target, progress, rep)
    except Exception as e:                       # loi khong luong truoc: khong lam mat kich ban
        _ai_log("other", "loi khong luong truoc: %s" % e)
        rep["kinds"].append("other")
        try:
            out, _n = _fallback_annotate(text, target, "")
        except Exception:
            out = text
        return out, ("AI biên kịch gặp lỗi lạ nên dùng bản biên kịch nhanh. "
                     "Gửi file data/logs/ai_bien_kich.log cho kỹ thuật nếu lặp lại.")


def _strip_emotion_tags(text):
    """Bo the CAM XUC cu ([vui], [nghi], *nhan*) de AI gan lai theo the loai moi;
    GIU the phan vai [giong:...] va giu nguyen xuong dong / doan."""
    keep = []

    def hold(m):
        keep.append(m.group(0))
        return "\x00%d\x00" % (len(keep) - 1)
    t = _VOICE_TAG_RE.sub(hold, text or "")
    t = re.sub(r"\*([^*\n]{1,80})\*", r"\1", t)
    t = re.sub(r"\[[^\[\]\n]{1,25}\]", " ", t)
    t = re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return "\n".join(ln.strip() for ln in t.split("\n")).strip()


def _auto_annotate(text, target, progress, rep):
    import time as _t
    from . import translate
    if has_markup(text):                 # bam AI lan nua / doi the loai -> gan lai tu dau, khong chong the
        text = _strip_emotion_tags(text)
    chunks = _split_paras(text, limit=4000)
    rep["total"] = len(chunks)
    if progress:
        progress(0, len(chunks))
    call = _make_ai_call(translate, rep)
    out = []
    ai_dead = None                     # loai loi khien AI chet han (down / no_model)
    if not translate.has_llm():
        ai_dead = "no_model" if translate.ollama_info().get("online") else "down"
        _ai_log(ai_dead, "AI local chưa sẵn sàng khi bấm biên kịch")
    # BUOC 1: doc toan bai -> the loai + tong chu dao (lam boi canh cho buoc 2)
    if not rep.get("genre") and not ai_dead:
        try:
            rep["genre"], rep["tone"] = _macro_analyze(text, call)
        except translate.AIError as e:
            _ai_log(e.kind, "doc toan bai: %s" % e, model=e.model)
            if e.kind in ("down", "no_model"):
                ai_dead = e.kind
        except Exception as e:
            _ai_log("other", "doc toan bai: %s" % e)
    if not rep.get("genre"):
        rep["genre"] = detect_genre_kw(text)
    ctx = _genre_ctx(rep.get("genre"), rep.get("tone"))
    # BUOC 2: gan the tung phan, kem boi canh toan bai
    for ci, chunk in enumerate(chunks):
        got, last_err = None, None
        if not ai_dead:
            for attempt in range(2):           # moi phan: thu 2 lan
                try:
                    got = _annotate_one(chunk, target, call, ctx)
                    break
                except translate.AIError as e:
                    last_err = e
                    _ai_log(e.kind, str(e), part=ci + 1, model=e.model)
                    if e.kind in ("down", "no_model"):
                        ai_dead = e.kind       # AI chet han: khong cho doi o cac phan sau
                        break
                    if e.kind in ("memory", "timeout"):
                        break                  # thu lai cung khong khac
                except Exception as e:         # AI tra sai dinh dang / doi noi dung
                    last_err = e
                    _ai_log("other", str(e), part=ci + 1)
        if got is None:
            kind = ai_dead or getattr(last_err, "kind", None) or "other"
            if kind not in rep["kinds"]:
                rep["kinds"].append(kind)
            rep["fallback"].append(ci)
            got, _n = _fallback_annotate(chunk, target, "")
        else:
            rep["ai"] += 1
            AI_LAST["ok_at"] = _t.time()
        out.append(got)
        if progress:
            progress(ci + 1, len(chunks))
    res = re.sub(r"\s*\[(nghỉ|nghỉ dài)\]\s*$", "", "\n\n".join(out).strip())   # [nghi] cuoi bai: vo ich
    return res, _report_note(rep)



# =====================================================================
# GIONG NHAN BAN (F5-TTS) DOC CO BIEU CAM
# ---------------------------------------------------------------------
# F5 khong co "nut" cam xuc nhu edge, nen bieu cam duoc tao bang:
#   * toc do rieng tung doan (vui/hao hung nhanh hon, buon/thi tham cham hon)
#   * dau cau (vui -> "!", buon -> "...") de F5 tu len/xuong giong
#   * nang/ha cao do sau khi sinh (+-1..2 cung) + am luong (thi tham nho lai)
#   * *nhan manh*: tach rieng, doc cham ~15%, cao giong, nghi nhe 2 ben
#   * [nghi] / "..." chen khoang lang that; [cuoi] [um] [tho dai]... doc thanh
#     am thanh tuong ung (tieng Anh hoac tieng Viet tuy van ban)
# Van ban thuong (khong the) cung di qua day: cat theo CAU (<=200 ky tu, khong
# bao gio cat giua tu) + chen khoang nghi tu nhien giua cac cau -> het nuot chu
# o cho noi cac doan.
# =====================================================================

# cao do (cung) + am luong (dB) theo cam xuc
_CLONE_EMO_FX = {
    # (doi cao do - CUNG, am luong - dB). Cam xuc thuong KHONG doi cao do de giu
    # dung giong nhan vat; bieu cam den tu toc do + dau cau + am luong + khoang nghi.
    "binh thuong": (0, 0), "ke chuyen": (0, 0), "vui": (0, 1.5), "hao hung": (0, 2.5),
    "ngac nhien": (0, 1.5), "so hai": (0, -1), "nung niu": (0, -2), "tu hao": (0, 1),
    "cham biem": (0, 0), "gap gap": (0, 1), "diu dang": (0, -3), "buon": (0, -3),
    "met moi": (0, -4), "nghiem tuc": (0, 0), "lanh lung": (0, -1),
    "bi an": (0, -3), "gian du": (0, 3), "thi tham": (0, -10),
    "binh tinh": (0, -1), "tu tin": (0, 1.5), "lo lang": (0, -1),
    "ngai ngung": (0, -4), "that vong": (0, -2), "lang man": (0, -2),
    "kich tinh": (0, 1), "hoi hop": (0, -4), "het": (0, 4),
    "nghen ngao": (0, -2), "buon ngu": (0, -4), "quang cao": (0, 2),
    "nhe nhom": (0, -1), "kho chiu": (0, 1), "tram ngam": (0, -2),
    "nghi ngo": (0, -1), "tinh nghich": (0, 0.5),
    # the DOI GIONG (co y bien thanh nhan vat khac): van doi cao do
    "phan dien": (-3.0, 1), "tre con": (4.0, 0), "gia": (-2.0, -1),
}
# cao do lech nhe theo cam xuc (cung, nhan theo Do cam xuc) — nguoi vui/hao hung noi cao
# giong hon, buon/tram thap hon. Nho (<= ~1 cung) de van dung giong nhan vat.
_EMO_PITCH = {"hao hung": 0.8, "vui": 0.6, "ngac nhien": 1.0, "het": 1.0, "gian du": 0.3,
              "quang cao": 0.5, "tu tin": 0.3, "kich tinh": 0.3, "tinh nghich": 0.6, "tu hao": 0.4,
              "gap gap": 0.4, "so hai": 0.5, "lo lang": 0.3,
              "buon": -0.7, "met moi": -0.8, "thi tham": -0.3, "diu dang": -0.4, "tram ngam": -0.6,
              "bi an": -0.5, "nghen ngao": -0.5, "buon ngu": -0.8, "that vong": -0.6, "lanh lung": -0.4,
              "binh tinh": -0.3, "lang man": -0.3, "hoi hop": -0.2}
# loc am thanh rieng cho mot so tong giong (run giong, run run...)
_CLONE_EMO_FILTER = {
    "lo lang": "tremolo=f=7:d=0.15",
    "nghen ngao": "vibrato=f=6:d=0.12",
    "gia": "vibrato=f=5:d=0.15",
    "so hai": "tremolo=f=8:d=0.12",
}
# hieu ung am thanh (lop [phone] [echo] [robot]...)
_CLONE_FX_FILTER = {
    "dien thoai": "highpass=f=300,lowpass=f=3400,acompressor=threshold=-18dB:ratio=4:attack=5:release=50",
    "radio": "highpass=f=400,lowpass=f=4500,acompressor=threshold=-20dB:ratio=6:attack=5:release=80,volume=2dB",
    "vang": "aecho=0.8:0.6:140|280:0.35|0.18",
    "hoi truong": "aecho=0.8:0.85:40|75|115|170:0.32|0.25|0.18|0.12",
    "hang dong": "aecho=0.8:0.9:450|850:0.35|0.2",
    "robot": "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75",
    "loa": "highpass=f=600,lowpass=f=3000,volume=6dB,acompressor=threshold=-12dB:ratio=8",
    "noi tam": "lowpass=f=6000,aecho=0.8:0.5:60:0.25,volume=-3dB",
    "xa": "lowpass=f=2500,aecho=0.8:0.5:90:0.3,volume=-9dB",
}
_CLONE_EVT_EN = {"cuoi": "Haha!", "cuoi nhe": "Hehe.", "um": "Umm...", "a": "Uhh...",
                 "tho dai": "Haah...", "khoc": "Sob...", "wow": "Wow!",
                 "hang giong": "Ahem.", "suyt": "Shh...",
                 "ha hoc": "Hah!", "het len": "Aaaah!", "ngap": "Haaaah...", "hum": "Hmm...",
                 "o": "Oh!", "oi": "Ouch!", "yeah": "Yay!", "ha": "Huh?", "phu": "Phew...",
                 "hu": "Ugh.", "ui": "Oops!", "oi chao": "Aww...", "u hu": "Mm-hmm."}
_HAPPY = {"vui", "hao hung", "ngac nhien", "tu hao", "gap gap", "het", "quang cao",
          "tinh nghich"}
_SAD = {"buon", "met moi", "thi tham", "bi an", "that vong", "nghen ngao", "buon ngu",
        "hoi hop", "tram ngam"}
_SLOW_GAP = {"kich tinh": 1.6, "hoi hop": 1.5, "tram ngam": 1.45, "nghen ngao": 1.45,
             "buon": 1.35, "buon ngu": 1.3, "thi tham": 1.3, "ke chuyen": 1.25,
             "diu dang": 1.2, "lang man": 1.25, "that vong": 1.3}
_CLONE_MAX_CHARS = 200
_CLONE_MAX_CHARS_VI = 165
# cam xuc -> doc bang mau "tong giong" rieng cua giong (neu giong co mau do)
_STYLE_OF = {e: "hype" for e in ("vui", "hao hung", "ngac nhien", "tu hao", "gap gap", "het",
                                 "quang cao", "tinh nghich", "tu tin", "kich tinh", "gian du")}
_STYLE_OF.update({e: "calm" for e in ("buon", "met moi", "diu dang", "thi tham", "bi an", "nung niu",
                                      "binh tinh", "tram ngam", "nghen ngao", "buon ngu", "that vong",
                                      "lang man", "hoi hop", "ngai ngung", "lo lang", "so hai")})
# MAU CAM XUC uu tien cho tung the (co mau rieng thi dung, khong co thi lui ve hype/calm):
# cam xuc THAT den tu mau giong do doc dung cam xuc, khong phai chinh am sau khi doc
_STYLE_PREF = {}
for _e in ("buon", "that vong", "nghen ngao", "met moi", "buon ngu", "tram ngam"):
    _STYLE_PREF[_e] = ("sad", "calm")
for _e in ("gian du", "kho chiu", "het", "phan dien"):
    _STYLE_PREF[_e] = ("angry", "hype")
for _e in ("thi tham", "bi an", "hoi hop", "so hai", "lo lang"):
    _STYLE_PREF[_e] = ("soft", "calm")
for _e in ("ngac nhien",):
    _STYLE_PREF[_e] = ("surprise", "hype")
for _e in ("nghiem tuc", "lanh lung", "nghi ngo"):
    _STYLE_PREF[_e] = ("serious", "")
for _e in ("diu dang", "nung niu", "lang man", "binh tinh", "nhe nhom"):
    _STYLE_PREF[_e] = ("warm", "calm")


def _style_for(emo, have):
    """Mau cam xuc dung cho the `emo` (theo thu tu uu tien, chi lay mau giong do CO)."""
    for st in _STYLE_PREF.get(emo, (_STYLE_OF.get(emo, ""),)):
        if st and st in have:
            return st
    return ""


# am luong: chi nhich nhe (tranh cam giac "tut/vot tieng" nhu loi), tru thi tham / het
_GAIN_CAP = (-1.0, 1.0)
_GAIN_SPECIAL = {"thi tham": -2.5, "het": 2.5}
_CBX_EX = {"hype": 0.85, "calm": 0.4, "": 0.65}   # muc cam xuc Chatterbox theo nhom the
# Chatterbox RIENG TUNG CAM XUC: (exaggeration = do bieu cam, cfg = do bam nhip/chu).
# Bieu cam cao lam giong noi nhanh + gat -> ha cfg (~0.3) de giu nhip; giong tram/nhe
# thi bieu cam thap + cfg cao hon cho dieu dang, dut khoat. Moc chuan: thuong (0.65, 0.3)
# — do tren Ray: dung 100% chu. Giong khong co mau hype/calm (36/37 giong Anh) chi
# con cach nay de [vui] khac [gian du], [thi tham] khac [buon].
_CBX_EMO = {
    # --- soi noi / manh ---
    "het": (1.0, 0.3), "gian du": (0.95, 0.3), "hao hung": (0.9, 0.3),
    "kich tinh": (0.85, 0.3), "gap gap": (0.85, 0.3), "ngac nhien": (0.82, 0.32),
    "quang cao": (0.8, 0.32), "vui": (0.78, 0.33), "tinh nghich": (0.75, 0.33),
    "tu tin": (0.72, 0.35), "tu hao": (0.72, 0.35), "kho chiu": (0.7, 0.35),
    "so hai": (0.7, 0.35), "phan dien": (0.7, 0.35),
    # --- trung tinh ---
    "binh thuong": (0.65, 0.3), "ke chuyen": (0.58, 0.35), "nghiem tuc": (0.55, 0.4),
    "cham biem": (0.6, 0.38), "lanh lung": (0.45, 0.45), "nghi ngo": (0.55, 0.4),
    "lo lang": (0.62, 0.38), "hoi hop": (0.6, 0.4), "nghen ngao": (0.6, 0.4),
    "ngai ngung": (0.48, 0.42), "nhe nhom": (0.5, 0.42), "lang man": (0.5, 0.45),
    # --- tram / nhe ---
    "that vong": (0.45, 0.45), "bi an": (0.45, 0.45), "binh tinh": (0.4, 0.45),
    "buon": (0.4, 0.5), "tram ngam": (0.4, 0.5), "nung niu": (0.38, 0.5),
    "diu dang": (0.35, 0.5), "met moi": (0.3, 0.5), "thi tham": (0.3, 0.5),
    "buon ngu": (0.25, 0.5),
}


# GIONG DIEU THEO THE LOAI VIDEO: tong nen (cau khong gan the doc theo tong nay),
# toc do, do ngat nghi giua cau, do bieu cam them cho Chatterbox.
# "goi_y" dua cho AI bien kich biet the loai can gan the ra sao.
GENRES = {
    "quang_cao": {"pitch": 0.4, "gain": 1.0, "k": 1.15, "label": "Quảng cáo", "base": "quang cao", "speed": 1.06, "gap": 0.8,
                  "ex": 0.08, "goi_y": "năng lượng cao, chốt ý dứt khoát, nhấn tên sản phẩm "
                  "và lợi ích, câu kêu gọi cuối bài [hào hứng]"},
    "ke_chuyen": {"pitch": 0.0, "gain": 0.0, "k": 1.2, "label": "Kể chuyện / Audiobook", "base": "ke chuyen", "speed": 0.95, "gap": 1.2,
                  "ex": 0.0, "goi_y": "kể chậm rãi cuốn hút, đổi tông theo diễn biến, cao trào "
                  "[kịch tính], đoạn buồn [buồn], lời thoại nhân vật theo cảm xúc nhân vật"},
    "tin_tuc": {"pitch": 0.0, "gain": 0.0, "k": 0.7, "label": "Tin tức / Bình luận", "base": "nghiem tuc", "speed": 1.0, "gap": 1.0,
                "ex": -0.1, "goi_y": "trung lập, rõ ràng, ít cảm xúc; chỉ đổi tông khi tin "
                "rất vui hoặc rất buồn"},
    "tam_su": {"pitch": -0.3, "gain": -1.5, "k": 0.95, "label": "Tâm sự / Podcast", "base": "diu dang", "speed": 0.92, "gap": 1.3,
               "ex": -0.05, "goi_y": "gần gũi như đang nói chuyện, ấm áp, nhiều chỗ [nghỉ] "
               "để suy ngẫm, [trầm ngâm] ở đoạn sâu lắng"},
    "review": {"pitch": 0.1, "gain": 0.5, "k": 1.0, "label": "Review / Giới thiệu sản phẩm", "base": "tu tin", "speed": 1.03, "gap": 0.9,
               "ex": 0.05, "goi_y": "tự tin, thân thiện, [ngạc nhiên] khi khen điểm nổi bật, "
               "[nghiêm túc] khi nói nhược điểm"},
    "kinh_di": {"pitch": -0.5, "gain": -2.0, "k": 1.2, "label": "Kinh dị / Bí ẩn", "base": "bi an", "speed": 0.88, "gap": 1.45,
                "ex": 0.0, "goi_y": "chậm, căng thẳng, [thì thầm] ở chi tiết rợn, [hồi hộp] "
                "trước cao trào, [nghỉ dài] trước khi lộ bí mật"},
}


# MACH CAM XUC tung the loai (AI bien kich bam theo) + DINH NGHIA (de doan dung the loai)
GENRE_ARC = {
    "quang_cao": ("ban/gioi thieu san pham de khach MUA: co keu goi mua, gia, giam gia, dat hang",
                  "câu mở 'Imagine…/Hãy tưởng tượng…' [dịu dàng], câu hỏi gây tò mò [ngạc nhiên] -> "
                  "giới thiệu sản phẩm [hào hứng] hoặc [tự tin] -> "
                  "tính năng, con số [tự tin] -> lợi ích, cảm giác khi dùng [vui] -> nỗi lo của khách "
                  "[nghiêm túc] -> câu chốt kêu gọi mua [hào hứng]"),
    "ke_chuyen": ("ke 1 cau chuyen co nhan vat, dien bien",
                  "mở chuyện [kể chuyện] -> diễn biến [hồi hộp] hoặc [bí ẩn] -> cao trào [kịch tính] -> "
                  "cảm xúc nhân vật ([vui] [buồn] [sợ hãi] [giận dữ]) -> kết [trầm ngâm] hoặc [dịu dàng]"),
    "tin_tuc": ("dua tin, binh luan su kien, so lieu",
                "[nghiêm túc] chủ đạo; tin vui [vui] nhẹ; tin buồn [buồn]; chuyển tin [nghỉ]"),
    "tam_su": ("tam su, podcast, chia se cam xuc ca nhan",
               "[dịu dàng] mở đầu -> chia sẻ [trầm ngâm] -> kỷ niệm buồn [buồn] hoặc [nghẹn ngào] -> "
               "hy vọng [nhẹ nhõm] hoặc [vui]"),
    "review": ("DANH GIA san pham khach quan: uu diem + nhuoc diem, KHONG keu goi mua",
               "giới thiệu [tự tin] -> điểm khen [ngạc nhiên] hoặc [vui] -> nhược điểm [nghiêm túc] -> "
               "kết luận [tự tin]"),
    "kinh_di": ("chuyen ma, bi an, rung ron",
                "[bí ẩn] mở đầu -> chi tiết rợn [thì thầm] -> căng thẳng [hồi hộp] -> cao trào [sợ hãi] "
                "hoặc [kịch tính] -> [nghỉ dài] trước khi tiết lộ"),
}


# DO CAM XUC (nguoi dung chon): k = he so gian bieu cam quanh muc trung tinh.
# Chatterbox: exaggeration = 0.5 + (ex - 0.5) * k; toc do / am luong / ngat nghi cung gian theo k.
# Doc qua manh de doc sai chu -> buoc TU KIEM TRA tu doc lai cau sai.
INTENSITY = {"nhe": ("Nhẹ", 0.7), "vua": ("Vừa", 1.0), "manh": ("Mạnh", 1.35), "rat_manh": ("Rất mạnh", 1.7)}


def intensity_list():
    return [{"id": k, "label": v[0]} for k, v in INTENSITY.items()]


# VI DU MAU da gan dung cho tung the loai (model nho hoc theo vi du tot hon hoc theo luat).
# Dang: (cac cau danh so, cac dong tra ve so|the|tu nhan)
GENRE_EXAMPLE = {
    "quang_cao": ("1|Tired of coffee that goes cold in ten minutes?\n2|Meet ThermoMax.\n"
                  "3|It keeps your drink hot for 24 hours.\n4|No more wasted mornings.\n"
                  "5|Order today and get 20% off!",
                  "1|ngạc nhiên nghỉ|\n2|tự tin|ThermoMax\n3|vui|24 hours\n4|nghiêm túc|\n"
                  "5|hào hứng|20% off"),
    "ke_chuyen": ("1|Ngày ấy, làng tôi nằm sát bìa rừng.\n2|Một đêm, tiếng chó sủa vang khắp xóm.\n"
                  "3|Tôi hé cửa nhìn ra.\n4|Có ai đó đang đứng ngay dưới gốc đa.",
                  "1|kể chuyện|\n2|hồi hộp|\n3|thì thầm nghỉ|\n4|kịch tính|ngay dưới gốc đa"),
    "tin_tuc": ("1|Sáng nay, giá xăng giảm 500 đồng mỗi lít.\n2|Đây là lần giảm thứ ba trong tháng.\n"
                "3|Tuy nhiên, giá điện dự kiến sẽ tăng.",
                "1|nghiêm túc|500 đồng\n2|vui|lần giảm thứ ba\n3|nghiêm túc nghỉ|"),
    "tam_su": ("1|Có những ngày mình thấy thật mệt.\n2|Mình nhớ mẹ nhiều lắm.\n"
               "3|Nhưng rồi mình hiểu, ai cũng có lúc như vậy.\n4|Và ngày mai sẽ ổn thôi.",
               "1|dịu dàng|\n2|nghẹn ngào|nhớ mẹ\n3|trầm ngâm nghỉ|\n4|nhẹ nhõm|ổn thôi"),
    "review": ("1|I've used this phone for two weeks.\n2|The camera is honestly stunning.\n"
               "3|Battery life, though, is disappointing.\n4|Overall, it's worth the price.",
               "1|tự tin|\n2|ngạc nhiên|stunning\n3|nghiêm túc|disappointing\n4|tự tin|worth the price"),
    "kinh_di": ("1|The house had been empty for ten years.\n2|But that night, the lights came on.\n"
                "3|Someone whispered my name.\n4|I turned around.",
                "1|bí ẩn|\n2|hồi hộp|lights came on\n3|thì thầm nghỉ|\n4|sợ hãi nghỉ dài|"),
}


def genre_list():
    return [{"id": k, "label": v["label"]} for k, v in GENRES.items()]


def _cbx_params(emo):
    """(exaggeration, cfg) cho Chatterbox theo the cam xuc; the la -> theo nhom hype/calm."""
    if emo in _CBX_EMO:
        return _CBX_EMO[emo]
    return _CBX_EX.get(_STYLE_OF.get(emo, ""), 0.65), 0.3
_SHORT_SEG = 20          # doan ngan hon (ky tu) -> gop vao doan ben canh, F5 doc doan qua ngan hay loi


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _int_words(n):
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return _ONES[n // 100] + " hundred" + (" " + _int_words(n % 100) if n % 100 else "")
    for div, name in ((10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= div:
            return _int_words(n // div) + " " + name + (" " + _int_words(n % div) if n % div else "")
    return str(n)


def _num_words(tok):
    """'1,234.5' -> 'one thousand two hundred thirty-four point five'."""
    tok = tok.replace(",", "")
    if "." in tok:
        a, b = tok.split(".", 1)
        return _int_words(int(a or 0)) + " point " + " ".join(_ONES[int(d)] for d in b if d.isdigit())
    n = int(tok)
    # 7,200 -> "seventy two hundred" (cach nguoi My doc; "seven thousand two hundred"
    # F5 hay nuot thanh "seventy thousand two hundred")
    if 1100 <= n <= 9900 and n % 100 == 0 and n % 1000 != 0:
        return _int_words(n // 100) + " hundred"
    return _int_words(n)


def _year_words(n):
    if 2000 <= n <= 2009:
        return _int_words(n)
    hi, lo = divmod(n, 100)
    return _int_words(hi) + (" " + ("oh " + _ONES[lo] if lo < 10 else _int_words(lo)) if lo else " hundred")


_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
        "nine": "ninth", "twelve": "twelfth"}


def _ordinal(n):
    w = _int_words(n)
    head, _, last = w.rpartition(" ") if " " in w else ("", "", w)
    pre, unit = "", last
    if unit in _ORD:
        unit = _ORD[unit]
    elif unit.endswith("y"):
        unit = unit[:-1] + "ieth"
    else:
        unit += "th"
    return (head + " " if head else "") + unit


_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"


_LETTER_EN = {"B": "Bee", "C": "See", "D": "Dee", "E": "Ee", "F": "Eff", "G": "Gee", "H": "Aitch",
              "J": "Jay", "K": "Kay", "L": "Ell", "M": "Em", "N": "En", "O": "Oh", "P": "Pee", "Q": "Cue",
              "R": "Ar", "S": "Ess", "T": "Tee", "U": "You", "V": "Vee", "W": "Double you", "X": "Ex",
              "Y": "Why", "Z": "Zee"}


def normalize_numbers_en(text):
    """So/tien/% -> chu cho giong clone tieng Anh (F5 doc chu so hay nuot/sai)."""
    t = text or ""
    # "co-workers" -> "co workers": F5 hay ngat/doc vap o dau gach giua chu
    # tien to ngan viet lien ("co-worker" -> "coworker"; de cach thanh "co worker" thi F5 doc "cow worker")
    t = re.sub(r"\b(co|re|pre|non|un|de|mis|sub|anti|multi|semi|mid|inter|over|under)-(?=[A-Za-z])",
               r"\1", t, flags=re.I)
    t = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", t)
    # ky hieu F5 khong doc (hoac doc lech thoi luong) -> chu
    t = re.sub(r"\s*&\s*", " and ", t)
    t = re.sub(r"(?<=\d)\s*/\s*(mo|month)\b", " per month", t)
    t = re.sub(r"(?<=\d)\s*/\s*(yr|year)\b", " per year", t)
    t = re.sub(r"(?<=\d)\s*/\s*(hr|hour)\b", " per hour", t)
    t = re.sub(r"(?<=\d)\s*/\s*(day|week)\b", r" per \1", t)
    t = re.sub(r"\s*−\s*(?=\$?\d)", " minus ", t)          # dau tru that (U+2212)
    t = re.sub(r"(?<=\d)\s*\+\s*(?=\$?\d)", " plus ", t)
    t = re.sub(r"(?<=\d)\s*=\s*(?=\$?\d)", " equals ", t)
    # nhiet do: Chatterbox/F5 doc "65°C" thanh tieng la ("mực") -> chu
    t = re.sub(r"(\d)\s*°\s*C\b", r"\1 degrees Celsius", t)
    t = re.sub(r"(\d)\s*°\s*F\b", r"\1 degrees Fahrenheit", t)
    t = re.sub(r"(\d)\s*°", r"\1 degrees", t)
    # (ngoac ngan) va gach dai giua cau -> cho ngat tu nhien, khong doc dinh lien
    t = re.sub(r"\s*\(([^()\n]{1,60})\)", r", \1,", t)
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    t = re.sub(r",\s*([,.;:!?])", r"\1", t)
    # chu cai DUNG RIENG sau ten rieng ("Zenith C", "Plan B") -> viet lien "Zenith-C" de doc nhu
    # 1 TEN. (Doi thanh "Zenith See" thi model hieu "see" la dong tu: "Zenith. See?")
    t = re.sub(r"\b([A-Z][a-z]{1,20}) ([B-HJ-Z])\b(?![.'\-]\w)", r"\1-\2", t)
    if not re.search(r"\d", t):
        return t
    # day so liet ke "300, 220, 150, 100" -> ngat bang dau cham: dau phay qua ngan,
    # F5 doc dinh "three hundred two hundred twenty" thanh "three oh two twenty"
    t = re.sub(r"\$?\d{2,}(?:[\d,.]*\d)?(?:,\s+\$?\d{2,}(?:[\d,.]*\d)?){2,}",
               lambda m: re.sub(r",\s+", ". ", m.group(0)), t)
    t = re.sub(r"\$(%s)(?:\s*(k|K|m|M|bn|billion|million|thousand)\b)?" % _NUM,
               lambda m: _num_words(m.group(1)) + {"k": " thousand", "K": " thousand", "m": " million",
                                                  "M": " million", "bn": " billion"}.get(m.group(2) or "", (" " + m.group(2)) if m.group(2) else "") + " dollars", t)
    t = re.sub(r"(%s)\s*%%" % _NUM, lambda m: _num_words(m.group(1)) + " percent", t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: _int_words(int(m.group(1))) + (
        " o'clock" if m.group(2) == "00" else " oh " + _ONES[int(m.group(2))] if m.group(2)[0] == "0"
        else " " + _int_words(int(m.group(2)))), t)
    t = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: _ordinal(int(m.group(1))), t)
    t = re.sub(r"\b(\d+)\s*[-–]\s*(\d+)\b", lambda m: m.group(1) + " to " + m.group(2), t)
    t = re.sub(r"\b(1[1-9]\d\d|20\d\d)\b(?![,.]\d)", lambda m: _year_words(int(m.group(1))), t)
    t = re.sub(r"\b(%s)\b" % _NUM, lambda m: _num_words(m.group(1)), t)
    return t


_DIGITS_VI = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def _two_digits_vi(n):
    if n < 10:
        return _DIGITS_VI[n]
    if n == 10:
        return "mười"
    if n < 20:
        u = n % 10
        return "mười lăm" if u == 5 else "mười " + _DIGITS_VI[u]
    t, u = divmod(n, 10)
    head = _DIGITS_VI[t] + " mươi"
    if u == 0:
        return head
    if u == 1:
        return head + " mốt"
    if u == 4:
        return head + " tư"
    if u == 5:
        return head + " lăm"
    return head + " " + _DIGITS_VI[u]


def _three_digits_vi(n, full=False):
    h, rem = divmod(n, 100)
    if not full and h == 0:
        return _two_digits_vi(rem)
    head = _DIGITS_VI[h] + " trăm"
    if rem == 0:
        return head
    if rem < 10:
        return head + " lẻ " + _DIGITS_VI[rem]
    return head + " " + _two_digits_vi(rem)


def _int_words_vi(n):
    if n < 0:
        return "âm " + _int_words_vi(-n)
    if n < 1000:
        return _three_digits_vi(n, full=False)
    units = [(10 ** 9, "tỷ"), (10 ** 6, "triệu"), (1000, "nghìn")]
    parts = []
    rem = n
    started = False
    for div, label in units:
        if rem >= div:
            q, rem = divmod(rem, div)
            parts.append(_int_words_vi(q) + " " + label)
            started = True
    if rem > 0:
        parts.append(_three_digits_vi(rem, full=started))
    return " ".join(parts)


def _num_words_vi(tok):
    s = (tok or "").strip()
    if re.match(r"^\d{1,3}(?:\.\d{3})+$", s):
        return _int_words_vi(int(s.replace(".", "")))
    if re.match(r"^\d{1,3}(?:,\d{3})+$", s):
        return _int_words_vi(int(s.replace(",", "")))
    if "," in s:
        a, b = s.split(",", 1)
        a_clean = a.replace(".", "")
        return _int_words_vi(int(a_clean or 0)) + " phẩy " + (
            _int_words_vi(int(b)) if len(b) <= 2 and b.isdigit() and not b.startswith("0")
            else " ".join(_DIGITS_VI[int(d)] for d in b if d.isdigit()))
    if "." in s:
        a, b = s.split(".", 1)
        return _int_words_vi(int(a or 0)) + " chấm " + (
            _int_words_vi(int(b)) if len(b) <= 2 and b.isdigit() and not b.startswith("0")
            else " ".join(_DIGITS_VI[int(d)] for d in b if d.isdigit()))
    if s.isdigit():
        if len(s) >= 9 and s.startswith("0"):
            return " ".join(_DIGITS_VI[int(d)] for d in s)
        return _int_words_vi(int(s))
    return s


_NUM_VI = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"


_VI_PHRASE_LOANS = (
    (r"\blive\s+stream\b", "lai trym"),
    (r"\bsound\s+card\b", "sao cạc"),
    (r"\bflash\s+sale\b", "phờ lát sêu"),
    (r"\bbest\s+seller\b", "bét sêu lơ"),
    (r"\bcheck\s+in\b", "chếch in"),
    (r"\bmake\s+up\b", "mếch ắp"),
    (r"\bskin\s+care\b", "skin ke"),
    (r"\bhot\s+deal\b", "hót điu"),
    (r"\bfree\s+ship\b", "phờ ri síp"),
    (r"\bfull\s+hd\b", "phun hát đê"),
    (r"\bcamera\s+net\b", "ca mê ra nét"),
    (r"\bsát\s+thường\b", "sát thương"),
    (r"\bkhu\s+hình\b", "khung hình"),
    (r"\b(?:vững|vẫn)\s+(?:chảy|chải)\b", "vững chãi"),
    (r"\bchuyển\s+chua\b", "chuẩn chỉnh"),
    (r"\bnghiệp\s+dương\b", "nghiệp dư"),
    (r"\bphương\s+nền\b", "phông nền"),
)

_VI_WORD_LOANS = {
    "nova": "nô va", "watch": "goát", "smartwatch": "smát goát", "smart": "smát",
    "tech": "tếch", "zone": "dôn", "pro": "pờ rô", "max": "mắc", "plus": "pờ lớt",
    "ultra": "un tra", "mini": "mi ni", "airpods": "e pót", "airpod": "e pót",
    "pods": "pót", "iphone": "ai phôn", "ipad": "ai pát", "macbook": "mác búc",
    "apple": "áp pồ", "samsung": "sam sung", "galaxy": "ga la xi", "xiaomi": "sao mi",
    "oppo": "ốp pô", "sony": "sô ni", "vivo": "vi vô", "realme": "riu mi",
    "pixel": "pích seo", "garmin": "ga min", "livestream": "lai trym",
    "like": "lai", "live": "lai", "share": "se", "follow": "pho lâu",
    "comment": "còm men", "inbox": "in bốc", "idol": "ai đồ", "visual": "vi duồ",
    "combo": "com bô", "micro": "mi crô", "mic": "míc", "soundcard": "sao cạc",
    "camera": "ca mê ra", "background": "bách grao", "decor": "đề co",
    "setup": "sét ắp", "review": "ri viu", "unbox": "ăn bốc", "trend": "tren",
    "trending": "tren đinh", "viral": "vai rồ", "video": "vi đi ô", "clip": "cờ líp",
    "podcast": "pót cát", "channel": "chan nồ", "tiktok": "tích tốc",
    "youtube": "diu túp", "facebook": "phây búc", "instagram": "in ta gram",
    "shopee": "sóp pi", "lazada": "la da đa", "zalo": "da lô", "google": "gú gồ",
    "online": "on lai", "offline": "óp lai", "sale": "sêu", "deal": "điu",
    "voucher": "vau chờ", "freeship": "phờ ri síp", "shop": "sóp", "store": "sto",
    "studio": "stu đi ô", "auto": "ô tô", "app": "áp", "web": "guép",
    "website": "guép sai", "link": "linh", "code": "cốt", "game": "gêm",
    "gamer": "gêm mơ", "gaming": "gêm minh", "wifi": "goai phai",
    "bluetooth": "bờ lu tút", "led": "lét", "oled": "ô lét", "amoled": "a mô lét",
    "chip": "chíp", "full": "phun", "top": "tốp", "vip": "víp", "ceo": "si i ô",
    "marketing": "ma két tinh", "brand": "bờ ren", "design": "đì giai",
    "style": "xì tai", "fashion": "phát sần", "premium": "pờ ri mi ừm",
    "luxury": "lắc xơ ri", "limited": "li mi tựt", "edition": "ê đi sần",
    "version": "vơ sần", "update": "ắp đết", "upgrade": "ắp gờ rết",
    "order": "o đờ", "preorder": "pờ ri o đờ", "ship": "síp", "free": "phờ ri",
    "test": "tét", "check": "chếch", "checkin": "chếch in", "makeup": "mếch ắp",
    "skincare": "skin ke", "hotel": "hô teo", "resort": "ri sọt", "spa": "sờ pa",
    "gym": "gim", "yoga": "i ô ga", "fitness": "phít nịt", "protein": "pờ rô tê in",
    "vitamin": "vi ta min", "collagen": "cô la gen", "serum": "se rum",
    "toner": "tôn nờ", "lotion": "lô sần", "cream": "cờ rim", "cushion": "cút sần",
    "gpu": "gi pi iu", "cpu": "xi pi iu", "ram": "ram", "vram": "vi ram",
    "ssd": "ét ét đê", "pc": "pi xi", "ai": "ây ai", "api": "ây pi ai",
    "kol": "kây ô eo", "koc": "kây ô xi", "usb": "iu ét bi", "hdmi": "hát đê mờ i",
    "fps": "ép pi ét", "4k": "bốn ca", "8k": "tám ca", "5g": "năm gờ", "4g": "bốn gờ",
}
_VI_WORD_LOANS_RE = re.compile(
    r"\b(" + "|".join( sorted(_VI_WORD_LOANS.keys(), key=len, reverse=True) ) + r")\b",
    flags=re.I,
)


def _normalize_vi_loanwords(text):
    """Tach tu ghep CamelCase (NovaWatch -> Nova Watch, TechZone -> Tech Zone) va
    chuyen tu vay muon tieng Anh pho bien sang phien am tieng Viet tu nhien."""
    t = text or ""
    # Tach CamelCase: NovaWatch -> Nova Watch, TechZone -> Tech Zone, iPhone -> i Phone
    t = re.sub(r"([a-zà-ỹ])([A-Z])", r"\1 \2", t)
    # Gach ngang dai giua cau (NovaWatch 5 — chạm để bứt phá) -> dau phay de F5 ngat nhip muot
    t = re.sub(r"\s*[—–]+\s*", ", ", t)
    for pat, repl in _VI_PHRASE_LOANS:
        t = re.sub(pat, repl, t, flags=re.I)
    t = _VI_WORD_LOANS_RE.sub(lambda m: _VI_WORD_LOANS[m.group(1).lower()], t)
    return t


def normalize_numbers_vi(text):
    """Chuyen so, tien te, ngay thang, gio, %, tu vay muon sang chu tieng Viet de F5-VI doc ro va bieu cam."""
    t = text or ""
    t = re.sub(r"\s*&\s*", " và ", t)
    t = re.sub(r"\bVNĐ\b|\bVND\b", " đồng ", t, flags=re.I)
    # Cum 24/7, 24/24 pho bien trong quang cao
    t = re.sub(r"\b24\s*/\s*7\b", "hai mươi tư trên bảy", t)
    t = re.sub(r"\b24\s*/\s*24\b", "hai mươi tư trên hai mươi tư", t)
    t = _normalize_vi_loanwords(t)
    if not re.search(r"\d", t):
        return re.sub(r"[ \t]{2,}", " ", t).strip()
    # ngay dd/mm/yyyy
    t = re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b",
               lambda m: "ngày %s tháng %s năm %s" % (
                   _int_words_vi(int(m.group(1))), _int_words_vi(int(m.group(2))), _int_words_vi(int(m.group(3)))), t)
    # ngay dd/mm (khi co chu 'ngay' hoac 'hom' truoc, hoac thang <= 12)
    t = re.sub(r"(?i)\b(ngày\s+)(\d{1,2})/(\d{1,2})\b",
               lambda m: m.group(1) + "%s tháng %s" % (_int_words_vi(int(m.group(2))), _int_words_vi(int(m.group(3)))), t)
    t = re.sub(r"(?i)\b(tháng\s+)(\d{1,2})/(\d{4})\b",
               lambda m: m.group(1) + "%s năm %s" % (_int_words_vi(int(m.group(2))), _int_words_vi(int(m.group(3)))), t)
    # gio: 14h30, 8h15, 9h
    t = re.sub(r"\b(\d{1,2})[hH](\d{2})\b",
               lambda m: _int_words_vi(int(m.group(1))) + " giờ" + (
                   "" if int(m.group(2)) == 0 else " " + _int_words_vi(int(m.group(2))) + " phút"), t)
    t = re.sub(r"\b(\d{1,2})[hH]\b", lambda m: _int_words_vi(int(m.group(1))) + " giờ", t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b",
               lambda m: _int_words_vi(int(m.group(1))) + " giờ" + (
                   "" if int(m.group(2)) == 0 else " " + _int_words_vi(int(m.group(2))) + " phút"), t)
    # tien te: $100, 150.000đ, 50k
    t = re.sub(r"\$(%s)\b" % _NUM_VI, lambda m: _num_words_vi(m.group(1)) + " đô la", t)
    t = re.sub(r"(%s)\s*(?:đ|₫)\b" % _NUM_VI, lambda m: _num_words_vi(m.group(1)) + " đồng", t)
    t = re.sub(r"\b(\d+(?:[.,]\d+)?)[kK]\b", lambda m: _num_words_vi(m.group(1)) + " nghìn", t)
    # phan tram: 25%, 12,5%
    t = re.sub(r"(%s)\s*%%" % _NUM_VI, lambda m: _num_words_vi(m.group(1)) + " phần trăm", t)
    # khoang so: 10-15 -> 10 den 15
    t = re.sub(r"\b(\d+)\s*[-–]\s*(\d+)\b",
               lambda m: _int_words_vi(int(m.group(1))) + " đến " + _int_words_vi(int(m.group(2))), t)
    # phan so / ty le ngan: 3/4, 5/5
    t = re.sub(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b",
               lambda m: _int_words_vi(int(m.group(1))) + " trên " + _int_words_vi(int(m.group(2))), t)
    # cac con so con lai
    t = re.sub(r"\b(%s)\b" % _NUM_VI, lambda m: _num_words_vi(m.group(1)), t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def _emo_name(s):
    e = s.get("emo") or "binh thuong"
    return _EMO_CANON.get(e, e)


def _split_clone(text, max_chars=_CLONE_MAX_CHARS):
    """Cat theo cau; cau dai cat o dau phay/cham phay; cuoi cung moi cat theo tu.
    KHONG BAO GIO cat giua tu."""
    out = []
    for sent in re.split(r"(?<=[\.\!\?…])\s+|\n+", text or ""):
        sent = sent.strip()
        if not sent:
            continue
        pieces = [sent]
        if len(sent) > max_chars:
            pieces = [x.strip() for x in re.split(r"(?<=[,;:—])\s+", sent) if x.strip()]
        for pc in pieces:
            while len(pc) > max_chars:
                cut = pc.rfind(" ", 0, max_chars)
                if cut < max_chars // 3:
                    cut = max_chars
                out.append(pc[:cut].strip())
                pc = pc[cut:].strip()
            if pc:
                out.append(pc)
    # gop cac cau ngan lien nhau (F5 doc tu nhien hon khi co ngu canh)
    merged = []
    short_merge = 45 if max_chars <= _CLONE_MAX_CHARS_VI else 60
    for x in out:
        if merged and len(merged[-1]) + 1 + len(x) <= max_chars and \
                not merged[-1].endswith(("…", "...")) and len(x) < short_merge:
            merged[-1] = merged[-1] + " " + x
        else:
            merged.append(x)
    return merged


def _gap_after(text, english=True):
    # moi doan da giu ~80ms dem dau/cuoi -> khoang nghi thuc = 160ms + so duoi day
    t = (text or "").rstrip()
    if not english:
        if t.endswith(("…", "...")):
            return 350
        if t.endswith(("?", "!")):
            return 235
        if t.endswith("."):
            return 205
        if t.endswith((",", ";", ":", "—", "-")):
            return 105
        return 85
    if t.endswith(("…", "...")):
        return 300
    if t.endswith(("?", "!")):
        return 200
    if t.endswith("."):
        return 170
    if t.endswith((",", ";", ":", "—", "-")):
        return 80
    return 70


def _fx_clip(wav, semitones=0.0, gain_db=0.0, extra=None):
    """Nang/ha cao do (giu nguyen toc do) + am luong + bo loc them bang ffmpeg."""
    extra = [x for x in (extra or []) if x]
    if not wav or (abs(semitones) < 0.05 and abs(gain_db) < 0.1 and not extra):
        return wav
    from . import clone
    sr = 24000
    flt = []
    if abs(semitones) >= 0.05:
        f = 2 ** (semitones / 12.0)
        flt += ["asetrate=%d" % int(sr * f), "aresample=%d" % sr, "atempo=%.5f" % (1.0 / f)]
    flt += extra
    if abs(gain_db) >= 0.1:
        flt.append("volume=%.1fdB" % gain_db)
    flt.append("alimiter=limit=0.92:level=disabled")
    try:
        return clone._run_ffmpeg(["-f", "wav", "-i", "pipe:0", "-af", ",".join(flt),
                                  "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le",
                                  "-f", "wav", "pipe:1"], data=wav)
    except Exception:
        if extra:                       # bo loc la khong chay -> giu cao do/am luong
            return _fx_clip(wav, semitones, gain_db)
        return wav


def _apply_emph_envelope(wav, text, emph_phrases, boost_db=1.8):
    """Tao diem nhan tu nhien (*tu nhan*) ngay tren cung 1 luong hoi F5-TTS bang duong
    bao cosin muot (+1.8dB) dung vi tri cum tu duoc danh dau, khong cat vun cau."""
    if not wav or not emph_phrases:
        return wav
    words = re.findall(r"\w+", (text or "").lower())
    n_words = len(words)
    if n_words < 2:
        return wav
    ranges = []
    for ph in emph_phrases:
        pw = re.findall(r"\w+", (ph or "").lower())
        if not pw or len(pw) >= n_words:
            continue
        m = len(pw)
        for i in range(n_words - m + 1):
            if words[i:i + m] == pw:
                ranges.append((i / float(n_words), (i + m) / float(n_words)))
                break
    if not ranges:
        return wav
    try:
        import numpy as np
        from array import array
        from . import clone
        sr, _ch, samples = clone._wav_read(wav)
        if len(samples) < sr // 4:
            return wav
        arr = np.frombuffer(samples, dtype=np.int16).astype(np.float32)
        peak = float(np.max(np.abs(arr))) if len(arr) else 0.0
        if peak < 100:
            return wav
        nz = np.where(np.abs(arr) > max(120.0, peak * 0.025))[0]
        i0 = int(nz[0]) if len(nz) else 0
        i1 = int(nz[-1]) if len(nz) else len(arr)
        span = max(1, i1 - i0)
        env = np.ones(len(arr), dtype=np.float32)
        gain_lin = float(10 ** (boost_db / 20.0)) - 1.0
        fade = max(1, int(0.065 * sr))
        for r0, r1 in ranges:
            s0 = max(0, i0 + int(r0 * span) - fade // 2)
            s1 = min(len(arr), i0 + int(r1 * span) + fade // 2)
            if s1 <= s0:
                continue
            w = np.ones(s1 - s0, dtype=np.float32)
            f_len = min(fade, (s1 - s0) // 2)
            if f_len > 1:
                ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, f_len, dtype=np.float32)))
                w[:f_len] = ramp
                w[-f_len:] = ramp[::-1]
            env[s0:s1] = np.maximum(env[s0:s1], 1.0 + gain_lin * w)
        out = np.clip(arr * env, -31800.0, 31800.0).astype(np.int16)
        return clone._wav_write(sr, array("h", out.tobytes()))
    except Exception:
        return wav


def _room_tone(name, sr=24000):
    """Tieng phong that (nen yen tinh nhat) tu file mau cua chinh giong do -> lot vao khoang lang
    thay cho im lang tuyet doi (so 0) — tai nguoi nghe 'tat han' la nhan ra may ghep.
    -> mang float32 ~0.4s, da chinh ve -66..-58 dBFS; mau sach tuyet doi -> nhieu hong nhe."""
    import numpy as np
    from . import clone
    y = None
    try:
        r, _c, smp = clone._wav_read((clone.profile_dir(clone.safe_name(name)) / "ref.wav").read_bytes())
        x = np.frombuffer(smp, dtype=np.int16).astype(np.float32) / 32768.0
        if r == sr and len(x) > sr:
            w = int(0.4 * sr)
            best = None
            for i in range(0, len(x) - w, w // 4):
                seg = x[i:i + w]
                rms = float(np.sqrt((seg ** 2).mean()))
                # bo doan so 0 tuyet doi va doan co tieng tach/hoi (dinh > 5 lan trung binh):
                # tieng phong that phai DEU
                if (rms > 1e-5 and float(np.abs(seg).max()) < 5 * rms
                        and (best is None or rms < best[0])):
                    best = (rms, seg.copy())
            if best:
                y = best[1]
    except Exception:
        y = None
    if y is None:                                   # mau qua sach -> nhieu hong (giong tieng phong)
        rng = np.random.default_rng(7)
        y = np.cumsum(rng.standard_normal(int(0.4 * sr))).astype(np.float32)
        y -= np.convolve(y, np.ones(200) / 200, mode="same")
    y = y - float(y.mean())
    rms = float(np.sqrt((y ** 2).mean())) or 1e-9
    db = 20 * np.log10(rms)
    lo, hi = float(clone._cfg("ROOM_TONE_MIN_DB", -66)), float(clone._cfg("ROOM_TONE_MAX_DB", -58))
    target = min(hi, max(lo, db))
    return y * (10 ** (target / 20.0) / rms)


def _add_room_tone(wav, tone):
    """Tron tieng phong (lap lai, noi muot) duoi CA bai: cho lang co 'khong khi', cho noi bi lan at."""
    import numpy as np
    from array import array
    from . import clone
    sr, _c, smp = clone._wav_read(wav)
    x = np.frombuffer(smp, dtype=np.int16).astype(np.float32) / 32768.0
    if not len(x) or tone is None or not len(tone):
        return wav
    want = float(np.sqrt((tone ** 2).mean()))       # muc tieng phong can giu
    f = min(len(tone) // 4, int(0.05 * sr))
    if f > 1:                                       # noi vong muot: fade dau/cuoi moi lan lap
        ramp = np.linspace(0, 1, f, dtype=np.float32)
        tone = tone.copy()
        tone[:f] *= ramp
        tone[-f:] *= ramp[::-1]
        step = len(tone) - f
        bed = np.zeros(len(x) + len(tone), dtype=np.float32)
        for i in range(0, len(x), step):
            bed[i:i + len(tone)] += tone
        bed = bed[:len(x)]
    else:
        bed = np.resize(tone, len(x))
    got = float(np.sqrt((bed ** 2).mean())) or 1e-9
    bed *= want / got                               # noi vong lam lech muc -> chinh lai
    y = np.clip(x + bed, -1.0, 1.0)
    return clone._wav_write(sr, array("h", (y * 32767).astype(np.int16).tobytes()))


def _natural_gap(gap_ms, text, nxt_text, seed):
    """Ngat nghi KHONG deu nhu may: dao dong quanh muc chuan, cau hoi / cam than nghi lau hon,
    cau sau noi tiep y (and/but/so/va/nhung...) nghi ngan hon."""
    import random
    r = random.Random(seed)
    g = gap_ms * r.uniform(0.82, 1.25)
    t = (text or "").rstrip()
    if t.endswith(("?", "!")):
        g *= 1.15
    nx = (nxt_text or "").lstrip().lower()
    if re.match(r"(and|but|so|or|because|then|và|nhưng|nên|rồi|vì|thì)\b", nx):
        g *= 0.8
    return g


def _master(wav, peak_db=-1.5):
    """Chinh do to CA BAI bang 1 he so (dinh -1.5 dBFS) -> giu nguyen chenh lech to/nho giua cac cau."""
    import numpy as np
    from array import array
    from . import clone
    sr, _c, smp = clone._wav_read(wav)
    x = np.frombuffer(smp, dtype=np.int16).astype(np.float32)
    peak = float(np.abs(x).max())
    if peak < 1:
        return wav
    g = (32767.0 * 10 ** (peak_db / 20.0)) / peak
    return clone._wav_write(sr, array("h", np.clip(x * g, -32768, 32767).astype(np.int16).tobytes()))


def _silence(ms, sr=24000):
    from array import array
    from . import clone
    return clone._wav_write(sr, array("h", [0]) * max(1, int(sr * ms / 1000.0)))


def clone_segments(text, max_chars=_CLONE_MAX_CHARS):
    """Van ban (co/khong the) -> danh sach segment cho giong clone."""
    # xuong doan (dong trong) = nghi lau hon chut nhu nguoi doc that chuyen y
    text = re.sub(r"[ \t]*\n[ \t]*\n\s*", " [nghi] ", (text or "").strip())
    if not has_markup(text):
        return [{"text": t, "emo": "binh thuong", "rate": 0} for t in _split_clone(text, max_chars)]
    # the su kien ([wow] [cuoi] [ha] [yeah]...) KHONG doc thanh chu: F5 doc "Wow!",
    # "Huh.", "Haha" chen vao giua cau nghe nhu doc sai kich ban -> chi giu khoang nghi
    raw0 = []
    skip_pause = False
    for x in parse_script(text):
        if x.get("emph_pause"):
            continue
        if x.get("event"):
            if raw0 and "pause" in raw0[-1] and not raw0[-1].get("ellipsis"):
                raw0.pop()                      # bo khoang nghi truoc su kien
            raw0.append({"pause": 150, "speaker": x.get("speaker")})
            skip_pause = True
            continue
        if skip_pause and "pause" in x and not x.get("ellipsis"):
            skip_pause = False                  # bo khoang nghi sau su kien
            continue
        skip_pause = False
        raw0.append(x)
    raw = []
    k = 0
    while k < len(raw0):                    # "A ... B" cung tong giong -> 1 doan "A... B"
        x = raw0[k]
        if (x.get("ellipsis") and raw and "text" in raw[-1] and k + 1 < len(raw0)
                and "text" in raw0[k + 1] and not raw[-1].get("event")
                and not raw0[k + 1].get("event")
                and raw[-1].get("emo") == raw0[k + 1].get("emo")
                and raw[-1].get("fx") == raw0[k + 1].get("fx")
                and len(raw[-1]["text"]) + len(raw0[k + 1]["text"]) < max_chars):
            ep = (raw[-1].get("emph_phrases") or []) + (raw0[k + 1].get("emph_phrases") or [])
            raw[-1] = dict(raw[-1], text=raw[-1]["text"].rstrip(" .,") + "... " + raw0[k + 1]["text"].lstrip(),
                           emph=bool(raw[-1].get("emph") or raw0[k + 1].get("emph")),
                           emph_phrases=ep)
            k += 2
            continue
        if x.get("ellipsis") and raw and "text" in raw[-1] and not raw[-1].get("event"):
            raw[-1] = dict(raw[-1], text=raw[-1]["text"].rstrip(" .,") + "...")
        raw.append(x)
        k += 1
    merged = []
    for x in raw:
        prev = merged[-1] if merged else None
        if (prev is not None and "text" in x and "text" in prev
                and not x.get("event") and not prev.get("event")
                and x.get("emo") == prev.get("emo") and x.get("fx") == prev.get("fx")
                and (x.get("emph") or prev.get("emph"))
                and len(prev["text"]) + len(x["text"]) < max_chars):
            base = prev if not prev.get("emph") else x
            sep = "" if x["text"][:1] in ",.;:!?" else " "
            ep = (prev.get("emph_phrases") or []) + (x.get("emph_phrases") or [])
            merged[-1] = dict(base, text=prev["text"].rstrip() + sep + x["text"].lstrip(),
                              emph=True, emph_phrases=ep, rate=base.get("rate", 0))
        else:
            merged.append(dict(x))
    merged = _merge_clone(merged, max_chars)
    segs = []
    for s in merged:
        if "text" in s and not s.get("event") and len(s["text"]) > max_chars:
            for t in _split_clone(s["text"], max_chars):
                segs.append(dict(s, text=t))
        else:
            segs.append(s)
    return segs


def _merge_clone(segs, max_chars):
    """Gop doan lien nhau CUNG tong giong (khong co khoang nghi xen giua) thanh 1 lan
    doc lien mach; doan qua ngan hoac bi chen the giua cum tu thi nhap vao doan ben canh.
    F5 doc tung manh ngan rieng le -> moi manh 1 kieu, ngu dieu dut quang, de nuot/doc sai."""
    def key(x):
        return (x.get("emo"), x.get("fx"), x.get("speaker"), round(float(x.get("gain") or 0), 1),
                round(float(x.get("mrate") or 0), 1))

    def core(x):
        return len(re.sub(r"[\W_]", "", x.get("text") or ""))

    def neutral(x):                     # doan ngan KHONG co tong rieng -> nhap duoc vao doan ben
        return x.get("emo") in (None, "binh thuong", "ke chuyen") and not x.get("emph")

    def ends_sent(t):
        return bool(re.search(r"[.?!…]\s*$", (t or "").strip()))

    def ends_clause(t):
        return bool(re.search(r"[.?!…,:;—\-]\s*$", (t or "").strip()))

    def join(a, b):
        sep = "" if b[:1] in ",.;:!?" else " "
        return a.rstrip() + sep + b.lstrip()

    out = []
    for x in segs:
        prev = out[-1] if out else None
        if (prev is not None and "text" in x and "text" in prev
                and x.get("speaker") == prev.get("speaker") and x.get("fx") == prev.get("fx")
                and len(prev["text"]) + 1 + len(x["text"]) <= max_chars
                and (key(x) == key(prev)
                     or not ends_clause(prev["text"])
                     or (not ends_sent(prev["text"]) and (core(x) < 42 or core(prev) < 42))
                     or (core(x) < _SHORT_SEG and neutral(x))
                     or (core(prev) < _SHORT_SEG and neutral(prev)))):
            # giu TONG GIONG cua doan co the cam xuc (doan ngan [buon] gop voi cau thuong
            # khong duoc mat [buon]); ca hai cung trung tinh / cung tong -> lay doan dai hon
            if neutral(prev) != neutral(x):
                base = x if neutral(prev) else prev
            else:
                base = prev if core(prev) >= core(x) else x
            ep = (prev.get("emph_phrases") or []) + (x.get("emph_phrases") or [])
            out[-1] = dict(base, text=join(prev["text"], x["text"]),
                           emph=bool(prev.get("emph") or x.get("emph")),
                           emph_phrases=ep)
        else:
            out.append(dict(x))
    return out


def _voice_max_chars(name, english=True):
    """F5 tu cat van ban dai hon ~ (ky tu/giay cua mau) x (22 - do dai mau) roi noi
    bang cross-fade -> de nuot chu o cho noi. Voi tieng Viet, worker da patch chunk_text
    va tinh fix_duration theo am tiet -> giu tron cau <= 165 ky tu de doc lien mach."""
    from . import clone
    if not english:
        return _CLONE_MAX_CHARS_VI
    cap = _CLONE_MAX_CHARS
    try:
        d = clone.profile_dir(clone.safe_name(name))
        dur = clone._wav_seconds((d / "ref.wav").read_bytes())
        nb = len((d / "ref.txt").read_text(encoding="utf-8").strip().encode("utf-8"))
        if dur > 0.5 and nb:
            return int(max(85, min(cap, 0.85 * nb / dur * (22 - min(dur, 12)))))
    except Exception:
        pass
    return cap


VERIFY_LAST = {}          # ket qua tu kiem tra lan doc gan nhat (de bao cao / test)
TRIM_LAST = []            # moc cat am thua cuoi doan (de test)
MISS_LAST = []            # doan thieu chu then chot / keo dai chu (de test)


def _words_cmp(t, english):
    t = (normalize_numbers_en(t) if english else normalize_numbers_vi(t)).lower()
    toks = re.findall(r"[\w']+", t.replace("-", " "))
    if english:                      # chu cai rieng "c" == "see" (Whisper ghi kieu nao cung duoc)
        toks = [_LETTER_EN.get(x.upper(), x).lower() if len(x) == 1 and x not in ("a", "i") else x
                for x in toks]
    return toks


def _cut_tail(wav, t_end):
    """Cat wav tai giay t_end (fade 30ms) — bo am thua Chatterbox hay doc them o cuoi doan."""
    from array import array
    from . import clone
    sr, _ch, samples = clone._wav_read(wav)
    a = array("h", samples)
    n = min(len(a), max(1, int(t_end * sr)))
    a = a[:n]
    f = min(len(a), int(0.03 * sr))
    for k in range(f):
        a[len(a) - 1 - k] = int(a[len(a) - 1 - k] * (k / float(f)))
    return clone._wav_write(sr, a)


_KEY_WORDS_EN = set(("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                     "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty "
                     "sixty seventy eighty ninety hundred thousand million billion percent degrees "
                     "celsius fahrenheit dollars cents").split())
_KEY_WORDS_VI = set(("không một hai ba bốn năm sáu bảy tám chín mười mươi trăm nghìn ngàn triệu tỷ "
                     "phần trăm độ đồng").split())


def _key_tokens(text, english):
    """Chu THEN CHOT bat buoc phai doc du: con so, don vi, ten rieng / chu cai viet hoa
    (khong tinh chu dau cau). Thieu 1 chu = doc sai (ti le khop cao van khong cho qua)."""
    keys = []
    words = re.findall(r"[\w']+", (text or "").replace("-", " "))
    first = True
    for w in words:
        lw = w.lower()
        if lw in (_KEY_WORDS_EN if english else _KEY_WORDS_VI):
            keys.append(lw)
        elif w[:1].isupper() and not first and lw not in ("i", "i'm", "i'll", "i've", "i'd", "a"):
            keys.append(_LETTER_EN.get(w, w).lower() if len(w) == 1 and english else lw)
        first = False
    sent_starts = {m.group(1).lower() for m in re.finditer(r"(?:^|[.!?]\s+)([\w']+)", text or "")}
    return [k for k in keys if k not in sent_starts or k in _KEY_WORDS_EN or k in _KEY_WORDS_VI]


_LETTER_SOUND = {v.lower(): k.lower() for k, v in _LETTER_EN.items()}   # "see" -> "c"


def _name_like(k, h):
    """Ten rieng nghe gan dung: cung chu cai dau, dai tuong duong, giong >= 60% ("zennic" ~ "zenith")."""
    import difflib
    return (h[:1] == k[:1] and abs(len(h) - len(k)) <= 2
            and difflib.SequenceMatcher(None, k, h).ratio() >= 0.6)


def _missing_keys(keys, heard, english):
    """So chu then chot bi THIEU. So / don vi: phai khop dung (Whisper nghe so rat chuan).
    Ten rieng: cho gan dung (Whisper hay viet sai ten: "Zenith C" -> "ZenniC").
    Chu cai rieng ("see" = C): nhan "c" / "see" / "sea" hoac dinh cuoi ten ("zennic")."""
    import difflib
    from collections import Counter
    nums = _KEY_WORDS_EN if english else _KEY_WORDS_VI
    have = Counter(heard)
    miss = 0
    need_num = Counter(k for k in keys if k in nums)
    miss += sum(max(0, n - have.get(k, 0)) for k, n in need_num.items())
    for k in set(k for k in keys if k not in nums):
        if k in have:
            continue
        if k in _LETTER_SOUND:
            ch = _LETTER_SOUND[k]
            alt = {ch, k, {"see": "sea", "bee": "be", "tee": "tea", "pee": "pea", "why": "y"}.get(k, k)}
            if alt & set(heard) or any(len(h) > 3 and h.endswith(ch) for h in heard):
                continue
            miss += 1
            continue
        if any(_name_like(k, h) for h in heard):
            continue
        miss += 1
    return miss


def _canon_names(heard, keys, english):
    """Dua ten rieng Whisper viet sai ve dung cach viet cua kich ban truoc khi tinh ti le khop
    ("zennic" -> "zenith", "see"; "zenith c" -> "zenith", "see")."""
    import difflib
    nums = _KEY_WORDS_EN if english else _KEY_WORDS_VI
    names = [k for k in keys if k not in nums and k not in _LETTER_SOUND]
    after = {keys[i]: keys[i + 1] for i in range(len(keys) - 1) if keys[i + 1] in _LETTER_SOUND}
    out = []
    for h in heard:
        if h in _LETTER_SOUND.values() or h in ("sea", "be", "tea", "pea", "y"):
            lk = next((k for k, c in _LETTER_SOUND.items() if c == h or
                       {"sea": "see", "be": "bee", "tea": "tee", "pea": "pee", "y": "why"}.get(h) == k), None)
            out.append(lk if lk in keys else h)
            continue
        best = next((k for k in names if _name_like(k, h)), None)
        if best and h != best:
            out.append(best)
            nxt = after.get(best)
            if nxt and h.endswith(_LETTER_SOUND[nxt]):      # "zennic": ten + chu cai dinh lien
                out.append(nxt)
            continue
        out.append(h)
    return out


def _voiced_after(wav, t0):
    """So giay CO TIENG NOI trong wav tinh tu giay t0 (khung 20ms, nguong 10% dinh)."""
    from . import clone
    sr, _ch, samples = clone._wav_read(wav)
    import numpy as np
    x = np.frombuffer(samples, dtype=np.int16).astype(np.float32)
    peak = float(np.abs(x).max()) if len(x) else 0.0
    seg = x[int(t0 * sr):]
    if peak <= 0 or len(seg) < sr * 0.02:
        return 0.0
    fr = int(sr * 0.02)
    n = len(seg) // fr
    rms = np.sqrt((seg[:n * fr].reshape(n, fr) ** 2).mean(axis=1))
    return float((rms > peak * 0.1).sum()) * 0.02


def _verify_clips(texts, clips, english, model_name=None):
    """Whisper nghe lai tung doan -> ti le khop chu (0..1). Loi nghe -> None (coi nhu dat).
    Doan co AM THUA sau tu cuoi cua kich ban (vd Chatterbox doc them "Send!") -> CAT bo
    ngay trong `clips` (sua tai cho) roi cham lai."""
    import difflib
    import tempfile as _tf
    from . import clone, engine
    m_name = model_name or str(clone._cfg("CLONE_VERIFY_MODEL", "small"))
    try:
        model = engine.get_model(m_name)
    except Exception:
        return [None] * len(texts)
    out = []
    with _tf.TemporaryDirectory(prefix="verify_") as td:
        for i, (t, wav) in enumerate(zip(texts, clips)):
            if not wav:
                out.append(0.0)
                continue
            f = Path(td) / ("%d.wav" % i)
            f.write_bytes(wav)
            try:
                res = engine.transcribe(model, f, language="en" if english else "vi")
            except Exception:
                out.append(None)
                continue
            a = _words_cmp(t, english)
            toks = []                       # (tu, bat dau, ket thuc) theo moc thoi gian Whisper
            ws = []
            for w in res.get("words") or []:    # Whisper tach "30" + "%" -> gop lai truoc khi so
                txt = (w.get("text") or "").strip()
                if ws and txt[:1] in "%°":
                    ws[-1] = (ws[-1][0] + txt, ws[-1][1], float(w.get("end") or 0))
                else:
                    ws.append((txt, float(w.get("start") or 0), float(w.get("end") or 0)))
            long_word = False
            lim = float(clone._cfg("CLONE_VERIFY_MAX_WORD_SEC", 1.5))
            for wi, (txt, st, en) in enumerate(ws):
                tks = _words_cmp(txt, english)
                for tk in tks:
                    toks.append((tk, st, en))
                # Whisper gop "65°C" = 4 chu -> cho phep dai tuong ung; chu cuoi doan bo qua
                if wi < len(ws) - 1 and (en - st) > max(lim, 0.55 * len(tks) + 0.6):
                    long_word = True
            b = [x[0] for x in toks] or _words_cmp(res.get("text") or "", english)
            if not a:
                out.append(None)
                continue
            sm = difflib.SequenceMatcher(None, a, b)
            blocks = [m for m in sm.get_matching_blocks() if m.size]
            if toks and blocks:
                last = blocks[-1]
                tail = len(b) - (last.b + last.size)       # so tu THUA sau tu cuoi khop
                if last.a + last.size >= len(a) - 1 and tail == 0:
                    # Whisper co the KHONG nghe ra am thua (bo qua vi ngan) -> do nang luong:
                    # con tieng noi > 0.2s sau tu cuoi -> cat
                    end_ok = toks[-1][2]
                    try:
                        if _voiced_after(wav, end_ok + 0.15) > 0.2:
                            clips[i] = _cut_tail(wav, end_ok + 0.12)
                            TRIM_LAST.append(round(end_ok + 0.12, 2))
                    except Exception:
                        pass
                elif last.a + last.size >= len(a) - 1 and 0 < tail <= 3:
                    end_ok = toks[last.b + last.size - 1][2]
                    nxt = toks[last.b + last.size][1]
                    cut = max(end_ok + 0.08, (end_ok + nxt) / 2.0)
                    try:
                        clips[i] = _cut_tail(wav, cut)
                        b = b[:last.b + last.size]
                        TRIM_LAST.append(round(cut, 2))
                    except Exception:
                        pass
            ratio = difflib.SequenceMatcher(None, a, _canon_names(b, _key_tokens(t, english), english)).ratio()
            # thieu CHU THEN CHOT (so, don vi, ten rieng) -> coi nhu sai, du ti le khop cao
            # dem tren CA CAU Whisper nghe (de "Zenith C" -> "zenith see" giong kich ban)
            heard_all = list(b) + _words_cmp(res.get("text") or "", english)
            missing = _missing_keys(_key_tokens(t, english), heard_all, english)
            # chu bi KEO DAI bat thuong (model "lap cho trong" khi nuot chu) -> sai
            stretched = long_word
            if missing or stretched:
                ratio = min(ratio, 0.5)
                MISS_LAST.append({"doan": i, "thieu": missing, "keo_dai": stretched})
            out.append(ratio)
    return out


def synth_clone(text, voice, base_rate="+0%", progress=None, genre=None, intensity=None):
    """Doc bang giong NHAN BAN co bieu cam. voice = 'c:<ten>'. Tra ve (wav, mime, ext).
    genre: the loai video (GENRES) -> tong nen, toc do, ngat nghi rieng.
    intensity: do cam xuc (INTENSITY: nhe / vua / manh / rat_manh)."""
    gp = GENRES.get(genre or "") or {}
    from .. import config as _conf
    k_emo = INTENSITY.get(intensity or getattr(_conf, "DEFAULT_INTENSITY", "vua"), INTENSITY["vua"])[1]
    k_emo *= float(gp.get("k") or 1.0)          # the loai: tin tuc diem tinh, ke chuyen/kinh di rong hon
    from . import clone
    name = voice.split(":", 1)[1] if voice.startswith("c:") else voice
    english = not clone._is_vietnamese(strip_tags(text))
    if english:
        text = normalize_numbers_en(text)
    else:
        text = normalize_numbers_vi(text)
    use_cbx = english and clone.engine_for(True) == "chatterbox"
    if use_cbx:
        # Chatterbox khong nhan chu that duoc; giu *...* thi cau bi cat giua cum
        # ("...Auto Brew system | will heat") -> bo dau nhan, chi ngat o dau cau / dau phay
        text = re.sub(r"\*([^*\n]{1,80})\*", r"\1", text)
    # Chatterbox: doan ~200 ky tu (1-2 cau) — doan dai hon de doc lech / nuot cum tu
    segs = clone_segments(text, int(clone._cfg("CBX_MAX_CHARS", 200)) if use_cbx
                          else _voice_max_chars(name, english=english))
    try:
        have_styles = clone.available_styles(name)
    except Exception:
        have_styles = set()
    if gp.get("base"):               # cau KHONG gan the -> doc theo tong nen cua the loai
        for s in segs:
            if "text" in s and _emo_name(s) == "binh thuong":
                s["emo"] = gp["base"]
    items = []                       # (vi tri seg, text doc, he so toc do)
    for k, s in enumerate(segs):
        if "text" not in s:
            continue
        t = s["text"].strip().lstrip(",;:—-) ").strip()
        if not re.search(r"\w", t):
            continue
        emo = _emo_name(s)
        if s.get("event"):
            continue
        natural = bool(_style_for(emo, have_styles))
        if emo == "het" and t[-1:] in ".,":
            t = t[:-1] + "!"
        elif emo in _HAPPY and t.endswith("."):
            t = t[:-1] + "!"                         # F5 tu len giong tu nhien o cau cam than
        mr = float(s.get("mrate") or 0)               # [cham]/[nhanh]: nguoi dung chu dong
        # Voi tieng Viet, giu toc do gan nhip mau that (tranh ep nhanh lam mat nhan nha cau tu)
        if not english:
            er = max(-5.0 * k_emo, min(1.5 * k_emo, (float(s.get("rate") or 0) - mr) * 0.18 * k_emo))
        else:
            er = max((-5.0 if natural else -8.0) * k_emo,
                     min(3.0 * k_emo, (float(s.get("rate") or 0) - mr) * 0.3 * k_emo))
        sp = 1.0 + (er + mr) / 100.0
        if s.get("emph_phrases"):
            sp *= 0.97                                # chua nhip de model nhan ro cum *tu nhan*
        sp *= float(gp.get("speed") or 1.0)
        items.append((k, t, max(0.82, min(1.15, sp))))
    if not items:
        raise ValueError("Văn bản không có gì để đọc.")

    styles = [_style_for(_emo_name(segs[k]), have_styles) for k, _t, _s in items]
    rot = [""] + [a for a in getattr(clone, "ALT_STYLES", ()) if a in have_styles]
    if len(rot) > 1:
        k_rot = 0
        for n_i, (k, t, _s) in enumerate(items):
            if styles[n_i]:
                continue
            styles[n_i] = rot[k_rot % len(rot)]
            k_rot += 1
    exs = cfgs = None
    if use_cbx:
        pr = [_cbx_params(_emo_name(segs[k])) for k, _t, _s in items]
        ex_add = float(gp.get("ex") or 0.0)
        exs = [round(max(0.25, min(1.0 + 0.3 * (k_emo > 1), 0.5 + (p[0] + ex_add - 0.5) * k_emo)), 3)
               for p in pr]
        # bieu cam manh -> Chatterbox noi nhanh/gat hon -> ha nhe cfg de giu nhip
        cfgs = [round(max(0.2, p[1] - 0.05 * max(0.0, k_emo - 1.0) / 0.35), 3) for p in pr]
    texts = [t for _k, t, _s in items]
    speeds = [sp for _k, _t, sp in items]

    def run(idx, prog=None, calm_down=0):
        pick = lambda L: [L[i] for i in idx] if L else None
        e2, c2 = pick(exs), pick(cfgs)
        if calm_down and e2:
            # doc lai cau bi SAI CHU: bot bieu cam + tang bam kich ban -> ro chu hon
            # (cau doc dung giu nguyen cam xuc manh)
            e2 = [round(max(0.3, x - 0.15 * calm_down), 3) for x in e2]
            c2 = [round(min(0.7, x + 0.15 * calm_down), 3) for x in c2]
        return clone.synth_cues_clone(pick(texts), name, rate=base_rate, progress=prog,
                                      speeds=pick(speeds), styles=pick(styles),
                                      exaggerations=e2, cfgs=c2)
    clips = run(list(range(len(items))), progress)
    # TU KIEM TRA: Whisper nghe lai tung doan; doan doc lech (nuot / doc sai chu) -> doc lai,
    # giu ban khop chu nhat. Tat: CLONE_VERIFY = False.
    if clone._cfg("CLONE_VERIFY", True):
        floor = float(clone._cfg("CLONE_VERIFY_MIN", 0.9))
        score = _verify_clips(texts, clips, english)
        for _round in range(int(clone._cfg("CLONE_VERIFY_RETRY", 3))):
            bad = [i for i, sc in enumerate(score) if sc is not None and sc < floor]
            if not bad:
                break
            try:
                again = run(bad, calm_down=_round + 1)
            except Exception:
                break
            new_sc = _verify_clips([texts[i] for i in bad], again, english)
            for i, wav, sc in zip(bad, again, new_sc):
                if wav and (sc is None or sc > score[i]):
                    clips[i], score[i] = wav, sc
        VERIFY_LAST.update(n=len(score), low=[round(x, 2) for x in score if x is not None and x < floor])
    by_seg = {}
    for (k, t, _sp), wav in zip(items, clips):
        s = segs[k]
        emo = _emo_name(s)
        if s.get("emph_phrases"):
            wav = _apply_emph_envelope(wav, t, s.get("emph_phrases"))
        semi, gain = _CLONE_EMO_FX.get(emo, (0, 0))
        # KHONG doi cao do nhan tao theo cam xuc / the loai: ffmpeg doi cao do bang tua nhanh/cham
        # -> doi luon AM SAC (nghe nhu nguoi khac, do that: cau hao hung con giong 64%). Cao do
        # cam xuc den tu model + mau cam xuc cua chinh giong do. Bat lai: CLONE_EMO_PITCH = True.
        if clone._cfg("CLONE_EMO_PITCH", False):
            semi += _EMO_PITCH.get(emo, 0.0) * k_emo + float(gp.get("pitch") or 0.0)
        # moi doan da chuan hoa cung DO TO GIONG NOI -> tra lai chenh lech to-nho theo cam xuc
        # (vui/hao hung to hon, buon nhe hon, thi tham nhe han); cuoi bai chinh 1 lan cho ca bai.
        hi = float(clone._cfg("CLONE_EMO_GAIN_CAP", 1.5)) * k_emo
        gain = max(-4.0, min(hi, gain * 0.4 * k_emo)) + float(gp.get("gain") or 0.0)
        gain += float(s.get("gain") or 0)
        extra = [_CLONE_FX_FILTER.get(s.get("fx") or "")]
        if use_cbx:
            # Chatterbox KHONG nhan toc do -> co gian tung cau (giu cao do): cau buon cham,
            # cau hao hung nhanh, va o "Toc do" cham/nhanh cung co tac dung
            # Chatterbox da tu doc nhanh khi hao hung -> chi duoc LAM CHAM (buon / o Toc do cham)
            tempo = clone._rate_to_speed(base_rate) * min(1.0, _sp)
            if abs(tempo - 1.0) > 0.015:
                extra.append("atempo=%.4f" % max(0.6, min(1.5, tempo)))
        by_seg[k] = (_fx_clip(wav, semi, gain, extra), t)

    out = []
    for k, s in enumerate(segs):
        if "pause" in s:
            # Moi clip da co ~80ms dem dau/cuoi -> tru 130ms de [nghi] khong bi ngat qua lau
            out.append(_silence(max(120, int(s["pause"]) - 130)))
            continue
        if k not in by_seg or not by_seg[k][0]:
            continue
        wav, t = by_seg[k]
        out.append(wav)
        nxt = segs[k + 1] if k + 1 < len(segs) else None
        if nxt is not None and "pause" not in nxt:   # khoang nghi tu nhien giua cau
            gap = (_gap_after(t, english=english) * (_SLOW_GAP.get(_emo_name(s), 1.0) ** k_emo)
                   * float(gp.get("gap") or 1.0))
            gap = _natural_gap(gap, t, nxt.get("text"), __import__("zlib").crc32(("%s|%d|%s" % (name, k, t)).encode("utf-8")))
            out.append(_silence(gap))
    from .tts import _concat_clips
    data, mime, ext = _concat_clips(out)
    if clone._cfg("ROOM_TONE", True) and data[:4] == b"RIFF":
        try:
            data = _add_room_tone(data, _room_tone(name))
        except Exception:
            pass
    if data[:4] == b"RIFF":
        try:
            data = _master(data, -1.5 + min(0.0, float(gp.get("gain") or 0.0)))   # tam su / kinh di nhe hon
        except Exception:
            pass
    return data, mime, ext
