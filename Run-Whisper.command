#!/bin/bash
# Chay thu ban whisper tren may Mac (bam dup). Code app/ dung chung, chi doi cau hinh editions/whisper/
cd "$(dirname "$0")"
echo "=== Chay ban: whisper ==="
WHISPER_EDITION=whisper python3 run.py --open
