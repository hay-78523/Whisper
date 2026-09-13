# -*- coding: utf-8 -*-
"""Tai & kiem tra tai san cho Voice Clone (model giong Viet + vocab).

Ba thu lam cho buoc tai ve khong con gay nua:

1. RESUME — tai vao file `.part`, dut mang thi lan sau tiep tuc tu byte dang do
   (khong tai lai 1.3GB tu dau), xong moi doi ten nguyen khoi sang `model.pt`.
   Nho vay KHONG bao gio ton tai file model "nua voi" ma tool tuong da tai xong.
2. KIEM TRA NOI DUNG — model phai la checkpoint torch that, vocab phai la bang
   ky tu that. Trang HTML bao loi, con tro Git-LFS, file JSON dat sai cho... deu
   bi phat hien ngay luc tai, va tu chuyen sang nguon (mirror) ke tiep.
3. TU SUA — file hong thi xoa va tai lai, khong bat nguoi dung tu di don.

Dung duoc ca tu CLI (`setup_clone.py`) lan tu trang /admin.
"""

import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import config

_UA = {"User-Agent": "whisper-stt-voice-clone/2.1"}
_CHUNK = 1 << 18          # 256KB


# ---------------------------------------------------------------- duong dan

def model_dir():
    return Path(getattr(config, "CLONE_MODEL_DIR", os.path.join("data", "f5_vi")))


def path_of(name):
    return model_dir() / name


def model_path():
    return path_of("model.pt")


def vocab_path():
    return path_of("vocab.txt")


def _specs():
    return getattr(config, "CLONE_ASSETS", {}) or {}


# ------------------------------------------------------------- kiem tra file

def _head(p, n=64):
    try:
        with open(p, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _is_junk(head):
    """Trang loi HTML hoac con tro Git-LFS — dau hieu tai sai duong dan."""
    h = head.lstrip()[:40].lower()
    return (h.startswith(b"<!doctype") or h.startswith(b"<html")
            or h.startswith(b"version https://git-lfs"))


def check(name, p=None):
    """Tra ve (ok, ly_do). Khong ok = file can tai lai."""
    spec = _specs().get(name) or {}
    p = Path(p or path_of(name))
    if not p.exists():
        return False, "chưa tải"
    size = p.stat().st_size
    if size < int(spec.get("min_bytes") or 1):
        return False, "file quá nhỏ (%s) — tải dở hoặc sai nguồn" % human(size)
    head = _head(p)
    if _is_junk(head):
        return False, "không phải dữ liệu model (trang lỗi HTML / con trỏ Git-LFS)"

    kind = spec.get("kind")
    if kind == "torch":
        # torch.save moi = zip (PK), ban cu = pickle (\x80). Chap nhan ca safetensors.
        if not (head.startswith(b"PK\x03\x04") or head.startswith(b"\x80")
                or head[:8].isdigit() or b"__metadata__" in head):
            return False, "không phải checkpoint PyTorch"
    elif kind == "vocab":
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return False, "không đọc được: %s" % e
        stripped = txt.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            return False, "đây là file JSON (config), không phải bảng ký tự vocab"
        lines = [ln for ln in txt.splitlines() if ln.strip() != ""]
        if len(lines) < 50:
            return False, "chỉ có %d dòng — vocab thật phải hàng trăm dòng" % len(lines)
        if sum(1 for ln in lines if len(ln) > 12) > len(lines) * 0.2:
            return False, "nội dung không giống bảng ký tự"
    return True, "ok"


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit in ("B", "KB") else "%.2f %s" % (n, unit)
        n /= 1024


def state():
    """Tinh trang tung file — dung cho trang /admin va `setup_clone.py --check`."""
    out = []
    for name, spec in _specs().items():
        p = path_of(name)
        part = p.with_name(p.name + ".part")
        ok, why = check(name, p)
        out.append({
            "name": name,
            "label": spec.get("label", name),
            "path": str(p),
            "exists": p.exists(),
            "size": p.stat().st_size if p.exists() else 0,
            "partial": part.stat().st_size if part.exists() else 0,
            "expect_bytes": int(spec.get("min_bytes") or 0),
            "ok": ok,
            "problem": None if ok else why,
        })
    return out


def ready():
    return all(a["ok"] for a in state()) and bool(_specs())


def missing():
    return [a["name"] for a in state() if not a["ok"]]


# ------------------------------------------------------------------ tai ve

def _download(url, dest, progress=None, log=None, timeout=90):
    """Tai 1 URL vao dest, co resume. Tra ve so byte da tai."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0

    req = urllib.request.Request(url, headers=dict(_UA))
    if have:
        req.add_header("Range", "bytes=%d-" % have)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resumed = (getattr(r, "status", r.getcode()) == 206)
        if have and not resumed:
            have = 0                       # may chu khong cho resume -> tai lai
        total = int(r.headers.get("Content-Length") or 0) + (have if resumed else 0)
        if log:
            log("  %s %s" % ("tiếp tục từ " + human(have) if have else "bắt đầu tải",
                             "/ " + human(total) if total else ""))
        mode = "ab" if have else "wb"
        last = 0.0
        with open(part, mode) as f:
            while True:
                chunk = r.read(_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                have += len(chunk)
                if progress and (time.time() - last > 0.3):
                    last = time.time()
                    progress(have, total)
    if progress:
        progress(have, have)
    os.replace(part, dest)
    return have


def fetch(name, progress=None, log=None, force=False):
    """Bao dam 1 file san sang: tai (resume) + kiem tra, xoay vong cac nguon.

    progress(da_tai, tong) — tinh theo byte cua rieng file nay.
    """
    spec = _specs().get(name)
    if not spec:
        raise ValueError("Không biết tài sản '%s'." % name)
    dest = path_of(name)
    log = log or (lambda *_a: None)

    if not force:
        ok, _why = check(name, dest)
        if ok:
            log("✓ %s đã có sẵn (%s)" % (spec.get("label", name), human(dest.stat().st_size)))
            return dest

    if dest.exists():
        ok, why = check(name, dest)
        if not ok:
            log("⚠ %s bị lỗi (%s) — xóa và tải lại." % (dest.name, why))
            try:
                dest.unlink()
            except OSError:
                pass

    errors = []
    tries = max(1, int(getattr(config, "CLONE_DOWNLOAD_RETRY", 3)))
    for url in spec.get("urls", []):
        for attempt in range(1, tries + 1):
            try:
                log("⬇ %s  (nguồn %s, lần %d)" % (spec.get("label", name),
                                                  url.split("//")[-1].split("/")[0], attempt))
                _download(url, dest, progress=progress, log=log)
                ok, why = check(name, dest)
                if ok:
                    log("✓ xong: %s (%s)" % (dest.name, human(dest.stat().st_size)))
                    return dest
                errors.append("%s: %s" % (url, why))
                log("✗ tải xong nhưng không hợp lệ (%s) — thử nguồn khác." % why)
                try:
                    dest.unlink()
                except OSError:
                    pass
                break                      # noi dung sai thi doi URL, thu lai vo ich
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError) as e:
                errors.append("%s: %s" % (url, e))
                log("✗ lỗi mạng: %s" % e)
                if attempt < tries:
                    time.sleep(min(2 ** attempt, 15))
    raise RuntimeError("Không tải được %s.\n  %s" % (spec.get("label", name),
                                                     "\n  ".join(errors[-4:])))


def ensure(progress=None, log=None, force=False):
    """Tai het nhung gi con thieu. progress(da_tai, tong) tinh GOP ca cac file."""
    log = log or (lambda *_a: None)
    todo = list(_specs().items()) if force else [(n, _specs()[n]) for n in missing()]
    if not todo:
        log("✓ Dữ liệu Voice Clone đã đầy đủ.")
        return True

    # tong uoc tinh de ve thanh tien do gop
    est_total = sum(int(s.get("min_bytes") or 0) for _n, s in todo) or 1
    base = [0]

    for name, spec in todo:
        def _p(done, total, _name=name, _spec=spec):
            if progress:
                progress(base[0] + done, max(est_total, base[0] + (total or 0)))
        fetch(name, progress=_p, log=log, force=force)
        base[0] += path_of(name).stat().st_size
    if progress:
        progress(base[0], base[0])
    return True


def clean_partials():
    """Xoa cac file .part dang do (khi nguoi dung muon tai lai tu dau)."""
    n = 0
    d = model_dir()
    if d.is_dir():
        for p in d.glob("*.part"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n
