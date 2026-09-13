#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI cho whisper_stt — phien am / dich / long tieng khong can mo web.

    python3 cli.py transcribe audio.mp3 --language vi --srt
    python3 cli.py transcribe ./folder --recursive
    python3 cli.py dub --audio video.mp4 --translate vi -o long_tieng.mp3
    python3 cli.py dub --text "Xin chào" -o chao.mp3
    python3 cli.py dub --file transcript.txt --voice edge:ja-JP-NanamiNeural -o ja.mp3
    python3 cli.py voices

Nhan ban giong (tinh nang chinh — cai: python3 setup_clone.py):
    python3 cli.py clone status
    python3 cli.py clone add mau_giong.m4a --name dan --text "cau trong mau"
    python3 cli.py clone list
    python3 cli.py clone test dan
    python3 cli.py clone delete dan
    python3 cli.py dub --text "Xin chào" --voice c:dan -o chao.wav
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config
from app.core import clone, engine, translate, tts


def cmd_voices(_args):
    names = clone.list_profiles()
    if names:
        print("Giong nhan ban cua ban:")
        for n in names:
            inf = clone.profile_info(n)
            print("  %-28s mau %s%s" % (
                "c:" + n,
                ("%.0fs" % inf["duration"]) if inf["duration"] else "?",
                (" — " + "; ".join(inf["warnings"])) if inf["warnings"] else ""))
        print()
    for v, desc in config.COMMON_VOICES:
        print("  %-28s %s" % (v, desc))
    print("\nNgon ngu dich duoc:", ", ".join(config.TRANSLATE_LANGS))
    print("Xem het giong edge: python3 -m edge_tts --list-voices")


def cmd_clone(args):
    act = args.action
    if act == "status":
        st = clone.status(recheck=True)
        for a in st["assets"]:
            print("  %s %-44s %s" % ("OK " if a["ok"] else "-->", a["label"],
                                     a["problem"] or "%d MB" % (a["size"] // (1 << 20))))
        e = st["engine"]
        print("  %s engine F5-TTS: %s" % ("OK " if e["installed"] else "-->",
                                          e["python"] or "chua cai"))
        print("  chat luong: %d buoc | thiet bi: %s"
              % (st["quality"]["nfe_step"], st["settings"].get("device") or "tu chon"))
        print("  giong: %s" % (", ".join(p["name"] for p in st["profiles"]) or "(chua co)"))
        if not st["ready"]:
            print("\nChua san sang — chay: python3 setup_clone.py")
        return 0 if st["ready"] else 1

    if act == "list":
        for n in clone.list_profiles():
            inf = clone.profile_info(n)
            print("  c:%-22s %s  script: %s" % (
                n, ("%.1fs" % inf["duration"]) if inf["duration"] else "?",
                (inf["ref_text"] or "(chua co)")[:60]))
        return 0

    if act == "add":
        if not args.name and not args.file:
            sys.exit("Can --file (hoac tham so vi tri) cho mau giong.")
        src = Path(args.file)
        if not src.is_file():
            sys.exit("Khong co file: %s" % src)
        name = args.name or src.stem
        print("Dang xu ly mau '%s'..." % src.name)
        inf = clone.add_profile(name, src.read_bytes(), args.text or "",
                               filename=src.name, background=False)
        print("Da tao giong c:%s — mau %.1fs%s"
              % (inf["name"], inf["duration"] or 0,
                 " (da cat ngan)" if inf["trimmed"] else ""))
        print("  script mau: %s" % (inf["ref_text"] or "(chua lay duoc)"))
        for w in inf["warnings"]:
            print("  luu y: %s" % w)
        if inf["error"]:
            print("  loi: %s" % inf["error"])
        return 0

    if act == "test":
        out = clone.make_sample(args.file or args.name)
        print("Da ghi ban nghe thu: %s" % out)
        clone.stop_worker()
        return 0

    if act == "delete":
        target = args.file or args.name
        print("Da xoa." if clone.delete_profile(target) else "Khong co giong do.")
        return 0
    sys.exit("Hanh dong khong hop le: %s" % act)


def _collect(target, recursive):
    p = Path(target)
    if p.is_file():
        return [p]
    if p.is_dir():
        it = p.rglob("*") if recursive else p.glob("*")
        files = sorted(f for f in it if f.is_file() and f.suffix.lower() in engine.AUDIO_EXTS)
        if files:
            return files
        sys.exit("Khong tim thay file audio nao trong %s" % p)
    sys.exit("Khong ton tai: %s" % target)


def cmd_transcribe(args):
    files = _collect(args.input, args.recursive)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Nap model '%s' (lan dau se tai ve)..." % args.model)
    t0 = time.time()
    model = engine.get_model(args.model)
    print("Model san sang sau %.1fs. Phien am %d file -> %s/"
          % (time.time() - t0, len(files), out_dir))

    ok = fail = 0
    for i, path in enumerate(files, 1):
        print("[%d/%d] %s (%.1f MB)..." % (i, len(files), path.name,
                                           path.stat().st_size / 1e6), end=" ", flush=True)
        t0 = time.time()
        try:
            result = engine.transcribe(model, path, args.language)
        except Exception as e:
            print("LOI: %s" % e)
            fail += 1
            continue
        base = out_dir / path.stem
        base.with_suffix(".txt").write_text(result["text"] + "\n", encoding="utf-8")
        wrote = [".txt"]
        if args.srt and result["words"]:
            base.with_suffix(".srt").write_text(engine.words_to_srt(result["words"]),
                                                encoding="utf-8")
            wrote.append(".srt")
        if args.json:
            base.with_suffix(".json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            wrote.append(".json")
        print("xong %.1fs — ngon ngu %s (%.0f%%), audio %.0fs -> %s"
              % (time.time() - t0, result["language_code"],
                 (result.get("language_probability") or 0) * 100,
                 result.get("duration", 0), "+".join(wrote)))
        ok += 1

    print("\nHoan tat: %d thanh cong, %d loi. Ket qua trong %s/" % (ok, fail, out_dir))
    if fail:
        sys.exit(2)


def cmd_dub(args):
    # --video-out: luong CAN KHOP THOI GIAN (doc tung doan, dat dung timestamp)
    if args.video_out:
        if not args.audio:
            sys.exit("--video-out can di kem --audio <video goc>.")
        from app.core import video
        print("Phien am %s (model %s)..." % (args.audio, args.model))
        model = engine.get_model(args.model)
        result = engine.transcribe(model, args.audio, args.language)
        cues = engine.words_to_cues(result.get("words") or [])
        print("Ngon ngu %s, %d doan co timestamp." % (result["language_code"], len(cues)))
        texts = [c["text"] for c in cues]
        if args.translate and args.translate.split("-")[0] != result["language_code"]:
            print("Dich %d doan sang '%s'..." % (len(cues), args.translate))
            texts = translate.translate_cues(texts, args.translate)
        print("Doc %d doan bang giong %s..." % (len(texts), args.voice))
        clips = tts.synth_cues(texts, args.voice, args.rate)
        aud = Path(args.out) if args.out else Path(args.video_out).with_suffix(".m4a")
        print("Can khop thoi gian + ghep video...")
        total = video.duration(args.audio)
        video.assemble_aligned(cues, clips, total, aud)
        video.mux(args.audio, aud, args.video_out)
        print("Da ghi %s (%.0fs, %d doan can dung timestamp) + track audio %s"
              % (args.video_out, total, len(cues), aud))
        return

    # khong co --video-out: doc van ban / doc lai audio thanh 1 file lien mach
    if args.text:
        text = args.text
    elif args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.audio:
        print("Phien am %s (model %s)..." % (args.audio, args.model))
        model = engine.get_model(args.model)
        result = engine.transcribe(model, args.audio, args.language)
        text = result["text"]
        print("Transcript (%s): %s%s" % (result["language_code"], text[:120],
                                         "..." if len(text) > 120 else ""))
    else:
        sys.exit("Can --audio, --text hoac --file.")

    if args.translate:
        print("Dich sang '%s' (%d ky tu)..." % (args.translate, len(text)))
        text = translate.translate_text(text, args.translate, log=print)
        print("Ban dich: %s%s" % (text[:120], "..." if len(text) > 120 else ""))

    ext = ".m4a" if args.voice.startswith("say:") else ".mp3"
    out = Path(args.out) if args.out else Path("long_tieng" + ext)
    print("Doc bang giong %s (%d ky tu)..." % (args.voice, len(text)))
    tts.synth(text, args.voice, args.rate, out_path=out, log=print)
    print("Da ghi %s (%.0f KB)" % (out, out.stat().st_size / 1e3))


def main():
    ap = argparse.ArgumentParser(description="whisper_stt CLI — phien am / dich / long tieng")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transcribe", help="phien am audio -> txt/srt/json")
    t.add_argument("input", help="file audio hoac thu muc")
    t.add_argument("--language", default=None, help="ma ngon ngu (vd: vi); bo trong de tu phat hien")
    t.add_argument("--model", default=config.DEFAULT_MODEL, choices=config.MODELS)
    t.add_argument("--srt", action="store_true", help="xuat them phu de .srt")
    t.add_argument("--json", action="store_true", help="xuat them JSON tho")
    t.add_argument("--out", default="transcripts", help="thu muc ket qua (mac dinh ./transcripts)")
    t.add_argument("--recursive", action="store_true")
    t.set_defaults(func=cmd_transcribe)

    d = sub.add_parser("dub", help="dich + long tieng thanh file audio")
    src = d.add_mutually_exclusive_group(required=True)
    src.add_argument("--audio", help="file audio goc: phien am roi doc lai")
    src.add_argument("--text", help="van ban can doc")
    src.add_argument("--file", help="file .txt can doc")
    d.add_argument("--translate", default=None, metavar="LANG",
                   help="dich sang ngon ngu nay truoc khi doc (%s)" % ",".join(config.TRANSLATE_LANGS))
    d.add_argument("--voice", default=config.DEFAULT_VOICE)
    d.add_argument("--rate", default="+0%", help="toc do: -20%% cham, +20%% nhanh (engine edge)")
    d.add_argument("--model", default=config.DEFAULT_MODEL, choices=config.MODELS,
                   help="model Whisper khi dung --audio")
    d.add_argument("--language", default=None, help="ngon ngu audio goc khi dung --audio")
    d.add_argument("-o", "--out", default=None)
    d.add_argument("--video-out", default=None, metavar="MP4",
                   help="ghep giong doc vao video goc (--audio) va xuat ra file mp4 nay")
    d.set_defaults(func=cmd_dub)

    v = sub.add_parser("voices", help="liet ke giong doc co san")
    v.set_defaults(func=cmd_voices)

    c = sub.add_parser("clone", help="nhan ban giong: status / add / list / test / delete")
    c.add_argument("action", choices=["status", "add", "list", "test", "delete"])
    c.add_argument("file", nargs="?", help="file mau (add) hoac ten giong (test/delete)")
    c.add_argument("--name", help="ten giong (mac dinh: lay ten file)")
    c.add_argument("--text", help="script cua mau (bo trong = tool tu nghe)")
    c.set_defaults(func=cmd_clone)

    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
