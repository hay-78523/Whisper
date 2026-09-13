# -*- coding: utf-8 -*-
"""Worker VieNeu-TTS — chay BEN TRONG venv vieneu_env (Python >=3.10).

Daemon thuong truc: nap model MOT lan, nhan lenh JSON tung dong qua stdin,
tra loi qua stdout. Server giu process nay song xuyen cac job.

Lenh:  {"texts": [...], "voice": "Adam" | null, "ref_audio": path|null, "out_dir": ...}
Tra:   {"progress": n, "total": m} ... roi {"done": [duong dan wav...]}  hoac {"error": ...}
"""

import json
import os
import sys

# tranh import nham module cua tool (thu muc nay nam dau sys.path)
_here = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if os.path.abspath(p or ".") != _here]


def main():
    from vieneu import Vieneu
    precision = sys.argv[1] if len(sys.argv) > 1 else "int8"
    tts = Vieneu(precision=precision) if precision != "int8" else Vieneu()
    print(json.dumps({"ready": True}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            outs = []
            texts = req["texts"]
            for i, t in enumerate(texts):
                t = (t or "").strip()
                if not t:
                    outs.append("")
                else:
                    if req.get("ref_audio"):
                        audio = tts.infer(t, ref_audio=req["ref_audio"], denoise=True)
                    else:
                        audio = tts.infer(t, voice=req.get("voice") or "Adam")
                    p = "%s/%04d.wav" % (req["out_dir"], i + 1)
                    tts.save(audio, p)
                    outs.append(p)
                print(json.dumps({"progress": i + 1, "total": len(texts)}), flush=True)
            print(json.dumps({"done": outs}), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)[:300]}), flush=True)


if __name__ == "__main__":
    main()
