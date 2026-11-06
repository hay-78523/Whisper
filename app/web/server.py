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

API nhan ban giong (Voice Clone — tinh nang chinh):
    GET  /api/admin/clone_status[?recheck=1]   thieu gi, loi gi, dang chay tren gi
    POST /api/admin/clone_install {force?}     tai model giong Viet (job nen, co resume)
    POST /api/admin/clone_warm                 nap san engine vao RAM
    POST /api/admin/clone_stop                 tat engine, giai phong RAM
    POST /api/admin/clone_add?name=&ref_text=  (body = file mau) tao giong
    POST /api/admin/clone_sample {name,text?}  tao ban nghe thu
    POST /api/admin/clone_delete {name}           (tu choi giong cho duyet = xoa)
    POST /api/admin/clone_approve {name}          duyet giong nhan vien gui (chi tren may chu)
    POST /api/clone_add?name=...                  may chu+admin: tao ngay; con lai: gui cho duyet
    POST /api/admin/clone_settings {quality,device,normalize,trim,lowercase}
    GET  /api/clone_test_audio?name=           nghe thu giong da nhan ban
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

# doc thu giong nhan ban: chi 1 yeu cau mot luc (model giu GPU/CPU rat nang)
_clone_preview_lock = threading.Lock()

STATIC = Path(__file__).resolve().parent / "static"


def _edition_load():
    """Giao dien cua ban dang chay: editions/<ten>/static/ phu len giao dien goc.
    "brand_from": "<ban khac>" -> dung chung logo/mau cua ban do (vd starlight_test).
    Ban khong co static/ (vd whisper) -> giu giao dien goc."""
    d, ed = config.EDITION_DIR, config.EDITION
    if not d:
        return None, {}
    src = (d.parent / ed["brand_from"]) if ed.get("brand_from") else d
    if not (src / "static" / "brand.css").is_file():
        return None, {}
    return src, ed


def _brand_html(html, name):
    """Chen CSS/JS nhan dien vao trang goc, doi tieu de tab."""
    ed_dir, ed = _edition_load()
    if not ed_dir:
        return html
    import re as _re
    title = (ed.get("titles") or {}).get(name)
    if title:
        html = _re.sub(r"<title>.*?</title>", "<title>%s</title>" % title, html, count=1, flags=_re.S)
    head = ""
    if ed.get("fonts"):
        head += '<link rel="stylesheet" href="%s">' % ed["fonts"]
    head += '<link rel="stylesheet" href="/static/edition/brand.css">'
    if (ed_dir / "static" / "favicon.png").is_file():
        head += '<link rel="icon" href="/static/edition/favicon.png">'
    html = html.replace("</head>", head + "</head>", 1)
    html = html.replace("<body", '<body data-edition="%s" data-page="%s"' % (ed.get("id", ""), name), 1)
    cfg = json.dumps({k: v for k, v in ed.items() if k not in ("titles", "fonts")}, ensure_ascii=False)
    tail = '<script>window.EDITION=%s;</script><script src="/static/edition/brand.js"></script>' % cfg.replace("</", "<\\/")
    i = html.rfind("</body>")
    return html[:i] + tail + html[i:] if i >= 0 else html + tail
SESSION_COOKIE = "whisper_session"

jobs = JobManager()
_started = time.time()
_sessions = {}  # token -> {"user", "role", "ip", "created", "last_seen", "exp"}
_personal = {}  # ban ca nhan: phien tu dang nhap tren may chu


def _new_session(username, role, ip=""):
    token = secrets.token_hex(32)
    now = time.time()
    _sessions[token] = {"user": username, "role": role, "ip": ip or "",
                        "created": now, "last_seen": now,
                        "exp": now + config.ADMIN_SESSION_HOURS * 3600}
    for t, s in list(_sessions.items()):  # don phien het han
        if s["exp"] < now:
            _sessions.pop(t, None)
    return token


def _active_sessions():
    now = time.time()
    out = []
    for t, s in list(_sessions.items()):
        if s["exp"] < now:
            _sessions.pop(t, None)
            continue
        out.append({
            "token_id": t[:10],
            "user": s["user"],
            "role": s["role"],
            "ip": s.get("ip") or "127.0.0.1",
            "created": s.get("created") or now,
            "last_seen": s.get("last_seen") or now,
        })
    return sorted(out, key=lambda x: -x["last_seen"])


def _local_host_ips():
    """Tap hop IP cua chinh may chu (loopback + IP LAN tren card mang may chu)."""
    ips = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1", "0:0:0:0:0:0:0:1"}
    try:
        import socket
        for addr in socket.gethostbyname_ex(socket.gethostname())[2]:
            if addr:
                ips.add(addr)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except Exception:
        pass
    return ips


class Handler(BaseHTTPRequestHandler):
    server_version = "whisperstt/" + VERSION

    # ---------- tien ich ----------

    def _is_host_request(self):
        """Kiem tra yeu cau HTTP co xuat phat truc tiep tu may chu (Host) hay khong."""
        host_ips = _local_host_ips()
        try:
            sock_ip = (self.connection.getsockname()[0] or "").strip()
            if sock_ip.startswith("::ffff:"):
                sock_ip = sock_ip[7:]
            if sock_ip and sock_ip not in ("0.0.0.0", "::"):
                host_ips.add(sock_ip)
        except Exception:
            pass
        peer = (self.client_address[0] if self.client_address else "").strip()
        if peer.startswith("::ffff:"):
            peer = peer[7:]
        if peer not in host_ips:
            return False
        xff = (self.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            first_ip = xff.split(",")[0].strip()
            if first_ip.startswith("::ffff:"):
                first_ip = first_ip[7:]
            if first_ip and first_ip not in host_ips:
                return False
        return True

    def _require_host_admin(self, s):
        """Chi cho phep them/xoa giong mau khi dang ngoi truc tiep tren may chu voi quyen Quan tri."""
        if not s or s.get("role") != "admin":
            self._send(403, {"error": "Chỉ tài khoản Quản trị trên máy chủ mới được phép thêm hoặc xóa giọng mẫu."})
            return False
        if not self._is_host_request():
            self._send(403, {"error": "Chức năng thêm/xóa giọng mẫu chỉ được thực hiện trực tiếp trên máy chủ (Host)."})
            return False
        return True

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
            html = (STATIC / name).read_text(encoding="utf-8")
            self._send(200, _brand_html(html, name).encode("utf-8"), "text/html; charset=utf-8")
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
        s = _sessions.get(item.value) if item else None
        if s and s["exp"] > time.time():
            s["last_seen"] = time.time()
            return s
        return self._personal_session()

    def _personal_session(self):
        """Ban ca nhan: mo tren CHINH may chu -> tu dang nhap bang tai khoan quan tri.
        Truy cap tu may khac (LAN) van phai dang nhap nhu thuong."""
        if not config.PERSONAL_MODE or not self._is_host_request():
            return None
        now = time.time()
        s = _personal.get("s")
        if not s:
            name = users.first_admin()
            if not name:
                return None
            s = {"user": name, "role": "admin", "ip": "127.0.0.1", "created": now,
                 "last_seen": now, "exp": now + 10 * 365 * 86400, "personal": True}
            _personal["s"] = s
        s["last_seen"] = now
        return s

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
        
        if vid.startswith("c:"):
            cname = clone.safe_name(vid.split(":", 1)[1])
            pth = clone.sample_path(cname)
            if not pth:
                with _clone_preview_lock:
                    pth = clone.sample_path(cname)
                    if not pth:
                        try:
                            clone.make_sample(cname)
                        except Exception as e:
                            return self._send(503, {"error": str(e)})
                        pth = clone.sample_path(cname)
            if pth:
                self._send(200, pth.read_bytes(),
                           "audio/wav" if pth.suffix == ".wav" else "audio/mpeg")
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
            sroot = STATIC
            ed_dir, _ = _edition_load()
            if route.startswith("/static/edition/") and ed_dir:
                sroot = (ed_dir / "static").resolve()
                fp = (sroot / route[len("/static/edition/"):]).resolve()
            else:
                fp = (STATIC / route[len("/static/"):]).resolve()
            if (fp.suffix in (".css", ".js", ".jpg", ".png", ".jpeg", ".svg") and fp.is_file()
                    and str(fp).startswith(str(sroot))):
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
                is_host = self._is_host_request()
                self._send(200, {
                    "user": s["user"],
                    "role": s["role"],
                    "is_host": is_host,
                    "can_add_voice": bool(is_host and s["role"] == "admin"),
                    # may tram / nhan vien: gui giong len -> cho Quan tri tren may chu duyet
                    "can_submit_voice": True,
                    "personal": bool(config.PERSONAL_MODE),
                    "auto_login": bool(s.get("personal")),
                })
        elif route == "/api/voices":
            if self._require():
                groups = []
                clone_profiles = clone.list_profiles()
                vi_voices = []
                en_voices = []
                for name in clone_profiles:
                    inf = clone.profile_info(name)
                    lang = inf.get("lang") or "en"
                    styles = inf.get("styles") or []
                    note_parts = []
                    if inf.get("from_video"):
                        note_parts.append("từ video")
                    else:
                        note_parts.append("mẫu chuẩn")
                    if inf["state"] == "processing":
                        note_parts.append("đang xử lý…")
                    elif inf["duration"]:
                        note_parts.append("%.0fs" % inf["duration"])
                    if "hype" in styles or "calm" in styles:
                        note_parts.append("đa sắc thái")
                    note = " · ".join(note_parts)
                    item = {
                        "id": "c:" + name,
                        "label": "%s — %s" % (name, note),
                        "name": name,
                        "style": note,
                        "lang": lang,
                        "styles": styles,
                        "from_video": bool(inf.get("from_video")),
                        "state": inf["state"],
                        "engine": "F5-TTS VI" if lang == "vi" else "Clone EN",
                    }
                    if lang == "vi":
                        vi_voices.append(item)
                    else:
                        en_voices.append(item)
                if vi_voices:
                    groups.append({"label": "Giọng nhân bản — Tiếng Việt",
                                   "voices": vi_voices})
                if en_voices:
                    groups.append({"label": "Giọng nhân bản — Tiếng Anh",
                                   "voices": en_voices})
                flat = [x for g in groups for x in g["voices"]]
                self._send(200, {
                    "groups": groups,
                    "voices": flat,
                    "languages": [],
                    "lang_voice": {},
                })
        elif route == "/api/genres":
            if self._require():
                from ..core import expressive
                self._send(200, {"genres": expressive.genre_list()})
        elif route == "/api/clone_profile_info":
            s = self._require()
            if s:
                qs = parse_qs(parsed.query)
                name = clone.safe_name((qs.get("name") or [""])[0])
                if (not (clone.profile_dir(name) / "ref.wav").exists()
                        or not self._can_see_voice(s, name)):
                    self._send(404, {"error": "Không tìm thấy giọng '%s'." % name})
                else:
                    self._send(200, {"profile": clone.profile_info(name)})
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
                    "ai": self._ai_status(),
                })
        elif route == "/api/admin/jobs":
            if self._require("admin"):
                self._send(200, {"jobs": jobs.list()})
        elif route == "/api/admin/gen_history":
            if self._require("admin"):
                qs = parse_qs(parsed.query)
                owner = (qs.get("owner") or [""])[0].strip() or None
                self._send(200, {"history": jobs.list_history(limit=80, owner=owner)})
        elif route == "/api/admin/gen_audio":
            if self._require("admin"):
                qs = parse_qs(parsed.query)
                hid = (qs.get("id") or [""])[0].strip()
                dl = (qs.get("download") or ["0"])[0] == "1"
                data, mime, fn = jobs.get_history_audio(hid)
                if data:
                    hdrs = [("Content-Disposition", 'attachment; filename="%s"' % fn)] if dl else None
                    self._send(200, data, mime, extra_headers=hdrs)
                else:
                    self._send(404, {"error": "Không tìm thấy file audio của bản ghi này."})
        elif route == "/api/admin/users":
            if self._require("admin"):
                self._send(200, {
                    "users": users.list_users(),
                    "quota": users.quota_info(),
                    "sessions": _active_sessions(),
                })
        elif route == "/api/admin/clones":
            if self._require("admin"):
                # giu nguyen 2 khoa cu cho tuong thich, them trang thai day du
                st = clone.status()
                self._send(200, {"available": st["ready"],
                                 "profiles": [p["name"] for p in st["profiles"]],
                                 "status": st})
        elif route == "/api/admin/clone_status":
            s = self._require("admin")
            if s:
                qs = parse_qs(parsed.query)
                recheck = (qs.get("recheck", ["0"])[0] == "1")
                st = clone.status(recheck=recheck)
                st["is_host"] = self._is_host_request()
                st["can_add_voice"] = bool(st["is_host"] and s["role"] == "admin")
                self._send(200, st)
        elif route == "/api/clone_test_audio":
            s = self._require()
            if not s:
                return
            qs = parse_qs(parsed.query)
            name = clone.safe_name(qs.get("name", [""])[0])
            p = clone.sample_path(name) if self._can_see_voice(s, name) else None
            if p:
                mime = "audio/wav" if p.suffix == ".wav" else "audio/mpeg"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(p.stat().st_size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(p.read_bytes())
            else:
                self._send(404, {"error": "Chưa có bản nghe thử — bấm \"Nghe thử\" "
                                          "để tool đọc một câu mẫu."})
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
                s = self._require()
                if s:
                    self._post_prepare(s)
            elif route == "/api/render":
                s = self._require()
                if s:
                    self._post_render(s)
            elif route == "/api/text_dub":
                s = self._require()
                if s:
                    self._post_text_dub(s)
            elif route == "/api/clone_add":
                s = self._require()
                if s:
                    host_admin = s["role"] == "admin" and self._is_host_request()
                    self._handle_clone_add(s, pending=not host_admin)
            elif route == "/api/annotate":
                if self._require():
                    p = self._json_body()
                    text = (p.get("text") or "").strip()
                    if not text:
                        raise ValueError("Thiếu văn bản.")
                    if len(text) > 200000:
                        raise ValueError("Văn bản quá dài cho AI biên kịch (>200k ký tự).")
                    job = jobs.start_annotate(
                        text, (p.get("target") or "").strip() or None,
                        genre=(p.get("genre") or "").strip() or None)
                    self._send(200, {"ok": True, "job": job["id"]})
            elif route == "/api/admin/preload":
                if self._require("admin"):
                    self._post_preload()
            elif route == "/api/admin/job_delete":
                if self._require("admin"):
                    ok = jobs.delete(str(self._json_body().get("id", "")))
                    self._send(200 if ok else 404, {"ok": ok})
            elif route == "/api/admin/gen_delete":
                if self._require("admin"):
                    ok = jobs.delete_history(str(self._json_body().get("id", "")))
                    self._send(200 if ok else 404, {"ok": ok})
            elif route == "/api/admin/session_kick":
                if self._require("admin"):
                    p = self._json_body()
                    tid = (p.get("token_id") or "").strip()
                    uname = (p.get("username") or "").strip()
                    kicked = 0
                    for t, sess in list(_sessions.items()):
                        if (tid and t.startswith(tid)) or (uname and sess.get("user") == uname):
                            _sessions.pop(t, None)
                            kicked += 1
                    self._send(200, {"ok": True, "kicked": kicked})
            elif route == "/api/admin/user_add":
                if self._require("admin"):
                    p = self._json_body()
                    users.add_user(p.get("username"), p.get("password"), p.get("role", "user"))
                    self._send(200, {"ok": True})
            elif route == "/api/admin/user_delete":
                s = self._require("admin")
                if s:
                    uname = str(self._json_body().get("username", ""))
                    users.delete_user(uname, s["user"])
                    for t, sess in list(_sessions.items()):
                        if sess.get("user") == uname:
                            _sessions.pop(t, None)
                    self._send(200, {"ok": True})
            elif route == "/api/admin/user_password":
                if self._require("admin"):
                    p = self._json_body()
                    users.set_password(str(p.get("username", "")), p.get("password"))
                    self._send(200, {"ok": True})
            elif route == "/api/admin/clone_add":
                s = self._require("admin")
                if s and self._require_host_admin(s):
                    self._handle_clone_add(s)
            elif route == "/api/admin/clone_delete":
                s = self._require("admin")
                if s and self._require_host_admin(s):
                    clone.delete_profile(str(self._json_body().get("name", "")))
                    self._send(200, {"ok": True})
            elif route == "/api/admin/clone_approve":
                s = self._require("admin")
                if s and self._require_host_admin(s):
                    name = str(self._json_body().get("name", ""))
                    info = clone.approve_profile(name, approved_by=s["user"])
                    sys.stderr.write("[web] %s duyet giong %s (gui boi %s)\n"
                                     % (s["user"], info["name"], info.get("created_by") or "?"))
                    self._send(200, {"ok": True, "profile": info})
            elif route == "/api/admin/clone_make_styles":
                s = self._require("admin")
                if s and self._require_host_admin(s):
                    p = self._json_body()
                    if p.get("all"):
                        names = [n for n in clone.list_profiles(include_all_en=True)
                                 if not clone.is_pending(n)
                                 and not ({"hype", "calm"} <= clone.available_styles(n))]
                    else:
                        names = [clone.safe_name(str(p.get("name", "")))]
                        if names[0] not in clone.list_profiles(include_all_en=True):
                            raise ValueError("Không có giọng '%s'." % names[0])
                    if not names:
                        raise ValueError("Mọi giọng đều đã có mẫu cảm xúc.")
                    job = jobs.start_clone_styles(names, force=bool(p.get("force")))
                    self._send(200, {"ok": True, "job": job["id"], "count": len(names)})
            elif route == "/api/admin/clone_install":
                if self._require("admin"):
                    force = bool(self._json_body().get("force"))
                    job = jobs.start_clone_setup(force=force)
                    self._send(200, {"ok": True, "job": job["id"]})
            elif route == "/api/admin/clone_warm":
                if self._require("admin"):
                    job = jobs.start_clone_warm()
                    self._send(200, {"ok": True, "job": job["id"]})
            elif route == "/api/admin/clone_stop":
                if self._require("admin"):
                    clone.stop_worker()
                    self._send(200, {"ok": True})
            elif route == "/api/admin/clone_sample":
                if self._require("admin"):
                    p = self._json_body()
                    name = clone.safe_name(str(p.get("name", "")))
                    if name not in clone.list_profiles(include_all_en=True):
                        raise ValueError("Không có giọng '%s'." % name)
                    job = jobs.start_clone_sample(name, (p.get("text") or "").strip() or None)
                    self._send(200, {"ok": True, "job": job["id"]})
            elif route == "/api/admin/clone_settings":
                if self._require("admin"):
                    self._send(200, {"ok": True,
                                     "settings": clone.set_settings(self._json_body()),
                                     "nfe_step": clone.nfe_step()})
            else:
                self._send(404, {"error": "not found"})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": "Loi khong mong doi: %s" % e})

    # ---------- handlers ----------

    @staticmethod
    def _ai_status():
        """Trang thai AI bien kich — loi o day khong duoc lam hong trang /admin."""
        try:
            from ..core import expressive
            return expressive.ai_status()
        except Exception as e:  # noqa: BLE001
            return {"online": False, "model": None, "last_error": str(e)}

    def _can_see_voice(self, s, name):
        """Giong cho duyet: chi Quan tri hoac chinh nguoi gui moi thay/nghe duoc."""
        if s.get("role") == "admin" or not clone.is_pending(name):
            return True
        return clone.profile_meta(name).get("created_by") == s.get("user")

    def _check_voices_usable(self, text, voice):
        """Chan doc bang giong chua duoc duyet (ke ca giong chen giua kich ban [giong:...])."""
        from ..core import expressive
        voices = [voice or ""]
        try:
            voices += [v.strip() for v in expressive._VOICE_TAG_RE.split(text or "")[1::2]]
        except Exception:
            pass
        for v in voices:
            if v.startswith("c:") and clone.is_pending(v.split(":", 1)[1].split("|", 1)[0]):
                raise ValueError("Giọng '%s' đang chờ Quản trị duyệt — chưa dùng được."
                                 % v.split(":", 1)[1])

    def _handle_clone_add(self, s, pending=False):
        qs = parse_qs(urlparse(self.path).query)
        name = (qs.get("name") or [""])[0]
        if pending:
            # gui tu may tram: khong duoc ghi de giong co san, gioi han so giong cho duyet
            sname = clone.safe_name(name)
            if clone.profile_dir(sname).exists():
                raise ValueError("Đã có giọng tên '%s' — đặt tên khác." % sname)
            if clone.pending_count(s.get("user")) >= config.MAX_PENDING_VOICES:
                raise ValueError("Bạn đã gửi %d giọng đang chờ duyệt — đợi Quản trị duyệt bớt."
                                 % config.MAX_PENDING_VOICES)
        ref_text = (qs.get("ref_text") or [""])[0]
        fname = (qs.get("filename") or [""])[0]
        lang = (qs.get("lang") or ["vi"])[0]
        trim_start = (qs.get("trim_start") or [None])[0]
        trim_end = (qs.get("trim_end") or [None])[0]
        denoise_q = (qs.get("denoise") or [""])[0]
        denoise = True if denoise_q == "1" else False if denoise_q == "0" else None
        tmp, _ = self._read_body_to_temp("ref_sample")
        try:
            audio = Path(tmp).read_bytes()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        info = clone.add_profile(
            name, audio, ref_text, filename=fname,
            trim_start=trim_start, trim_end=trim_end,
            denoise=denoise, lang=lang, created_by=s.get("user"),
            approval="pending" if pending else "approved"
        )
        if pending:
            sys.stderr.write("[web] %s gui giong %s cho duyet\n" % (s.get("user"), info["name"]))
        self._send(200, {"ok": True, "profile": info, "pending": pending})

    def _post_login(self):
        payload = self._json_body()
        username = (payload.get("username") or "").strip()
        role = users.verify(username, payload.get("password") or "")
        if not role:
            time.sleep(1)  # can do mat khau
            return self._send(401, {"error": "Sai tên đăng nhập hoặc mật khẩu."})
        ip = self.client_address[0] if self.client_address else "127.0.0.1"
        token = _new_session(username, role, ip=ip)
        sys.stderr.write("[web] dang nhap: %s (%s) tu %s\n" % (username, role, ip))
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

    def _post_prepare(self, s=None):
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
        owner = (s or {}).get("user") or "admin"
        job = jobs.start_prepare(tmp, filename, model_name, language, target, owner=owner)
        sys.stderr.write("[web] job %s (%s): prepare %s (%.1f MB, model %s, dich=%s)\n"
                         % (job["id"], owner, filename, length / 1e6, model_name, target or "khong"))
        self._send(200, {"job": job["id"]})

    def _post_render(self, s=None):
        payload = self._json_body()
        prepare_job = str(payload.get("prepare_job") or "")
        texts = payload.get("texts")  # None = dung ban dich goc chua sua
        if texts is not None and not isinstance(texts, list):
            raise ValueError("texts phải là danh sách dòng.")
        voice = payload.get("voice") or config.DEFAULT_VOICE
        if "|c11:" in voice:
            voice = voice.split("|c11:")[0]
        rate = payload.get("rate") or "+0%"
        self._check_voices_usable("", voice)

        owner = (s or {}).get("user") or "admin"
        job = jobs.start_render(prepare_job, texts, voice, rate, owner=owner)
        sys.stderr.write("[web] job %s (%s): render tu prepare %s, giong %s\n"
                         % (job["id"], owner, prepare_job, voice))
        self._send(200, {"job": job["id"]})

    def _post_text_dub(self, s=None):
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
        self._check_voices_usable(text, voice)

        from ..core import expressive
        genre = (payload.get("genre") or "").strip() or None
        if genre and genre not in expressive.GENRES:
            raise ValueError("Thể loại video không hợp lệ.")

        owner = (s or {}).get("user") or "admin"
        job = jobs.start_text_dub(text, voice, rate, target, owner=owner, genre=genre)
        sys.stderr.write("[web] job %s (%s): text_dub %d ky tu, dich=%s, giong %s\n"
                         % (job["id"], owner, len(text), target or "khong", voice))
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
    local_url = "http://127.0.0.1:%d" % port
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(local_url,)).start()
    lan_ip = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
    except Exception:
        pass
    print("===============================================================")
    if config.PERSONAL_MODE:
        print("Whisper Dubbing Studio (Ban ca nhan v%s) dang chay!" % VERSION)
        print("  - Mo tren may nay   : %s  (tu dang nhap)" % local_url)
        print("  - Cai dat           : %s/admin" % local_url)
    else:
        print("Whisper Dubbing Studio (Enterprise v%s) dang chay!" % VERSION)
        print("  - May chu (Host)    : %s" % local_url)
        print("  - Trang Quan tri    : %s/admin" % local_url)
        if lan_ip and args.host in ("0.0.0.0", ""):
            print("  - May tram LAN      : http://%s:%d" % (lan_ip, port))
        print("Luu y: Chi may chu (Host) moi duoc phep them/xoa ho so giong.")
    print("===============================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDa tat server.")


if __name__ == "__main__":
    main()
