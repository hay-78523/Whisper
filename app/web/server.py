# -*- coding: utf-8 -*-
"""Tang web: routing HTTP + serve trang tinh. Khong chua business logic.

Phan quyen: moi trang/API deu can dang nhap (tru /login va /static).
    user   dung trang chinh; admin  them /admin va /api/admin/*.

Luong chinh 2 buoc:
    POST /api/prepare?filename=&model=&language=&target=   (body = file tho)
         -> job: phien am -> cat doan giu timestamp -> dich tung doan
    POST /api/render {prepare_job, texts?, voice, rate}
         -> job: doc tung doan -> dat dung thoi diem -> ghep video (neu la video)
    GET  /api/job?id=N   tien do/ket qua     GET /api/audio|/api/video?id=N  tai ket qua

API chung:
    GET  /login              trang dang nhap    POST /api/login    {username,password}
    POST /api/logout         dang xuat          GET  /api/me       {user, role}

API quan tri:
    GET  /api/admin/status   phien ban, uptime, model, thong ke job
    GET  /api/admin/jobs     danh sach job      POST /api/admin/job_delete {id}
    POST /api/admin/preload  {model}
    GET  /api/admin/users    danh sach tai khoan
    POST /api/admin/user_add {username,password,role}
    POST /api/admin/user_delete {username}
    POST /api/admin/user_password {username,password}
"""

import argparse
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import VERSION, config
from ..core import clone, engine, users
from ..core.jobs import JobManager

STATIC = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "whisper_session"

jobs = JobManager()
_started = time.time()
_sessions = {}  # token -> {"user", "role", "exp"}


def _new_session(username, role):
    token = secrets.token_hex(32)
    _sessions[token] = {"user": username, "role": role,
                        "exp": time.time() + config.ADMIN_SESSION_HOURS * 3600}
    for t, s in list(_sessions.items()):  # don phien het han
        if s["exp"] < time.time():
            _sessions.pop(t, None)
    return token


class Handler(BaseHTTPRequestHandler):
    server_version = "whisperstt/" + VERSION

    # ---------- tien ich ----------

    def _send(self, code, data, ctype="application/json; charset=utf-8", extra_headers=None):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _page(self, name):
        try:
            self._send(200, (STATIC / name).read_bytes(), "text/html; charset=utf-8")
        except OSError:
            self._send(500, {"error": "Thieu file %s trong app/web/static" % name})

    def _json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _job_or_404(self, query):
        job_id = (parse_qs(query).get("id") or [""])[0]
        job = jobs.get(job_id)
        if not job:
            self._send(404, {"error": "khong co job %s" % job_id})
        return job

    def log_message(self, fmt, *args):
        sys.stderr.write("[web] %s\n" % (fmt % args))

    # ---------- phien dang nhap ----------

    def _session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        item = cookie.get(SESSION_COOKIE)
        if not item:
            return None
        s = _sessions.get(item.value)
        return s if s and s["exp"] > time.time() else None

    _PREVIEW_TEXTS = {
        "vi": "Xin chào! Mình là giọng đọc mới của bạn đây. Cùng tạo nội dung thật hay nha!",
        "en": (
            "Imagine waking up one morning, and everything you ever dreamed of... "
            "has finally come true. The sun is shining, the birds are singing, "
            "and you realize — this is YOUR moment! "
            "But wait... there's a twist. Nothing is ever that simple, is it? "
            "Life throws challenges at us every single day. "
            "And honestly? That's what makes the journey so incredibly beautiful. "
            "So hold on tight, because this story... is just getting started."
        ),
        "ja": "こんにちは！あなたの新しいナレーターです。よろしくお願いします！",
        "ko": "안녕하세요! 새로운 내레이터 목소리예요. 잘 부탁드려요!",
        "zh": "你好！我是你的新配音声音，一起做出精彩的内容吧！",
        "fr": "Bonjour ! Je suis votre nouvelle voix de narration. Créons ensemble !",
        "de": "Hallo! Ich bin deine neue Erzählerstimme. Los geht's!",
        "es": "¡Hola! Soy tu nueva voz de narración. ¡Vamos a crear algo increíble!",
    }

    def _get_voice_preview(self, query):
        """Nghe thu giong: tao 1 cau chao ngan, cache ra dia — moi giong 1 lan."""
        import hashlib
        import re as _re
        from urllib.parse import parse_qs
        from ..core import tts
        from ..core.vieneu_engine import DATA
        qs = parse_qs(query)
        vid = qs.get("voice", [""])[0]
        if not vid:
            self._send(400, {"error": "Thiếu voice"})
            return
            
        if "|c11:" in vid:
            vid = vid.split("|c11:")[0]

        allowed = {v for _, vs in config.VOICE_CATALOG for v, _n, _s in vs}
        if vid not in allowed and not vid.startswith(("c:", "vieneu:@", "eleven:", "persona:", "edge:")):
            self._send(404, {"error": "Giọng không tồn tại."})
            return
        
        pdir = DATA / "previews"
        pdir.mkdir(parents=True, exist_ok=True)
        cache_key = hashlib.md5(vid.encode()).hexdigest()
        # Check for cached preview (either .mp3 or .wav)
        f_mp3 = pdir / (cache_key + ".mp3")
        f_wav = pdir / (cache_key + ".wav")
        if f_mp3.exists():
            self._send(200, f_mp3.read_bytes(), "audio/mpeg")
            return
        if f_wav.exists():
            self._send(200, f_wav.read_bytes(), "audio/wav")
            return
        if not f_mp3.exists() and not f_wav.exists():
            m = _re.match(r"edge:([a-z]{2})-", vid)
            lang = m.group(1) if m else "vi"
            
            if "Multilingual" in vid or "-US-" in vid or "-GB-" in vid:
                lang = "en"
            
            if vid.startswith("persona:"):
                _p = (getattr(config, "PERSONA_VOICES", {}) or {}).get(vid.split(":", 1)[1], {})
                lang = _p.get("lang", "vi")
            elif vid.startswith("c:"):
                clone_name = vid.split(":", 1)[1]
                test_mp3 = DATA / "voice_profiles" / clone_name / "test.mp3"
                if test_mp3.exists():
                    self._send(200, test_mp3.read_bytes(), "audio/mpeg")
                    return
                lang = "en"

            text = self._PREVIEW_TEXTS.get(lang, self._PREVIEW_TEXTS["vi"])
            data, mime, ext = tts.synth(text, vid, "+0%")
            f = pdir / (cache_key + ext)
            f.write_bytes(data)
            self._send(200, data, mime)
            return

    def _require(self, role=None):
        """Tra ve session neu du quyen; nguoc lai gui loi va tra ve None."""
        s = self._session()
        if not s:
            self._send(401, {"error": "Chưa đăng nhập."})
            return None
        if role and s["role"] != role:
            self._send(403, {"error": "Cần quyền %s." % role})
            return None
        return s

    # ---------- routes ----------

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path

        # tai nguyen tinh (khong can dang nhap — trang login cung dung)
        if route.startswith("/static/"):
            fp = (STATIC / route[len("/static/"):]).resolve()
            if (fp.suffix in (".css", ".js", ".jpg", ".png", ".jpeg", ".svg") and fp.is_file()
                    and str(fp).startswith(str(STATIC))):
                if fp.suffix == ".css": ctype = "text/css"
                elif fp.suffix == ".js": ctype = "application/javascript"
                elif fp.suffix in (".jpg", ".jpeg"): ctype = "image/jpeg"
                elif fp.suffix == ".png": ctype = "image/png"
                elif fp.suffix == ".svg": ctype = "image/svg+xml"
                return self._send(200, fp.read_bytes(), ctype + "; charset=utf-8" if fp.suffix in (".css", ".js", ".svg") else ctype)
            return self._send(404, {"error": "not found"})

        # trang
        if route in ("/", "/index.html"):
            return self._page("index.html") if self._session() else self._redirect("/login")
        if route in ("/login", "/admin/login"):
            return self._redirect("/") if self._session() else self._page("login.html")
        if route == "/admin":
            s = self._session()
            if not s:
                return self._redirect("/login")
            return self._page("admin.html") if s["role"] == "admin" else self._redirect("/")

        # API chung (can dang nhap)
        if route == "/api/me":
            s = self._require()
            if s:
                self._send(200, {"user": s["user"], "role": s["role"]})
        elif route == "/api/voices":
            if self._require():
                from ..core import translate as tr
                from ..core import vieneu_engine as vn
                vn_ok = vn.available()
                gem_ok = tr.has_gemini()

                def badge(vid):
                    if vid.startswith("vieneu:"):
                        return "Local ∞"
                    if vid.startswith("gemini:"):
                        return "AI Studio"
                    if vid.startswith("c:"):
                        return "Clone"
                    return "Online"

                groups = []
                if clone.available():
                    clone_voices = []
                    for p in clone.list_profiles():
                        # Giong clone goc
                        clone_voices.append({"id": "c:" + p, "label": p + " — giọng clone",
                                           "name": p, "style": "giọng nhân bản", "engine": "Clone"})
                    
                    groups.append({"label": "Giọng của bạn · nhân bản", "voices": clone_voices})
                if vn_ok and vn.list_custom():
                    groups.append({"label": "Giọng đúc riêng",
                                   "voices": [{"id": v, "label": d,
                                               "name": d.split(" — ")[0], "style": "đúc riêng",
                                               "engine": "Local ∞"}
                                              for v, d in vn.list_custom()]})
                # gom don gian THEO NGON NGU: Viet / English / khac
                pv = getattr(config, "PERSONA_VOICES", {})

                def prow(order):
                    return [{"id": "persona:" + k,
                             "label": "%s — %s" % (pv[k]["name"], pv[k]["style"]),
                             "name": pv[k]["name"], "style": pv[k]["style"],
                             "engine": "Nhân vật"}
                            for k in order if k in pv]

                def rows(vs):
                    out = []
                    for vid, name, style in vs:
                        if vid.startswith("vieneu:") and not vn_ok:
                            continue
                        if vid.startswith("gemini:") and not gem_ok:
                            continue
                        out.append({"id": vid, "label": "%s — %s" % (name, style),
                                    "name": name, "style": style, "engine": badge(vid)})
                    return out

                cat = dict(config.VOICE_CATALOG)
                groups.append({"label": "🇻🇳 Tiếng Việt",
                               "voices": prow(getattr(config, "PERSONA_ORDER", []))
                               + rows(cat.get("Siêu tự nhiên · AI Studio (online)", []))
                               + rows(cat.get("🇻🇳 Việt Nam", []))})
                groups.append({"label": "🇺🇸 Mỹ",
                               "voices": prow(getattr(config, "PERSONA_ORDER_EN", []))
                               + rows(cat.get("🇺🇸 Mỹ", []))})
                groups.append({"label": "🇬🇧 Anh Quốc",
                               "voices": rows(cat.get("🇬🇧 Anh Quốc", []))})
                groups.append({"label": "🌍 Ngôn ngữ khác",
                               "voices": rows(cat.get("🌍 Ngôn ngữ khác", []))})
                from ..core import tts as _tts
                ev = _tts.eleven_voices()
                if ev:
                    groups.append({"label": "ElevenLabs · đỉnh thế giới (online)",
                                   "voices": [{"id": vid, "label": "%s — %s" % (n, st),
                                               "name": n, "style": st,
                                               "engine": "ElevenLabs"}
                                              for vid, n, st in ev]})
                groups = [g for g in groups if g["voices"]]
                flat = [x for g in groups for x in g["voices"]]
                self._send(200, {
                    "groups": groups,
                    "voices": flat,
                    "languages": [{"code": c, "label": l}
                                  for c, l in config.TRANSLATE_LANGS.items()],
                    "lang_voice": config.LANG_DEFAULT_VOICE,
                })
        elif route == "/api/job":
            if self._require():
                job = self._job_or_404(parsed.query)
                if job:
                    body = {k: job[k] for k in
                            ("state", "phase", "done", "total", "phase_started", "error")}
                    if job["state"] == "done":
                        body["result"] = job["result"]
                    self._send(200, body)
        elif route == "/api/voice_preview":
            if self._require():
                self._get_voice_preview(parsed.query)
        elif route == "/api/audio":
            if self._require():
                job = self._job_or_404(parsed.query)
                if job:
                    if job.get("audio"):
                        self._send(200, job["audio"], job.get("mime") or "audio/mpeg")
                    else:
                        self._send(404, {"error": "job nay khong co audio"})
        elif route == "/api/video":
            if self._require():
                job = self._job_or_404(parsed.query)
                if job:
                    path = job.get("video_path")
                    if path and os.path.isfile(path):
                        try:
                            size = os.path.getsize(path)
                            self.send_response(200)
                            self.send_header("Content-Type", "video/mp4")
                            self.send_header("Content-Length", str(size))
                            self.send_header("Cache-Control", "no-store")
                            self.end_headers()
                            with open(path, "rb") as f:
                                while True:
                                    chunk = f.read(1 << 20)
                                    if not chunk:
                                        break
                                    self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError):
                            pass  # nguoi dung dong tab giua chung — binh thuong
                    else:
                        self._send(404, {"error": "job nay khong co video"})

        # API quan tri (can admin)
        elif route == "/api/admin/status":
            if self._require("admin"):
                self._send(200, {
                    "version": VERSION,
                    "uptime": round(time.time() - _started),
                    "default_model": config.DEFAULT_MODEL,
                    "models": [{"name": m,
                                "loaded": m in engine.loaded_models(),
                                "downloaded": m in engine.downloaded_models()}
                               for m in config.MODELS],
                    "jobs": jobs.summary(),
                })
        elif route == "/api/admin/jobs":
            if self._require("admin"):
                self._send(200, {"jobs": jobs.list()})
        elif route == "/api/admin/users":
            if self._require("admin"):
                self._send(200, {"users": users.list_users()})
        elif route == "/api/admin/clones":
            if self._require("admin"):
                self._send(200, {"available": clone.available(),
                                 "profiles": clone.list_profiles() if clone.available() else []})
        elif route == "/api/clone_test_audio":
            qs = parse_qs(parsed.query)
            name = qs.get("name", [""])[0]
            p = clone.CLONE_DIR / name / "test.mp3"
            if p.exists():
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(p.stat().st_size))
                self.end_headers()
                self.wfile.write(p.read_bytes())
            else:
                self._send(404, {"error": "not found"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/login":
                return self._post_login()
            if route == "/api/logout":
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                item = cookie.get(SESSION_COOKIE)
                if item:
                    _sessions.pop(item.value, None)
                return self._send(200, {"ok": True}, extra_headers=[
                    ("Set-Cookie", "%s=; Path=/; Max-Age=0" % SESSION_COOKIE)])

            # tu day tro di: can dang nhap
            if route == "/api/prepare":
                if self._require():
                    self._post_prepare()
            elif route == "/api/render":
                if self._require():
                    self._post_render()
            elif route == "/api/text_dub":
                if self._require():
                    self._post_text_dub()
            elif route == "/api/annotate":
                if self._require():
                    p = self._json_body()
                    text = (p.get("text") or "").strip()
                    if not text:
                        raise ValueError("Thiếu văn bản.")
                    if len(text) > 200000:
                        raise ValueError("Văn bản quá dài cho AI biên kịch (>200k ký tự).")
                    job = jobs.start_annotate(
                        text, (p.get("target") or "").strip() or None)
                    self._send(200, {"ok": True, "job": job["id"]})
            elif route == "/api/admin/preload":
                if self._require("admin"):
                    self._post_preload()
            elif route == "/api/admin/job_delete":
                if self._require("admin"):
                    ok = jobs.delete(str(self._json_body().get("id", "")))
                    self._send(200 if ok else 404, {"ok": ok})
            elif route == "/api/admin/user_add":
                if self._require("admin"):
                    p = self._json_body()
                    users.add_user(p.get("username"), p.get("password"), p.get("role", "user"))
                    self._send(200, {"ok": True})
            elif route == "/api/admin/user_delete":
                s = self._require("admin")
                if s:
                    users.delete_user(str(self._json_body().get("username", "")), s["user"])
                    self._send(200, {"ok": True})
            elif route == "/api/admin/user_password":
                if self._require("admin"):
                    p = self._json_body()
                    users.set_password(str(p.get("username", "")), p.get("password"))
                    self._send(200, {"ok": True})
            elif route == "/api/admin/clone_add":
                if self._require("admin"):
                    qs = parse_qs(urlparse(self.path).query)
                    name = (qs.get("name") or [""])[0]
                    ref_text = (qs.get("ref_text") or [""])[0]
                    tmp, _ = self._read_body_to_temp("ref_sample")
                    try:
                        audio = Path(tmp).read_bytes()
                    finally:
                        os.unlink(tmp)
                    clone.add_profile(name, audio, ref_text)
                    self._send(200, {"ok": True})
            elif route == "/api/admin/clone_delete":
                if self._require("admin"):
                    clone.delete_profile(str(self._json_body().get("name", "")))
                    self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": "Loi khong mong doi: %s" % e})

    # ---------- handlers ----------

    def _post_login(self):
        payload = self._json_body()
        username = (payload.get("username") or "").strip()
        role = users.verify(username, payload.get("password") or "")
        if not role:
            time.sleep(1)  # can do mat khau
            return self._send(401, {"error": "Sai tên đăng nhập hoặc mật khẩu."})
        token = _new_session(username, role)
        sys.stderr.write("[web] dang nhap: %s (%s)\n" % (username, role))
        self._send(200, {"ok": True, "user": username, "role": role}, extra_headers=[
            ("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
             % (SESSION_COOKIE, token, config.ADMIN_SESSION_HOURS * 3600))])

    def _read_body_to_temp(self, filename):
        """Doc body tho vao file tam (khong multipart — module cgi da bi xoa o Py3.13)."""
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Thiếu file.")
        if length > config.MAX_UPLOAD_MB * 1_000_000:
            raise ValueError("File quá lớn (>%d MB)." % config.MAX_UPLOAD_MB)
        fd, tmp = tempfile.mkstemp(suffix="_" + filename)
        remaining = length
        with os.fdopen(fd, "wb") as f:
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        if remaining > 0:
            os.unlink(tmp)
            raise ValueError("Upload bị ngắt giữa chừng — thử lại.")
        return tmp, length

    def _post_prepare(self):
        qs = parse_qs(urlparse(self.path).query)

        def q(key, default=""):
            return (qs.get(key) or [default])[0].strip()

        filename = Path(q("filename") or "media").name
        lang = q("language")
        language = None if lang in ("", "auto") else lang
        model_name = q("model") or config.DEFAULT_MODEL
        if model_name not in config.MODELS:
            raise ValueError("Model không hợp lệ.")
        target = q("target")
        if target and target not in config.TRANSLATE_LANGS:
            raise ValueError("Ngôn ngữ dịch không hợp lệ.")

        tmp, length = self._read_body_to_temp(filename)
        job = jobs.start_prepare(tmp, filename, model_name, language, target)
        sys.stderr.write("[web] job %s: prepare %s (%.1f MB, model %s, dich=%s)\n"
                         % (job["id"], filename, length / 1e6, model_name, target or "khong"))
        self._send(200, {"job": job["id"]})

    def _post_render(self):
        payload = self._json_body()
        prepare_job = str(payload.get("prepare_job") or "")
        texts = payload.get("texts")  # None = dung ban dich goc chua sua
        if texts is not None and not isinstance(texts, list):
            raise ValueError("texts phải là danh sách dòng.")
        voice = payload.get("voice") or config.DEFAULT_VOICE
        if "|c11:" in voice:
            voice = voice.split("|c11:")[0]
        rate = payload.get("rate") or "+0%"

        job = jobs.start_render(prepare_job, texts, voice, rate)
        sys.stderr.write("[web] job %s: render tu prepare %s, giong %s\n"
                         % (job["id"], prepare_job, voice))
        self._send(200, {"job": job["id"]})

    def _post_text_dub(self):
        payload = self._json_body()
        text = (payload.get("text") or "").strip()
        if not text:
            raise ValueError("Thiếu văn bản.")
        if len(text) > config.MAX_TTS_CHARS:
            raise ValueError("Văn bản quá dài (>%dk ký tự)." % (config.MAX_TTS_CHARS // 1000))
        voice = payload.get("voice") or config.DEFAULT_VOICE
        if "|c11:" in voice:
            voice = voice.split("|c11:")[0]
        rate = payload.get("rate") or "+0%"
        target = (payload.get("target") or "").strip() or None
        if target and target not in config.TRANSLATE_LANGS:
            raise ValueError("Ngôn ngữ dịch không hợp lệ.")

        job = jobs.start_text_dub(text, voice, rate, target)
        sys.stderr.write("[web] job %s: text_dub %d ky tu, dich=%s, giong %s\n"
                         % (job["id"], len(text), target or "khong", voice))
        self._send(200, {"job": job["id"]})

    def _post_preload(self):
        model_name = (self._json_body().get("model") or "").strip()
        if model_name not in config.MODELS:
            raise ValueError("Model không hợp lệ.")
        job = jobs.start_preload(model_name)
        self._send(200, {"job": job["id"]})


def main():
    ap = argparse.ArgumentParser(description="Web UI cho whisper_stt (mien phi, chay local)")
    ap.add_argument("--host", default=config.DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    ap.add_argument("--preload", default=None, choices=config.MODELS,
                    help="nap san model nay ngay khi khoi dong")
    ap.add_argument("--open", action="store_true", help="tu mo trinh duyet sau khi khoi dong")
    args = ap.parse_args()

    if users.ensure_default():
        print("Da tao tai khoan mac dinh: admin / admin123 — hay doi mat khau ngay trong /admin!")
    if args.preload:
        engine.get_model(args.preload)

    import socket
    port = args.port
    while port < args.port + 10:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((args.host, port)) != 0:
                break
        port += 1

    httpd = ThreadingHTTPServer((args.host, port), Handler)
    if args.open:
        threading.Timer(1.0, webbrowser.open,
                        args=("http://%s:%d" % (args.host, port),)).start()
    print("Whisper STT v%s dang chay: http://%s:%d  (quan tri: /admin)"
          % (VERSION, args.host, port))
    print("Moi nguoi dung can dang nhap. Ctrl+C de tat.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDa tat server.")


if __name__ == "__main__":
    main()
