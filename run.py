#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Khoi dong web server.

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()eload large-v3-turbo
"""

import os
import sys

# Ensure local ffmpeg is in PATH for torchcodec and pydub
ffmpeg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg-master-latest-win64-gpl-shared", "bin")
if os.path.exists(ffmpeg_path):
    os.environ["PATH"] = ffmpeg_path + os.pathsep + os.environ["PATH"]
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(ffmpeg_path)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.web.server import main

if __name__ == "__main__":
    main()
