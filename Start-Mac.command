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

echo "Kiem tra Voice Clone (engine + model giong Viet)..."
if ! "$PY" setup_clone.py --check >/dev/null 2>&1; then
  echo "Chua day du — dang tu dong cai dat."
  echo "Lan dau tai PyTorch + model giong (~2-4GB), mat 10-25 phut tuy mang."
  echo "Tai bi dut cung khong sao: chay lai la tiep tuc tu cho dang do."
  "$PY" setup_clone.py --no-test || {
    echo "[!] Voice Clone chua hoan tat — tool VAN CHAY duoc voi cac giong khac."
    echo "[!] Vao trang /admin bam 'Tai model giong Viet', hoac chay lai:"
    echo "[!]     python3 setup_clone.py"; }
fi

echo "Khoi dong server... Trinh duyet se tu mo. Dong cua so nay la TAT ung dung."
"$PY" run.py --open
