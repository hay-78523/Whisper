#!/bin/bash
# Chay thu ban starlight_test tren may Mac (bam dup). Code app/ dung chung, chi doi cau hinh editions/starlight_test/
cd "$(dirname "$0")"
echo "=== Chay ban: starlight_test ==="
WHISPER_EDITION=starlight_test python3 run.py --open
