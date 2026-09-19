# -*- coding: utf-8 -*-
"""Quan ly job chay nen: tao / theo doi tien do / don dep.

Moi viec nang (phien am, dich, tao giong) chay trong thread rieng va cap nhat
tien do vao job dict de tang web tra ve cho UI poll.
"""

import os
import tempfile
import threading
import time
from pathlib import Path

from ..config import JOB_KEEP
from . import clone, engine, story, translate, tts, video


class JobManager(object):
    def __init__(self, keep=JOB_KEEP):
        self._jobs = {}
        self._lock = threading.Lock()
        self._next = 0
        self._keep = keep

    # ---------- vong doi job ----------

    def _new(self, kind, label):
        with self._lock:
            self._next += 1
            job = {"id": str(self._next), "kind": kind, "label": label,
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
        job.update(phase=phase, done=0, total=0, phase_started=time.time())

    @staticmethod
    def _finish(job, error=None):
        job["finished"] = time.time()
        if error:
            job.update(state="error", error=error)
        else:
            job["state"] = "done"

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
            out.append({
                "id": job["id"], "kind": job["kind"], "label": job["label"],
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
                "error": states.count("error")}

    # ---------- cac loai job ----------

    def start_prepare(self, media_tmp, filename, model_name, language, target):
        """Buoc 1 tron goi: phien am -> cat doan giu timestamp -> dich tung doan.

        File goc duoc GIU LAI (job["media_path"]) de buoc render ghep video
        khong phai upload lai; job prepare cu hon bi don file goc.
        """
        job = self._new("prepare", filename)
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
                        progress=lambda d, t: job.update(done=d, total=t))
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

    def start_render(self, prepare_job_id, texts, voice, rate):
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
        job = self._new("render", "%d đoạn" % len(cues))

        def work():
            out_audio = tempfile.mkstemp(suffix=".m4a")[1]
            out_video = tempfile.mkstemp(suffix=".mp4")[1] if is_video else None
            try:
                self._set_phase(job, "tts")
                clips = tts.synth_cues(
                    final_texts, voice, rate,
                    progress=lambda d, t: job.update(done=d, total=t))
                self._set_phase(job, "assemble")
                audio_bytes = video.assemble_aligned(
                    cues, clips, total_s, out_audio,
                    progress=lambda d, t: job.update(done=d, total=t))
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

    def start_text_dub(self, text, voice, rate, target):
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
            " · biểu cảm" if (use_expr or use_acted or has_multi) else ""))

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
                        progress=lambda d, t: job.update(done=d, total=t))
                    job.update(audio=data, mime=mime)
                    job["result"] = {"translated": None, "mime": mime,
                                     "chars": len(text), "expressive": True}
                    self._finish(job)
                    return
                if use_expr:
                    self._set_phase(job, "tts")
                    data, mime = expressive.synth_expressive(
                        text, voice, rate, target=target,
                        progress=lambda d, t: job.update(done=d, total=t))
                    job.update(audio=data, mime=mime)
                    job["result"] = {"translated": None, "mime": mime,
                                     "chars": len(text), "expressive": True}
                    self._finish(job)
                    return
                current = expressive.strip_tags(text)
                if target:
                    self._set_phase(job, "translate")
                    current = translate.translate_text(
                        current, target,
                        progress=lambda d, t: job.update(done=d, total=t))
                    job["translated"] = current
                self._set_phase(job, "tts")
                if voice.startswith("edge:"):     # doc "nhu nguoi" — het deu deu robot
                    data, mime = expressive.synth_human(
                        current, voice, rate,
                        progress=lambda d, t: job.update(done=d, total=t))
                else:
                    data, mime, _ = tts.synth(
                        current, voice, rate,
                        progress=lambda d, t: job.update(done=d, total=t))
                job.update(audio=data, mime=mime)
                job["result"] = {"translated": job.get("translated"),
                                 "mime": mime, "chars": len(current)}
                self._finish(job)
            except Exception as e:
                self._finish(job, "Loi doc van ban: %s (dich/giong online can mang)" % e)

        threading.Thread(target=work, daemon=True).start()
        return job

    def start_annotate(self, text, target):
        """AI bien kich chay NEN — van ban dai (den 100k) co tien do tung phan."""
        from . import expressive
        job = self._new("annotate", "%d ký tự" % len(text))

        def work():
            try:
                self._set_phase(job, "annotate")
                out, note = expressive.auto_annotate(
                    text, target,
                    progress=lambda d, t: job.update(done=d, total=t))
                job["result"] = {"text": out, "note": note}
                self._finish(job)
            except Exception as e:
                self._finish(job, "Loi bien kich: %s" % e)

        threading.Thread(target=work, daemon=True).start()
        return job

    def start_story_video(self, text, voice, rate, target, opts=None):
        """Dung video tu ANH: doc kich ban -> do dai tung doan -> trai anh -> mp4."""
        opts = opts or {}
        shots = story.plan(text, voice)
        if not shots:
            raise ValueError("Kịch bản trống.")
        have = [sc for sc in story.list_scenes() if sc["count"]]
        if not have:
            raise ValueError("Chưa có ảnh nào — tạo cảnh và tải ảnh lên trước đã.")
        used = story.scenes_used(shots)
        missing = [sc for sc in used if not story.scene_images(sc)]
        job = self._new("story", "%d đoạn · %d cảnh" % (len(shots), len(used) or 1))
        lines = []

        def note(m):
            lines.append(m)
            job.update(log=lines[-6:])

        if missing:
            note("Cảnh chưa có ảnh (%s) — tạm dùng ảnh của cảnh khác."
                 % ", ".join(missing[:4]))

        def work():
            out_video = tempfile.mkstemp(suffix=".mp4")[1]
            try:
                self._set_phase(job, "tts")
                pcm, shots2 = story.narrate(
                    shots, rate=rate, target=target, gap=opts.get("gap"),
                    progress=lambda d, t: job.update(done=d, total=t))
                self._set_phase(job, "images")
                slot_list = story.slots(shots2, angle_every=opts.get("angle_every"))
                note("%d đoạn lời → %d khung hình" % (len(shots2), len(slot_list)))
                self._set_phase(job, "render")
                story.render(slot_list, pcm, out_video,
                             ratio=opts.get("ratio") or "16:9",
                             motion=opts.get("motion") or "cut",
                             transition=float(opts.get("transition") or 0),
                             progress=lambda d, t: job.update(done=d, total=t),
                             log=note)
                job.update(video_path=out_video, mime="video/mp4",
                           audio=story.encode_audio(pcm))
                job["result"] = {
                    "shots": len(shots2), "slots": len(slot_list),
                    "seconds": round(sum(x["seconds"] for x in slot_list), 1),
                    "scenes": used, "missing": missing,
                    "srt": story.srt(shots2), "is_video": True,
                }
                self._finish(job)
            except Exception as e:
                try:
                    os.unlink(out_video)
                except OSError:
                    pass
                self._finish(job, "Loi dung video tu anh: %s" % e)

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
                    progress=lambda d, t: job.update(done=d, total=t),
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
