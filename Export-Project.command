#!/bin/bash
cd "$(dirname "$0")"
echo "=== Dọn Dẹp & Đóng Gói Dự Án ==="

# Xóa các file rác và bộ nhớ tạm
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
find . -type f -name "*.log" -delete

# Tên file xuất ra
ZIP_NAME="Whisper_STT_ReadyToShare.zip"
rm -f "$ZIP_NAME"

echo "Đang nén dữ liệu (bao gồm cả AI Model và 36 giọng clone)..."
# Zip toàn bộ thư mục, nhưng loại trừ môi trường ảo f5env vì môi trường ảo không chạy chéo máy được.
# Người dùng khác chỉ cần chạy Setup-VoiceClone-Mac.command để tạo lại f5env.
zip -q -r "$ZIP_NAME" . -x "*.git*" -x "*f5env*" -x "*scratch*" -x ".DS_Store" -x "*.zip"

echo ""
echo "=== HOÀN TẤT ==="
echo "Đã đóng gói dự án thành file: $ZIP_NAME"
echo "Bạn có thể gửi file zip này cho người khác. Khi họ giải nén ra, họ sẽ có toàn bộ 36 giọng AI đã được setup sẵn."
