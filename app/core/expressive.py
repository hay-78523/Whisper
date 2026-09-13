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
PAUSES = {"nghi ngan": 300, "nghi": 600, "nghi dai": 1200}

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
            if not s or not re.search(r"\w", s):   # bo doan chi co dau cau
                return
            for chunk in split_text(s, max_chars=1200):   # doan qua dai thi cat theo cau
                segs.append({"text": chunk, "emo": cur_name, "emph": emph,
                             "rate": cur_emotion[0] + extra_rate,
                             "pitch": cur_emotion[1] + extra_pitch,
                             "speaker": cur_speaker})

        for m in _TOKEN_RE.finditer(part):
            add_text(part[pos:m.start()])
            pos = m.end()
            if m.group(1):                                 # [tag]
                tag = _norm_tag(m.group(1)[1:-1])
                if tag in EMOTIONS:
                    cur_emotion, cur_name = EMOTIONS[tag], tag
                elif tag in PAUSES:
                    segs.append({"pause": PAUSES[tag], "speaker": cur_speaker})
                elif tag in EVENTS:
                    t, r, p, b, a = EVENTS[tag]
                    segs.append({"pause": b, "speaker": cur_speaker})
                    segs.append({"text": t, "emo": cur_name, "event": tag,
                                 "rate": cur_emotion[0] + r,
                                 "pitch": cur_emotion[1] + p,
                                 "speaker": cur_speaker})
                    segs.append({"pause": a, "speaker": cur_speaker})
                # tag la -> bo qua em ai
            elif m.group(2):                               # *nhan manh*
                segs.append({"pause": 120, "speaker": cur_speaker})
                add_text(m.group(2)[1:-1], extra_rate=-15, extra_pitch=8, emph=True)
                segs.append({"pause": 120, "speaker": cur_speaker})
            else:                                          # ... / …
                segs.append({"pause": 450, "speaker": cur_speaker})
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
}
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


def _fast_prompt(sents, target):
    from ..config import TRANSLATE_LANGS
    want = (" Sau do dich cau sang %s? KHONG — chi gan the, khong dich." %
            TRANSLATE_LANGS.get(target, target)) if False else ""
    numbered = "\n".join("%d|%s" % (i + 1, x) for i, x in enumerate(sents))
    return (
        "Bạn là đạo diễn lồng tiếng. Dưới đây là các câu đánh số. "
        "Với câu cần biểu cảm, trả về MỘT dòng dạng: số|các thẻ cách nhau bằng khoảng trắng|từ cần nhấn (bỏ trống nếu không).\n"
        "Thẻ tông giọng (đặt trước câu): vui, buồn, hào hứng, nghiêm túc, kể chuyện, thì thầm, "
        "dịu dàng, giận dữ, sợ hãi, ngạc nhiên, nũng nịu, mệt mỏi, bí ẩn, gấp gáp, lạnh lùng, tự hào, châm biếm.\n"
        "Thẻ sự kiện (chèn sau câu): cười, cười nhẹ, khóc, wow, thở dài, hắng giọng, suỵt, nghỉ, nghỉ dài.\n"
        "KHÔNG viết lại câu, không giải thích. Khoảng 2 trong 3 câu nên có thẻ, chọn đa dạng theo nội dung.\n"
        "Ví dụ dòng trả về: 3|bí ẩn nghỉ|chiếc ghe ma\n\nCÂU:\n" + numbered)


def _annotate_chunk_fast(chunk, translate):
    """AI chi tra BANG GAN THE (it token ~2x nhanh hon), may rap vao van ban goc
    -> noi dung nguoi dung duoc giu nguyen 100%% may moc.
    Goi theo LO 12 cau — prompt ngan, model nho (Ollama) cung theo kip."""
    sents = [x.strip() for x in re.split(r"(?<=[\.\!\?…])\s+", chunk) if x.strip()]
    if not sents:
        return chunk
    plan = {}
    LO = 12
    for base in range(0, len(sents), LO):
        batch = sents[base:base + LO]
        raw = translate._llm_call(_fast_prompt(batch, None), temperature=0.5)
        for ln in raw.splitlines():
            m = re.match(r"^\s*(\d+)\s*\|([^|]*)(?:\|(.*))?$", ln.strip())
            if not m:
                continue
            n = int(m.group(1))
            if not (1 <= n <= len(batch)):
                continue
            tags = []
            for t in re.split(r"[,\s]+", m.group(2).strip()):
                t = _TAG_ALIAS.get(_norm_tag(t), _norm_tag(t)) if t else ""
                if t in _TAG_DISPLAY:
                    tags.append(t)
            emph = (m.group(3) or "").strip()
            if tags or emph:
                plan[base + n] = (tags[:2], emph)
    if len(plan) < max(1, len(sents) // 10):      # AI tra ve qua it -> coi nhu fail
        raise ValueError("AI gan the qua it")
    out = []
    for i, sen in enumerate(sents):
        tags, emph = plan.get(i + 1, ([], ""))
        tones = [t for t in tags if t in _TONE_SET]
        events = [t for t in tags if t not in _TONE_SET]
        if emph and emph in sen and "*" not in sen:
            sen = sen.replace(emph, "*%s*" % emph, 1)
        piece = " ".join("[%s]" % _TAG_DISPLAY[t] for t in tones)
        piece = (piece + " " if piece else "") + sen
        if events:
            piece += " " + " ".join("[%s]" % _TAG_DISPLAY[t] for t in events)
        out.append(piece)
    return " ".join(out)


def _annotate_prompt(chunk, target, strict):
    from ..config import TRANSLATE_LANGS
    want = ("Dịch sang %s rồi " % TRANSLATE_LANGS.get(target, target)) if target else ""
    extra = ("\nLẦN TRƯỚC BẠN LÀM SAI: đã thay đổi nội dung. "
             "Lần này tuyệt đối giữ nguyên từng chữ, chỉ chèn thẻ.") if strict else ""
    return (
        "Bạn là đạo diễn lồng tiếng chuyên nghiệp. %schèn thẻ biểu cảm vào văn bản.\n"
        "CHỈ được dùng đúng các thẻ sau, KHÔNG tự chế thẻ khác:\n"
        "- [vui] [buồn] [hào hứng] [nghiêm túc] [kể chuyện] [thì thầm] [dịu dàng] "
        "[giận dữ] [sợ hãi] [ngạc nhiên] [nũng nịu] [mệt mỏi] [bí ẩn] [gấp gáp] "
        "[lạnh lùng] [tự hào] [châm biếm] [bình thường]: đặt TRƯỚC câu cần đổi tông\n"
        "- *từ nhấn* (1-3 từ quan trọng)\n"
        "- [nghỉ] [nghỉ dài]: khoảng lặng\n"
        "- [ừm] [à]: ngập ngừng (rất hiếm)\n"
        "- [cười] [cười nhẹ] [thở dài] [khóc] [wow] [hắng giọng] [suỵt]: khi thật hợp cảnh\n"
        "LUẬT: giữ nguyên 100%% từ ngữ%s, chỉ CHÈN thẻ. Gắn thẻ HÀO PHÓNG như đạo diễn "
        "thực thụ: hầu như câu nào cũng có thẻ tông giọng hoặc *nhấn*, thêm [cười nhẹ] "
        "[thở dài] [nghỉ] bất cứ khi nào hợp cảnh. "
        "Trả về DUY NHẤT văn bản đã gắn thẻ — không lời dẫn, không markdown.%s\n"
        "Ví dụ: 'Hôm nay trời đẹp quá. Để mình kể chuyện này nhé.' ->\n"
        "'[vui] Hôm nay trời *đẹp quá*. [kể chuyện] Để mình kể chuyện này nhé. [cười nhẹ]'\n\n"
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
    return _heuristic_annotate(plain), note


def auto_annotate(text, target=None, progress=None):
    """AI 'bien kich': (dich neu can roi) gan the bieu cam tu nhien.

    Chong loi nhieu tang: chia khuc van ban dai, khu the AI tu che, tu thu lai
    khi AI doi noi dung, va het quota/mat mang thi tu roi xuong ban bien kich
    nhanh khong AI (kem ghi chu) — KHONG bao gio chet giua chung.
    -> (van_ban_gan_the, ghi_chu)  — ghi_chu rong = AI chay ngon.
    """
    from . import translate
    if not translate.has_llm():
        return _fallback_annotate(
            text, target,
            "Chưa có AI nên dùng bản biên kịch nhanh — thêm key free Gemini "
            "(aistudio.google.com) hoặc Groq (console.groq.com), hoặc cài Ollama "
            "(ollama.com) để AI gắn thẻ hay hơn nhiều.")
    out = []
    chunks = _split_paras(text, limit=4000)
    if progress:
        progress(0, len(chunks))
    for _ci, chunk in enumerate(chunks):
        try:
            if not target:                     # duong TOC HANH: chi gan the, khong viet lai
                try:
                    got = _sanitize_script(_annotate_chunk_fast(chunk, translate))
                    out.append(got)
                    if progress:
                        progress(_ci + 1, len(chunks))
                    continue
                except ValueError:
                    pass                       # AI tra kem -> thu duong day du ben duoi
            got = _sanitize_script(
                translate._llm_call(_annotate_prompt(chunk, target, False), temperature=0.6))
            # AI lam mat/che them noi dung? (chi so duoc khi khong dich)
            if not target:
                ratio = _core_len(got) / max(1, _core_len(chunk))
                if ratio < 0.6 or ratio > 1.7:
                    got = _sanitize_script(
                        translate._llm_call(_annotate_prompt(chunk, target, True),
                                            temperature=0.3))
                    ratio = _core_len(got) / max(1, _core_len(chunk))
                    if ratio < 0.6 or ratio > 1.7:
                        raise ValueError("AI đổi nội dung sau 2 lần thử")
            out.append(got)
            if progress:
                progress(_ci + 1, len(chunks))
        except Exception:
            fb, note = _fallback_annotate(
                "\n\n".join(chunks[_ci:]), target,
                "AI đang nghẽn hoặc hết quota nên một phần văn bản dùng bản biên kịch nhanh — "
                "thêm key Groq free (console.groq.com) hoặc cài Ollama (ollama.com) "
                "là không bao giờ hết quota nữa.")
            out.append(fb)
            return "\n\n".join(out).strip(), note
    return "\n\n".join(out).strip(), ""
