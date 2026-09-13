# -*- coding: utf-8 -*-
"""Dich van ban — dong co AI co CHUOI DU PHONG + Google:

  1. AI (tuy chon): Gemini (xoay het cac key) -> Groq (key free, han rat rong)
     -> Ollama (AI chay local, vo han). Nguon nao nghen/het quota tu nhay
     nguon ke — dich kieu long tieng chuyen nghiep.
  2. Google Translate (mac dinh): mien phi khong can key.

Moi loi cua dong co AI deu tu roi ve Google — pipeline khong bao gio gay.
Truoc khi dich, transcript duoc luoc bot tu dem van noi (um, you know, like...).
"""

import os
import re
import time

from .. import config
from ..config import TRANSLATE_CHUNK_CHARS, TRANSLATE_LANGS
from .tts import split_text

LAST_ENGINE = "Google"   # dong co da dung o lan dich gan nhat (de hien tren UI)

_FILLERS_RE = re.compile(
    r"\b(u+m+|u+h+|erm+|hmm+)\b[,.]?\s*"
    r"|\byou know,?\s+"
    r"|\bI mean,?\s+"
    r"|,\s*like,\s*",
    re.IGNORECASE)


def clean_spoken(text):
    """Luoc tu dem van noi truoc khi dich (khong dung cho transcript goc)."""
    out = _FILLERS_RE.sub(" ", text or "")
    return re.sub(r"\s{2,}", " ", out).strip()


# ---------------- dong co AI (Gemini) ----------------

_key_idx = [0]


def _gemini_keys():
    """Danh sach key (env + config don + config danh sach), bo trung, giu thu tu."""
    raw = ([os.environ.get("GEMINI_API_KEY", ""), config.GEMINI_API_KEY]
           + (getattr(config, "GEMINI_API_KEYS", "") or "").split(","))
    out = []
    for k in raw:
        k = (k or "").strip()
        if k and k not in out:
            out.append(k)
    return out


def _gemini_key():
    ks = _gemini_keys()
    return ks[_key_idx[0] % len(ks)] if ks else ""


def rotate_key():
    """Het quota key hien tai -> nhay sang key ke. True neu con key khac de thu."""
    if len(_gemini_keys()) > 1:
        _key_idx[0] += 1
        return True
    return False


_groq_key_idx = [0]


def _groq_keys():
    raw = ([os.environ.get("GROQ_API_KEY", ""), getattr(config, "GROQ_API_KEY", "")]
           + (getattr(config, "GROQ_API_KEYS", "") or "").split(","))
    out = []
    for k in raw:
        k = (k or "").strip()
        if k and k not in out:
            out.append(k)
    return out


def _groq_key():
    ks = _groq_keys()
    return ks[_groq_key_idx[0] % len(ks)] if ks else ""


def rotate_groq_key():
    if len(_groq_keys()) > 1:
        _groq_key_idx[0] += 1
        return True
    return False


_ollama_cache = [0.0, None]      # (luc kiem tra, ten model) — cache 60s


def _ollama_model():
    """Model Ollama dang chay local (None neu Ollama tat/chua cai)."""
    now = time.time()
    if now - _ollama_cache[0] < 60:
        return _ollama_cache[1]
    model = None
    try:
        import requests
        r = requests.get(config.OLLAMA_URL + "/api/tags", timeout=1.5)
        tags = [m["name"] for m in r.json().get("models", [])]
        model = getattr(config, "OLLAMA_MODEL", "") or (tags[0] if tags else None)
    except Exception:
        model = None
    _ollama_cache[:] = [now, model]
    return model


def has_gemini():
    """Rieng cho tinh nang CHI Gemini lam duoc (giong doc Gemini TTS)."""
    return bool(_gemini_key())


def has_llm():
    return bool(_gemini_key() or _groq_key() or _ollama_model())


def _strip_fences(s):
    return re.sub(r"^```[a-z]*\s*|\s*```$", "", (s or "").strip())


def _gemini_llm(prompt, temperature):
    """Goi Gemini; loi TAM THOI (429/5xx/mang) retry 3 lan co gian cach,
    loi cau hinh (key/model sai) nem ngay."""
    import requests
    last = None
    for attempt in range(3):
        try:
            r = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
                % config.GEMINI_MODEL,
                params={"key": _gemini_key()},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": temperature}},
                timeout=90)
            if r.status_code in (429, 500, 502, 503):
                last = RuntimeError("Gemini HTTP %d" % r.status_code)
                if r.status_code == 429 and rotate_key():
                    continue               # thu ngay key khac, khong cho
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return _strip_fences(r.json()["candidates"][0]["content"]["parts"][0]["text"])
        except requests.exceptions.HTTPError:
            raise                      # 400/403/404 — loi cau hinh, khong retry
        except (requests.exceptions.RequestException, KeyError, IndexError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


_groq_model_cache = [None]


def _groq_pick_model():
    """Model cau hinh neu con song, khong thi tu do model tot nhat dang co
    (Groq hay thay model — tranh chet vi ten model het han)."""
    if _groq_model_cache[0]:
        return _groq_model_cache[0]
    import requests
    try:
        r = requests.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": "Bearer " + _groq_key()}, timeout=15)
        names = {m["id"] for m in r.json().get("data", [])}
    except Exception:
        names = set()
    for want in (config.GROQ_MODEL, "openai/gpt-oss-120b", "qwen/qwen3.6-27b",
                 "openai/gpt-oss-20b", "llama-3.3-70b-versatile"):
        if want in names:
            _groq_model_cache[0] = want
            return want
    return config.GROQ_MODEL


def _groq_llm(prompt, temperature):
    import requests
    last = None
    for attempt in range(4):
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": "Bearer " + _groq_key()},
            json={"model": _groq_pick_model(),
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": temperature},
            timeout=120)
        if r.status_code in (429, 500, 502, 503, 401, 403):
            last = RuntimeError("Groq HTTP %d" % r.status_code)
            if r.status_code in (429, 401, 403) and rotate_groq_key():
                continue
            time.sleep(3 * (attempt + 1))
            continue
        r.raise_for_status()
        return _strip_fences(r.json()["choices"][0]["message"]["content"])
    raise last


def _ollama_llm(prompt, temperature):
    import requests
    r = requests.post(
        config.OLLAMA_URL + "/api/chat",
        json={"model": _ollama_model(), "stream": False,
              "messages": [{"role": "user", "content": prompt}],
              "options": {"temperature": temperature,
                          "num_ctx": 8192, "num_predict": 1600}},
        timeout=600)
    r.raise_for_status()
    return _strip_fences(r.json()["message"]["content"])


LLM_PROVIDER = ""    # nguon AI da tra loi lan gan nhat (hien tren UI)


_llm_cooldown = {}       # nguon vua sap -> nghi 5 phut, khoi cho no thu lai tung call


def _llm_call(prompt, temperature=0.2):
    """CHUOI AI DU PHONG: Gemini (xoay key) -> Groq -> Ollama local.
    Nguon nao loi/thieu tu nhay nguon ke (nguon sap duoc cho 'nghi' 5 phut
    de cac lan goi sau di thang nguon song); ca chuoi fail moi nem loi."""
    global LLM_PROVIDER
    errors = []
    tried = set()
    chain = [("Gemini", _gemini_key, _gemini_llm),
             ("Groq", _groq_key, _groq_llm),
             ("Ollama", _ollama_model, _ollama_llm)]
    for skip_cooldown in (False, True):   # vong 2: chi vot lai nguon da bi bo qua vi dang nghi
        for name, avail, call in chain:
            if name in tried or not avail():
                continue
            if not skip_cooldown and time.time() < _llm_cooldown.get(name, 0):
                continue
            tried.add(name)
            try:
                out = call(prompt, temperature)
                LLM_PROVIDER = name
                _llm_cooldown.pop(name, None)
                return out
            except Exception as e:
                _llm_cooldown[name] = time.time() + 300
                errors.append("%s: %s" % (name, str(e)[:80]))
    raise RuntimeError("; ".join(errors)
                       or "Chua cau hinh AI (key Gemini/Groq hoac Ollama)")


def _llm_translate_lines(lines, target):
    """Dich 1 batch dong, giu DUNG so dong. Loi/lech dong -> nem exception."""
    lang = TRANSLATE_LANGS.get(target, target)
    numbered = "\n".join("%d|%s" % (i + 1, l) for i, l in enumerate(lines))
    prompt = (
        "Bạn là dịch giả lồng tiếng video chuyên nghiệp. Dịch từng dòng sau sang %s.\n"
        "Yêu cầu: văn nói tự nhiên như người bản xứ thuyết minh; lược từ đệm vô nghĩa; "
        "giữ nguyên thuật ngữ kỹ thuật và tên riêng; xưng hô nhất quán, thân thiện.\n"
        "Trả về ĐÚNG %d dòng, mỗi dòng theo dạng 'số|bản dịch'. Không thêm bất cứ gì khác.\n\n%s"
        % (lang, len(lines), numbered))
    out = {}
    for ln in _llm_call(prompt).splitlines():
        m = re.match(r"^\s*(\d+)\s*\|\s*(.*)$", ln)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    if set(out) != set(range(1, len(lines) + 1)):
        raise ValueError("AI tra ve lech so dong (%d/%d)" % (len(out), len(lines)))
    return [out[i + 1] for i in range(len(lines))]


# ---------------- dong co Google ----------------

def _google_one(tr, s):
    for attempt in range(3):
        try:
            return (tr.translate(s) or "").strip()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


# ---------------- API chinh ----------------

def translate_cues(texts, target="vi", progress=None):
    """Dich danh sach doan, GIU NGUYEN so doan (de khong mat timestamp).

    Uu tien AI theo batch 50 doan (co ngu canh); batch nao loi thi roi ve Google.
    """
    global LAST_ENGINE
    texts = [clean_spoken(t) or "..." for t in texts]
    n = len(texts)
    out = [None] * n
    done = [0]
    used_llm = False

    def tick(k):
        done[0] += k
        if progress:
            progress(done[0], n)

    from deep_translator import GoogleTranslator
    tr = GoogleTranslator(source="auto", target=target)

    def google_batch(idxs):
        joined = "\n".join(texts[i] for i in idxs)
        lines = [l.strip() for l in _google_one(tr, joined).split("\n")]
        if len(lines) == len(idxs):
            for k, i in enumerate(idxs):
                out[i] = lines[k]
        else:
            for i in idxs:
                out[i] = _google_one(tr, texts[i])
        tick(len(idxs))

    # chia batch: AI 50 doan/lan; Google gioi han theo ky tu
    i = 0
    while i < n:
        if has_llm():
            idxs = list(range(i, min(i + 50, n)))
            try:
                res = _llm_translate_lines([texts[k] for k in idxs], target)
                for k, ix in enumerate(idxs):
                    out[ix] = res[k]
                used_llm = True
                tick(len(idxs))
            except Exception:
                google_batch(idxs)
            i = idxs[-1] + 1
        else:
            idxs, size = [], 0
            while i < n and (not idxs or size + len(texts[i]) <= TRANSLATE_CHUNK_CHARS) and len(idxs) < 40:
                idxs.append(i)
                size += len(texts[i])
                i += 1
            google_batch(idxs)

    LAST_ENGINE = ("AI (%s)" % LLM_PROVIDER) if used_llm else "Google"
    return [x if x is not None else "" for x in out]


def translate_text(text, target="vi", progress=None, log=None):
    """Dich van ban dai (khong can giu so dong)."""
    global LAST_ENGINE
    chunks = split_text(clean_spoken(text), max_chars=TRANSLATE_CHUNK_CHARS)
    if progress:
        progress(0, len(chunks))
    from deep_translator import GoogleTranslator
    tr = GoogleTranslator(source="auto", target=target)
    used_llm = False
    out = []
    for i, c in enumerate(chunks):
        piece = None
        if has_llm():
            try:
                lang = TRANSLATE_LANGS.get(target, target)
                piece = _llm_call(
                    "Dịch đoạn sau sang %s, văn phong tự nhiên như người bản xứ, "
                    "giữ thuật ngữ/tên riêng. Chỉ trả về bản dịch:\n\n%s" % (lang, c))
                used_llm = True
            except Exception:
                piece = None
        if piece is None:
            piece = _google_one(tr, c)
        out.append(piece)
        if log:
            log("dich %d/%d xong" % (i + 1, len(chunks)))
        if progress:
            progress(i + 1, len(chunks))
    LAST_ENGINE = ("AI (%s)" % LLM_PROVIDER) if used_llm else "Google"
    return "\n".join(out).strip()
