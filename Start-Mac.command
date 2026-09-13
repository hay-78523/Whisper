#!/bin/bash
# Khoi dong Whisper STT tren macOS — nhay dup chuot vao file nay la chay.
# Lan dau se tu cai thu vien (can mang, ~vai phut). Cac lan sau vao thang.
cd "$(dirname "$0")"
echo "=== Whisper STT ==="
# Uu tien Python he thong cua macOS (tranh dung nham python brew thieu package)
if [ -x /usr/bin/python3 ]; then PY=/usr/bin/python3; else PY=python3; fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "Chua co Python. Vao https://www.python.org/downloads/ tai va cai Python 3.11+ roi chay lai."
  read -p "Nhan Enter de thoat..."
  exit 1
fi
echo "Kiem tra / cai thu vien (lan dau hoi lau)..."
"$PY" -m pip install -q --user -r requirements.txt || {
  echo "Cai thu vien loi — kiem tra mang roi chay lai."; read -p "Enter de thoat..."; exit 1; }

echo "Kiem tra du lieu cho Voice Clone..."
mkdir -p "./data/f5_vi"
if [ ! -f "./data/f5_vi/vocab.txt" ]; then
    echo "Dang tai vocab.txt..."
    curl -L -o "./data/f5_vi/vocab.txt" https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice/resolve/main/config.json
fi
if [ ! -f "./data/f5_vi/model.pt" ]; then
    echo "Dang tai model_last.pt khoang 1.3GB - vui long doi chut..."
    curl -L -o "./data/f5_vi/model.pt" https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice/resolve/main/model_last.pt
fi

echo "Kiem tra F5-TTS va PyTorch tren he thong..."
"$PY" -c "import torch" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Dang cai dat PyTorch (Mac MPS/CPU)..."
    "$PY" -m pip install torch torchvision torchaudio
fi

"$PY" -c "import f5_tts" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Dang cai dat F5-TTS..."
    "$PY" -m pip install f5-tts
fi

echo "Khoi dong server... Trinh duyet se tu mo. Dong cua so nay la TAT ung dung."
"$PY" run.py --open
