# -*- coding: utf-8 -*-
"""Quan ly job chay nen: tao / theo doi tien do / don dep.

Moi viec nang (phien am, dich, tao giong) chay trong thread rieng va cap nhat
tien do vao job dict de tang web tra ve cho UI poll.
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from .. import config
from ..config import JOB_KEEP
from . import clone, engine, translate, tts, video

HISTORY_DIR = Path(__file__).resolve().parents[2] / "data" / "job_history"
HISTORY_MAX = 100


class JobManager(object):
    def __init__(self, keep=JOB_KEEP):
        self._jobs = {}
        self._lock = threading.Lock()
        self._hlock = threading.Lock()
        self._next = 0
        self._keep = keep

    # ---------- vong doi job ----------

    def _new(self, kind, label, owner=None, voice=None, full_script=None, rate=None):
        with self._lock:
            self._next += 1
            scr = (full_script or "").strip()
            job = {"id": str(self._next), "kind": kind, "label": label,
                   "owner": owner or "admin", "voice": voice or "",
                   "rate": rate or "+0%", "char_count": len(scr),
                   "full_script": scr,
                   "state": "running", "phase": "", "done": 0, "total": 0,
                   "phase_started": time.time(), "created": time.time(),
                   "finished": None, "error": None, "result": None,
                   "audio": None, "mime": None}
            self._jobs[job["id"]] = job
            for k in sorted(self._jobs, key=int)[:-self._keep]:
                self._cleanup(self._jobs.pop(k, None))
        return job

    @staticmethod
    def _cleanup(job):
        if not job:
            return
        for key in ("video_path", "media_path"):
            if job.get(key):
                try:
                    os.unlink(job[key])
                except OSError:
                    pass
                job[key] = None

    @staticmethod
    def _set_phase(job, phase):
        job.update(phase=phase, done=0, total=0, phase_started=time.time(), note=None)

    @staticmethod
    def _prog(job):
        """Callback tien do: (done, total) + ghi chu buoc dang lam (vd 'Kiểm tra đúng chữ khúc 3/35')
        -> thanh tien do khong dung im o 35/35 trong luc tu kiem tra / doc lai."""
        def cb(d, t, note=None):
            job.update(done=d, total=t, note=note)
        return cb

    def _finish(self, job, error=None):
        job["finished"] = time.time()
        if error:
            job.update(state="error", error=error)
        else:
            job["state"] = "done"
            if job.get("kind") in ("text_dub", "render") and job.get("audio"):
                try:
                    self._record_history(job)
                except Exception:
                    pass

    def _record_history(self, job):
        """Luu lich su 'Nhan vien da Gen cai gi' (kich ban day du + file audio) vao data/job_history."""
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        hist_id = "%d_%s" % (int(job["created"]), job["id"])
        mime = job.get("mime") or "audio/wav"
        ext = "mp3" if "mpeg" in mime else "m4a" if "mp4" in mime else "wav"
        audio_fn = "gen_%s.%s" % (hist_id, ext)
        audio_path = HISTORY_DIR / audio_fn
        audio_bytes = job.get("audio") or b""
        audio_path.write_bytes(audio_bytes)
        scr = (job.get("full_script") or "").strip()
        entry = {
            "id": hist_id,
            "job_id": job["id"],
            "owner": job.get("owner") or "admin",
            "kind": job["kind"],
            "label": job.get("label") or "",
            "voice": job.get("voice") or "",
            "rate": job.get("rate") or "+0%",
            "char_count": len(scr) or job.get("char_count") or 0,
            "script_preview": scr[:160] + ("…" if len(scr) > 160 else ""),
            "full_script": scr,
            "created": job["created"],
            "finished": job["finished"],
            "elapsed": round((job["finished"] or time.time()) - job["created"], 1),
            "audio_file": audio_fn,
            "audio_kb": len(audio_bytes) // 1024,
            "mime": mime,
            "state": "done",
        }
        with self._hlock:
            items = self._load_history_raw()
            items.insert(0, entry)
            while len(items) > HISTORY_MAX:
                old = items.pop()
                if old.get("audio_file"):
                    try:
                        (HISTORY_DIR / old["audio_file"]).unlink()
                    except OSError:
                        pass
            tmp = HISTORY_DIR / "history.json.tmp"
            tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, HISTORY_DIR / "history.json")

    @staticmethod
    def _load_history_raw():
        p = HISTORY_DIR / "history.json"
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []

    def list_history(self, limit=60, owner=None):
        """Danh sach lich su Gen kich ban & Audio cho Truong phong (/admin)."""
        live = []
        for job in sorted(self._jobs.values(), key=lambda j: -int(j["id"])):
            if job.get("kind") not in ("text_dub", "render"):
                continue
            if job.get("state") == "done":
                continue
            if owner and job.get("owner") != owner:
                continue
            scr = (job.get("full_script") or "").strip()
            end = job["finished"] or time.time()
            live.append({
                "id": "live_" + job["id"],
                "job_id": job["id"],
                "owner": job.get("owner") or "admin",
                "kind": job["kind"],
                "label": job.get("label") or "",
                "voice": job.get("voice") or "",
                "rate": job.get("rate") or "+0%",
                "char_count": len(scr) or job.get("char_count") or 0,
                "script_preview": scr[:160] + ("…" if len(scr) > 160 else ""),
                "full_script": scr,
                "created": job["created"],
                "finished": job["finished"],
                "elapsed": round(end - job["created"], 1),
                "audio_kb": len(job["audio"]) // 1024 if job.get("audio") else 0,
                "mime": job.get("mime") or "audio/wav",
                "state": job["state"],
                "phase": job.get("phase") or "",
                "done": job.get("done") or 0,
                "total": job.get("total") or 0,
                "error": job.get("error"),
            })
        saved = self._load_history_raw()
        if owner:
            saved = [x for x in saved if x.get("owner") == owner]
        return (live + saved)[:limit]

    def get_history_audio(self, hist_id):
        """Lay (bytes, mime, filename) cua 1 ban ghi trong lich su Gen."""
        for x in self._load_history_raw():
            if x.get("id") == hist_id or x.get("job_id") == hist_id:
                fn = x.get("audio_file")
                if fn and (HISTORY_DIR / fn).exists():
                    return (HISTORY_DIR / fn).read_bytes(), x.get("mime") or "audio/wav", fn
        job = self.get(hist_id.replace("live_", ""))
        if job and job.get("audio"):
            mime = job.get("mime") or "audio/wav"
            ext = "mp3" if "mpeg" in mime else "m4a" if "mp4" in mime else "wav"
            return job["audio"], mime, "dub_%s.%s" % (job["id"], ext)
        return None, None, None

    def delete_history(self, hist_id):
        with self._hlock:
            items = self._load_history_raw()
            keep = []
            removed = False
            for x in items:
                if x.get("id") == hist_id:
                    removed = True
                    if x.get("audio_file"):
                        try:
                            (HISTORY_DIR / x["audio_file"]).unlink()
                        except OSError:
                            pass
                else:
                    keep.append(x)
            if removed:
                HISTORY_DIR.mkdir(parents=True, exist_ok=True)
                tmp = HISTORY_DIR / "history.json.tmp"
                tmp.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, HISTORY_DIR / "history.json")
            return removed

    def get(self, job_id):
        return self._jobs.get(job_id)

    def delete(self, job_id):
        with self._lock:
            job = self._jobs.pop(job_id, None)
            self._cleanup(job)
            return job is not None

    def list(self):
        """Tom tat moi job (khong kem du lieu nang) cho trang admin."""
        out = []
        for job in sorted(self._jobs.values(), key=lambda j: -int(j["id"])):
            end = job["finished"] or time.time()
            scr = (job.get("full_script") or "").strip()
            out.append({
                "id": job["id"], "kind": job["kind"], "label": job["label"],
                "owner": job.get("owner") or "admin",
                "voice": job.get("voice") or "",
                "char_count": len(scr) or job.get("char_count") or 0,
                "script_preview": scr[:120] + ("…" if len(scr) > 120 else ""),
                "state": job["state"], "phase": job["phase"],
                "done": job["done"], "total": job["total"],
                "created": job["created"], "elapsed": round(end - job["created"], 1),
                "error": job["error"],
                "audio_kb": len(job["audio"]) // 1024 if job.get("audio") else 0,
                "log": job.get("log"),
            })
        return out

    def summary(self):
        states = [j["state"] for j in self._jobs.values()]
        return {"total": len(states),
                "running": states.count("running"),
                "done": states.count("done"),
                "error": states.count("error"),
                "history_total": len(self._load_history_raw())}

    # ---------- cac loai job ----------

    def start_prepare(self, media_tmp, filename, model_name, language, target, owner=None):
        """Buoc 1 tron goi: phien am -> cat doan giu timestamp -> dich tung doan.

        File goc duoc GIU LAI (job["media_path"]) de buoc render ghep video
        khong phai upload lai; job prepare cu hon bi don file goc.
        """
        job = self._new("prepare", filename, owner=owner)
        job["media_path"] = media_tmp
        with self._lock:  # chi giu file goc cua job prepare moi nhat (job dang chay thi tha)
            for other in self._jobs.values():
                if (other["id"] != job["id"] and other.get("media_path")
                        and other["state"] != "running"):
                    self._cleanup(other)

        def work():
            try:
                self._set_phase(job, "model")
                model = engine.get_model(model_name)
                self._set_phase(job, "transcribe")
                result = engine.transcribe(
                    model, media_tmp, language,
                    progress=lambda d, t: job.update(done=round(d, 1), total=round(t, 1)))
                cues = engine.words_to_cues(result.get("words") or [])
                detected = result.get("language_code") or ""
                need_translate = bool(target) and target.split("-")[0] != detected
                engine_label = ""
                if need_translate and cues:
                    self._set_phase(job, "translate")
                    trans = translate.translate_cues(
                        [c["text"] for c in cues], target,
                        progress=self._prog(job))
                    engine_label = translate.LAST_ENGINE
                else:
                    trans = [c["text"] for c in cues]
                for c, t in zip(cues, trans):
                    c["trans"] = t
                job["cues"] = cues
                job["is_video"] = video.is_video(media_tmp)
                job["result"] = {
                    "filename": filename,
                    "language_code": detected,
                    "language_probability": result.get("language_probability"),
                    "duration": result.get("duration"),
                    "model": model_name,
                    "target": target if need_translate else "",
                    "translate_engine": engine_label,
                    "is_video": job["is_video"],
                    "text": result.get("text", ""),
                    "srt": engine.words_to_srt(result.get("words") or []),
                    "cues": cues,
                }
                self._finish(job)
            except Exception as e:
                self._cleanup(job)
                self._finish(job, "Loi xu ly: %s" % e)

        threading.Thread(target=work, daemon=True).start()
        return job

    def start_render(self, prepare_job_id, texts, voice, rate, owner=None):
        """Buoc 2: doc tung doan (texts da duoc nguoi dung duyet/sua) ->
        dat dung timestamp -> ghep vao video goc (neu la video)."""
        prep = self.get(prepare_job_id)
        if not prep or prep["kind"] != "prepare" or prep["state"] != "done":
            raise ValueError("Chưa có kết quả phiên âm — chạy bước 1 trước.")
        cues = prep.get("cues") or []
        if not cues:
            raise ValueError("Không có đoạn lời nào để đọc.")
        if texts is not None and len(texts) != len(cues):
            raise ValueError("Số dòng (%d) không khớp số đoạn (%d) — đừng thêm/xóa dòng."
                             % (len(texts), len(cues)))
        media = prep.get("media_path")
        if not media or not os.path.isfile(media):
            raise ValueError("File gốc đã bị dọn — upload và chạy lại bước 1.")
        final_texts = texts if texts is not None else [c["trans"] for c in cues]
        is_video = prep.get("is_video")
        total_s = (prep.get("result") or {}).get("duration") or video.duration(media)
        joined_script = "\n".join(final_texts)
        job = self._new("render", "%d đoạn" % len(cues), owner=owner or prep.get("owner"),
                        voice=voice, full_script=joined_script, rate=rate)

        def work():
            out_audio = tempfile.mkstemp(suffix=".m4a")[1]
            out_video = tempfile.mkstemp(suffix=".mp4")[1] if is_video else None
            try:
                self._set_phase(job, "tts")
                clips = tts.synth_cues(
                    final_texts, voice, rate,
                    progress=self._prog(job))
                self._set_phase(job, "assemble")
                audio_bytes = video.assemble_aligned(
                    cues, clips, total_s, out_audio,
                    progress=self._prog(job))
                job.update(audio=audio_bytes, mime="audio/mp4")
                if is_video:
                    self._set_phase(job, "mux")
                    job["total"] = 1
                    video.mux(media, out_audio, out_video)
                    job.update(done=1, video_path=out_video)
                job["result"] = {"cues": len(cues), "is_video": bool(is_video),
                                 "video_seconds": round(total_s, 1)}
                self._finish(job)
            except Exception as e:
                if out_video:
                    try:
                        os.unlink(out_video)
                    except OSError:
                        pass
                self._finish(job, "Loi tao giong/ghep video: %s (giong edge can mang)" % e)
            finally:
                try:
                    os.unlink(out_audio)
                except OSError:
                    pass

        threading.Thread(target=work, daemon=True).start()
        return job

    def start_text_dub(self, text, voice, rate, target, owner=None, genre=None, intensity=None):
        """Dich van ban (neu co target) roi doc thanh audio — khong can file media.

        Van ban co THE BIEU CAM ([vui], *nhan*, [nghi]...) + giong edge
        -> tu dong dung engine bieu cam (bo qua dich — coi nhu da la ban cuoi).
        """
        from . import expressive
        has_multi = expressive.has_multi_voice(text)
        has_tags = expressive.has_markup(text)
        use_expr = has_tags and voice.startswith("edge:")
        use_acted = has_tags and voice.startswith("gemini:")
        if has_tags and voice.startswith("vieneu:"):
            # VieNeu hieu [cười]/[thở dài] goc; the khac chuyen/luoc truoc khi doc
            text = expressive.vieneu_script(text)
        job = self._new("text_dub", "%d ký tự%s%s" % (
            len(text), " → " + target if target else "",
            " · biểu cảm" if (use_expr or use_acted or has_multi or voice.startswith("c:")) else ""),
            owner=owner, voice=voice, full_script=text, rate=rate)

        def work():
            try:
                if has_multi:
                    self._set_phase(job, "tts")
                    parts = expressive._VOICE_TAG_RE.split(text or "")
                    clips = []
                    cur_voice = voice
                    
                    total_chars = len(text)
                    done_chars = [0]
                    
                    for i, part in enumerate(parts):
                        if i % 2 == 1:
                            cur_voice = part.strip()
                            continue
                            
                        if not part.strip():
                            continue
                            
                        v = cur_voice
                        part_has_tags = expressive.has_markup(part)
                        part_use_expr = part_has_tags and v.startswith("edge:")
                        part_use_acted = part_has_tags and v.startswith("gemini:")
                        part_text = part
                        
                        if part_has_tags and v.startswith("vieneu:"):
                            part_text = expressive.vieneu_script(part)
                            
                        def part_progress(d, t, pt=part_text):
                            job.update(done=done_chars[0] + (len(pt) * d / max(1, t)), total=total_chars)

                        if part_use_acted:
                            data, mime = expressive.synth_expressive_gemini(
                                part_text, v.split(":", 1)[1], target=target, progress=part_progress)
                        elif part_use_expr:
                            data, mime = expressive.synth_expressive(
                                part_text, v, rate, target=target, progress=part_progress)
                        elif v.startswith("c:") and not target:
                            # giong clone: giu the -> co bieu cam that
                            data, mime, _ = tts.synth(part_text, v, rate, progress=part_progress,
                                                      genre=genre, intensity=intensity)
                        else:
                            current = expressive.strip_tags(part_text)
                            if target:
                                current = translate.translate_text(current, target)
                            if v.startswith("edge:"):
                                data, mime = expressive.synth_human(current, v, rate, progress=part_progress)
                            else:
                                data, mime, _ = tts.synth(current, v, rate, progress=part_progress)
                        
                        clips.append(data)
                        done_chars[0] += len(part_text)
                        
                    audio_bytes = tts._concat_clips_to_mp3(clips)
                    job.update(audio=audio_bytes, mime="audio/mpeg")
                    job["result"] = {"translated": None, "mime": "audio/mpeg",
                                     "chars": len(text), "expressive": True}
                    self._finish(job)
                    return

                if use_acted:
                    # Gemini TTS dien xuat: tool tu tach doan + chi dan phong cach
                    # tung doan — the khong bao gio bi doc thanh loi
                    self._set_phase(job, "tts")
                    data, mime = expressive.synth_expressive_gemini(
                        text, voice.split(":", 1)[1], target=target,
                        progress=self._prog(job))
                    job.update(audio=data, mime=mime)
                    job["result"] = {"translated": None, "mime": mime,
                                     "chars": len(text), "expressive": True}
                    self._finish(job)
                    return
                if use_expr:
                    self._set_phase(job, "tts")
                    data, mime = expressive.synth_expressive(
                        text, voice, rate, target=target,
                        progress=self._prog(job))
                    job.update(audio=data, mime=mime)
                    job["result"] = {"translated": None, "mime": mime,
                                     "chars": len(text), "expressive": True}
                    self._finish(job)
                    return
                keep_tags = voice.startswith("c:") and not target
                auto_note, script = None, text
                if (keep_tags and not expressive.has_markup(text)
                        and getattr(config, "AUTO_EMOTION", True)):
                    # kich ban KHONG co the nao -> AI bien kich tu gan cam xuc truoc khi doc
                    # (khong thi ca bai doc giong [binh thuong], nghe deu deu)
                    self._set_phase(job, "annotate")
                    rep = {}
                    try:
                        script, auto_note = expressive.auto_annotate(
                            text, progress=self._prog(job),
                            report=rep, genre=genre)
                        job["auto_script"], job["auto_genre"] = script, rep.get("genre")
                    except Exception as e:
                        auto_note = "Không tự gắn được cảm xúc: %s" % e
                current = script if keep_tags else expressive.strip_tags(text)
                if target:
                    self._set_phase(job, "translate")
                    current = translate.translate_text(
                        current, target,
                        progress=self._prog(job))
                    job["translated"] = current
                self._set_phase(job, "tts")
                if voice.startswith("edge:"):     # doc "nhu nguoi" — het deu deu robot
                    data, mime = expressive.synth_human(
                        current, voice, rate,
                        progress=self._prog(job))
                else:
                    data, mime, _ = tts.synth(
                        current, voice, rate,
                        progress=self._prog(job),
                        genre=(genre or job.get("auto_genre")) if keep_tags else None,
                        intensity=intensity if keep_tags else None)
                job.update(audio=data, mime=mime)
                vsim = vpros = None
                if voice.startswith("c:") and data[:4] == b"RIFF":
                    try:                         # ban doc giong giong da chon bao nhieu %
                        fd, tmpw = tempfile.mkstemp(suffix=".wav")
                        os.close(fd)
                        Path(tmpw).write_bytes(data)
                        nm = clone.safe_name(voice.split(":", 1)[1])
                        got = clone._voicesim(clone._sim_refs(nm), [tmpw])
                        vsim = round(got[0], 3) if got else None
                        vpros = clone.prosody_match(nm, wav_path=tmpw)   # nhip dieu giong ban goc
                        os.unlink(tmpw)
                    except Exception:
                        vsim = None
                job["result"] = {"translated": job.get("translated"),
                                 "mime": mime, "chars": len(current), "voice_sim": vsim,
                                 "prosody_match": vpros,
                                 "voice": voice,
                                 "auto_script": job.get("auto_script"),
                                 "auto_genre": job.get("auto_genre"), "auto_note": auto_note}
                self._finish(job)
            except Exception as e:
                self._finish(job, "Loi doc van ban: %s (dich/giong online can mang)" % e)

        threading.Thread(target=work, daemon=True).start()
        return job

    def start_annotate(self, text, target, genre=None):
        """AI bien kich chay NEN — van ban dai (den 100k) co tien do tung phan."""
        from . import expressive
        job = self._new("annotate", "%d ký tự" % len(text))

        def work():
            try:
                self._set_phase(job, "annotate")
                rep = {}
                out, note = expressive.auto_annotate(
                    text, target,
                    progress=self._prog(job), report=rep,
                    genre=genre)
                job["result"] = {"text": out, "note": note, "report": rep}
                self._finish(job)
            except Exception as e:
                self._finish(job, "Loi bien kich: %s" % e)

        threading.Thread(target=work, daemon=True).start()
        return job

    def start_clone_setup(self, force=False):
        """Tai model + vocab cho Voice Clone (co resume, tu kiem tra file)."""
        job = self._new("clone_setup", "tải model giọng nhân bản")
        lines = []

        def work():
            try:
                self._set_phase(job, "download")
                clone.ensure_assets(
                    progress=self._prog(job),
                    log=lambda m: (lines.append(m), job.update(log=lines[-6:])),
                    force=force)
                job["result"] = {"assets": clone.clone_assets.state()}
                self._finish(job)
            except Exception as e:
                self._finish(job, "Tai model nhan ban giong loi: %s" % e)

        threading.Thread(target=work, daemon=True).start()
        return job

    def start_clone_warm(self):
        """Nap san engine nhan ban giong vao RAM (lan long tieng dau se nhanh hon)."""
        job = self._new("clone_warm", "nạp engine nhân bản giọng")

        def work():
            try:
                self._set_phase(job, "model")
                job["total"] = 1
                info = clone.warm_up(log=lambda m: job.update(label=m[:60]))
                job.update(done=1, label="nạp engine nhân bản giọng",
                           result={"device": info.get("device"), "port": info.get("port")})
                self._finish(job)
            except Exception as e:
                self._finish(job, "Khong nap duoc engine nhan ban giong: %s" % e)

        threading.Thread(target=work, daemon=True).start()
        return job

    def start_clone_sample(self, name, text=None):
        """Tao lai file nghe thu cho 1 giong nhan ban."""
        job = self._new("clone_sample", "nghe thử giọng %s" % name)

        def work():
            try:
                self._set_phase(job, "tts")
                job["total"] = 1
                clone.make_sample(name, text)
                job.update(done=1)
                self._finish(job)
            except Exception as e:
                self._finish(job, "Khong tao duoc ban nghe thu: %s" % e)

        threading.Thread(target=work, daemon=True).start()
        return job

    def start_clone_styles(self, names, force=False):
        """Tu tao mau cam xuc (hype/calm) cho cac giong Anh con thieu — chay nen, co nhat ky."""
        job = self._new("clone_styles", "mẫu cảm xúc %d giọng" % len(names))
        job["log"] = []

        def say(msg):
            job["log"] = (job["log"] + [msg])[-200:]

        def work():
            try:
                self._set_phase(job, "styles")
                job["total"] = len(names)
                results = {}
                for i, n in enumerate(names):
                    say("== %s (%d/%d)" % (n, i + 1, len(names)))
                    try:
                        results[n] = clone.make_styles_auto(n, force=force, say=say)
                    except Exception as e:
                        results[n] = {"error": str(e)}
                        say("  lỗi: %s" % e)
                    job.update(done=i + 1)
                job["result"] = {"styles": results}
                self._finish(job)
            except Exception as e:
                self._finish(job, "Khong tao duoc mau cam xuc: %s" % e)

        threading.Thread(target=work, daemon=True).start()
        return job

    def start_preload(self, model_name):
        """Nap truoc model vao bo nho (tai ve neu chua co)."""
        job = self._new("preload", model_name)

        def work():
            try:
                self._set_phase(job, "model")
                engine.get_model(model_name)
                self._finish(job)
            except Exception as e:
                self._finish(job, "Khong nap duoc model: %s" % e)

        threading.Thread(target=work, daemon=True).start()
        return job
