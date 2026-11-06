#!/bin/bash
# Chay thu ban starlight tren may Mac (bam dup). Code app/ dung chung, chi doi cau hinh editions/starlight/
cd "$(dirname "$0")"
echo "=== Chay ban: starlight ==="
WHISPER_EDITION=starlight python3 run.py --open
