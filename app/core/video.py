# -*- coding: utf-8 -*-
"""Ghep giong doc vao video (mux) bang ffmpeg dong goi san trong imageio-ffmpeg.

Video giu nguyen hinh (stream copy, khong re-encode), chi thay track am thanh
bang giong doc moi (AAC). Do dai output = do dai video goc.
"""

import subprocess
from pathlib import Path


def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def duration(path):
    """Do dai media (giay) — dung PyAV (co san tu faster-whisper)."""
    import av
    with av.open(str(path)) as c:
        if c.duration:
            return c.duration / av.time_base
    return 0.0


def is_video(path):
    """Co stream hinh that khong (loai audio thuan/anh bia mp3)."""
    import av
    try:
        with av.open(str(path)) as c:
            return any(s.type == "video" and (s.frames or 0) != 1 for s in c.streams)
    except Exception:
        return False


SR = 24000  # sample rate lap rap track giong doc (khop voi edge-tts)


def _decode_pcm(audio_bytes, tempo=1.0):
    """mp3/m4a bytes -> PCM s16le mono 24kHz; tempo>1 = doc nhanh lai bay nhieu lan."""
    args = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", "pipe:0"]
    if tempo > 1.01:
        t, filters = min(tempo, 4.0), []
        while t > 2.0:
            filters.append("atempo=2.0")
            t /= 2.0
        filters.append("atempo=%.4f" % t)
        args += ["-filter:a", ",".join(filters)]
    args += ["-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"]
    r = subprocess.run(args, input=audio_bytes, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg decode loi: %s" % r.stderr.decode()[-200:])
    return r.stdout


def assemble_aligned(cues, clips, total_seconds, out_path, progress=None):
    """Dat tung clip giong vao DUNG thoi diem goc tren track trang dai bang video.

    - Clip dai hon cho trong (tinh den dau doan ke tiep) -> tu nen toc do vua khit.
    - Cho khong loi (nhac/im lang goc) -> giu im lang.
    Ghi ra out_path (.m4a AAC) va tra ve bytes cua no.
    """
    from concurrent.futures import ThreadPoolExecutor

    total = max(int(total_seconds * SR), SR)
    buf = bytearray(total * 2)
    n = len(cues)
    done = [0]

    def decode_fit(i):
        """Giai ma 1 clip (+nen toc do neu tran cho) — chay song song cho nhanh."""
        clip = clips[i]
        if not clip:
            pcm = b""
        else:
            cue = cues[i]
            slot_end = cues[i + 1]["start"] if i + 1 < n else total_seconds
            avail = max(0.4, slot_end - cue["start"])
            pcm = _decode_pcm(clip)
            dur = len(pcm) / 2.0 / SR
            if dur > avail * 1.03:  # tran cho -> nen toc do
                pcm = _decode_pcm(clip, tempo=dur / avail)
        done[0] += 1
        if progress:
            progress(done[0], n)
        return pcm

    with ThreadPoolExecutor(max_workers=4) as ex:
        pcms = list(ex.map(decode_fit, range(n)))

    for i, pcm in enumerate(pcms):
        if not pcm:
            continue
        off = int(cues[i]["start"] * SR) * 2
        end = min(len(buf), off + len(pcm))
        if off < len(buf):
            buf[off:end] = pcm[:end - off]
    r = subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "s16le", "-ac", "1", "-ar", str(SR), "-i", "pipe:0",
         "-c:a", "aac", "-b:a", "96k", str(out_path)],
        input=bytes(buf), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg encode loi: %s" % r.stderr.decode()[-200:])
    return Path(out_path).read_bytes()


def mux(video_path, audio_path, out_path):
    """Thay track am thanh cua video bang audio_path. Tra ve (video_s, audio_s)."""
    vid_s, aud_s = duration(video_path), duration(audio_path)
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path), "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-t", "%.3f" % max(vid_s, 0.1),
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg loi: %s" % (r.stderr or "").strip()[-300:])
    return vid_s, aud_s
