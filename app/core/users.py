# -*- coding: utf-8 -*-
"""Quan ly tai khoan — luu users.json canh run.py, mat khau bam PBKDF2.

Vai tro:
    user   dung trang chinh (phien am, dich, long tieng)
    admin  them quyen vao /admin: xem job, model, quan ly tai khoan
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _user_data_dir():
    """Thu muc du lieu cua nguoi dung — luon ghi duoc, ke ca khi thu muc cai bi khoa."""
    local_data = ROOT / "data"
    try:
        local_data.mkdir(parents=True, exist_ok=True)
        return local_data
    except OSError:
        pass

    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    d = base / "whisper_stt"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = Path.home()
    return d


def _pick_users_file():
    """Uu tien users.json canh run.py; thu muc cai khong cho ghi (hay gap tren
    Windows voi thu muc bi read-only/ACL) thi dung thu muc du lieu nguoi dung."""
    root_file = ROOT / "users.json"
    alt_file = _user_data_dir() / "users.json"
    if root_file.exists():
        return root_file
    if alt_file.exists():
        return alt_file
    try:
        probe = ROOT / ".write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return root_file
    except OSError:
        return alt_file


USERS_FILE = _pick_users_file()

DEFAULT_ADMIN = ("admin", "admin123")   # tao lan dau — nho doi mat khau!

_lock = threading.Lock()


def _hash(password, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes.fromhex(salt_hex), 100000).hex()


def _load():
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {}


def _save(users):
    global USERS_FILE
    data = json.dumps(users, ensure_ascii=False, indent=2)
    try:
        tmp = USERS_FILE.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, USERS_FILE)
    except OSError:
        # thu muc cai bi cam ghi giua chung -> chuyen sang thu muc du lieu nguoi dung
        USERS_FILE = _user_data_dir() / "users.json"
        tmp = USERS_FILE.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, USERS_FILE)
    try:
        os.chmod(USERS_FILE, 0o600)  # chua hash mat khau — chi chu may duoc doc
    except OSError:
        pass


def ensure_default():
    """Tao tai khoan lan dau neu chua co users.json. Tra ve True neu vua tao.

    Uu tien bien moi truong WHISPER_USERS (dang "ten:matkhau:role,ten2:mk2:user")
    — dung cho moi truong o dia bi reset nhu Hugging Face Spaces (dat trong
    Settings > Variables and secrets cua Space). Khong co thi tao admin mac dinh.
    """
    with _lock:
        if _load():
            return False
        users = {}
        for part in os.environ.get("WHISPER_USERS", "").split(","):
            bits = part.strip().split(":")
            if len(bits) == 3 and bits[2] in ("user", "admin") and bits[0] and bits[1]:
                salt = secrets.token_hex(16)
                users[bits[0]] = {"salt": salt, "hash": _hash(bits[1], salt), "role": bits[2]}
        if not users:
            name, password = DEFAULT_ADMIN
            salt = secrets.token_hex(16)
            users[name] = {"salt": salt, "hash": _hash(password, salt), "role": "admin"}
        _save(users)
        return True


def verify(username, password):
    """Tra ve vai tro ('user'/'admin') neu dung mat khau, nguoc lai None."""
    u = _load().get(username or "")
    if not u:
        _hash(password or "", "00" * 16)  # can do thoi gian phan hoi
        return None
    if hmac.compare_digest(_hash(password or "", u["salt"]), u["hash"]):
        return u["role"]
    return None


def list_users():
    return [{"username": n, "role": u["role"]} for n, u in sorted(_load().items())]


def add_user(username, password, role):
    username = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,32}", username):
        raise ValueError("Tên tài khoản: 2-32 ký tự chữ/số/._-")
    if len(password or "") < 4:
        raise ValueError("Mật khẩu tối thiểu 4 ký tự.")
    if role not in ("user", "admin"):
        raise ValueError("Vai trò phải là user hoặc admin.")
    with _lock:
        users = _load()
        if username in users:
            raise ValueError("Tên tài khoản đã tồn tại.")
        salt = secrets.token_hex(16)
        users[username] = {"salt": salt, "hash": _hash(password, salt), "role": role}
        _save(users)


def delete_user(username, acting_user):
    with _lock:
        users = _load()
        if username not in users:
            raise ValueError("Không có tài khoản này.")
        if username == acting_user:
            raise ValueError("Không thể tự xóa tài khoản đang đăng nhập.")
        admins = sum(1 for u in users.values() if u["role"] == "admin")
        if users[username]["role"] == "admin" and admins <= 1:
            raise ValueError("Không thể xóa admin cuối cùng.")
        users.pop(username)
        _save(users)


def set_password(username, password):
    if len(password or "") < 4:
        raise ValueError("Mật khẩu tối thiểu 4 ký tự.")
    with _lock:
        users = _load()
        if username not in users:
            raise ValueError("Không có tài khoản này.")
        salt = secrets.token_hex(16)
        users[username].update(salt=salt, hash=_hash(password, salt))
        _save(users)
