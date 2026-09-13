---
title: Whisper STT
emoji: 🎙️
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# whisper_stt — Phiên âm, dịch & lồng tiếng audio. Miễn phí, chạy trên máy.

Đưa audio/video vào → **transcript** (`.txt`/`.srt`/`.json`) → **dịch** sang 8 thứ tiếng → **lồng tiếng** bằng giọng neural bản xứ. Không API key, không tốn phí:

- Phiên âm: **Whisper** (`faster-whisper`, chạy local, batched + VAD + đa luồng CPU)
- Dịch: Google Translate (miễn phí, cần mạng)
- Giọng đọc: **edge-tts** (neural, cần mạng) hoặc `say:Linh` (offline macOS)

## Cấu trúc

```
whisper_stt/
├── run.py              # khởi động web server
├── cli.py              # CLI: transcribe / dub / voices
├── requirements.txt
├── users.json          # tài khoản (tự tạo lần đầu, KHÔNG commit — đã gitignore)
└── app/
    ├── config.py       # MỌI cấu hình nằm ở đây (cổng, model, giọng, giới hạn)
    ├── core/           # tầng lõi — business logic, không dính web
    │   ├── engine.py      # Whisper: nạp model, phiên âm, SRT
    │   ├── tts.py         # tạo giọng đọc (edge / say), cắt khúc + song song
    │   ├── translate.py   # dịch máy theo khúc
    │   ├── users.py       # tài khoản: PBKDF2, vai trò user/admin
    │   └── jobs.py        # JobManager: job nền + tiến độ + dọn dẹp
    └── web/            # tầng web — chỉ routing HTTP + phiên đăng nhập
        ├── server.py
        └── static/        # tầng giao diện (CSS tách riêng để dễ debug)
            ├── style.css     # toàn bộ style dùng chung 3 trang
            ├── login.html    # trang đăng nhập
            ├── index.html    # trang chính (cần đăng nhập)
            └── admin.html    # trang quản trị (cần vai trò admin)
```

## Đăng nhập & phân quyền

Mọi trang đều yêu cầu đăng nhập. Hai vai trò:

- **user** — dùng trang chính: phiên âm, dịch, lồng tiếng
- **admin** — thêm trang `/admin`: xem job, quản lý model, **tạo/xóa tài khoản**

Lần chạy đầu tự tạo tài khoản **admin / admin123** — đăng nhập rồi **đổi mật khẩu ngay** (tạo admin mới trong /admin rồi xóa admin cũ, hoặc dùng API `user_password`). Mật khẩu băm PBKDF2 trong `users.json`; không thể xóa admin cuối cùng hoặc tự xóa chính mình.

## Cài đặt (1 lần)

```bash
pip3 install --user -r requirements.txt
```

Lần đầu dùng một model sẽ tự tải về (turbo ~1.6GB, small ~460MB), các lần sau chạy offline.

## Web UI

```bash
python3 run.py
```

- **Trang chính** <http://localhost:8765>: kéo thả file → phiên âm (thanh tiến trình % + giờ xong dự kiến) → xem Transcript / Phụ đề SRT → khối **Lồng tiếng đa ngôn ngữ**: transcript khác tiếng Việt sẽ tự đề xuất dịch sang tiếng Việt + giọng khớp, có cảnh báo khi giọng lệch ngôn ngữ → nghe ngay + tải file. File gốc là **video** thì sau khi tạo giọng sẽ hiện nút **🎬 Ghép giọng vào video gốc** — giữ nguyên hình (không re-encode), thay track tiếng, xem và tải mp4 ngay trên trang; có báo lệch thời lượng giọng/video kèm gợi ý chỉnh tốc độ đọc.
- **Trang quản trị** <http://localhost:8765/admin>: trạng thái server (phiên bản, uptime, thống kê job), model nào đã tải/đã nạp RAM (kèm nút nạp trước), danh sách job realtime (tiến độ, thời gian chạy, nghe lại audio, xóa job).

Tùy chọn: `--port 9000`, `--preload large-v3-turbo` (nạp model ngay khi khởi động), `--host 0.0.0.0` (cho máy khác trong mạng LAN truy cập — chú ý: chưa có đăng nhập, chỉ mở trong mạng tin cậy).

## CLI

```bash
# Phiên âm 1 file hoặc cả thư mục
python3 cli.py transcribe audio.mp3 --language vi --srt
python3 cli.py transcribe ./folder_audio --recursive

# Dịch lồng tiếng trọn gói: video tiếng Anh -> giọng Việt
python3 cli.py dub --audio video.mp4 --translate vi -o long_tieng.mp3

# Như trên + GHÉP LUÔN GIỌNG VÀO VIDEO (giữ nguyên hình, thay track tiếng)
python3 cli.py dub --audio video.mp4 --translate vi -o giong.mp3 --video-out video_long_tieng.mp4

# Đọc văn bản / file text, đổi giọng, đổi tốc độ
python3 cli.py dub --text "Xin chào" -o chao.mp3
python3 cli.py dub --file transcript.txt --voice edge:ja-JP-NanamiNeural --rate +20% -o ja.mp3

# Xem giọng có sẵn
python3 cli.py voices
```

## Chọn model

| Model | Dung lượng | Ghi chú |
|---|---|---|
| **large-v3-turbo** | ~1.6GB | **mặc định** — chính xác cao, tốc độ tốt (36s audio ≈ 15s xử lý trên M-series) |
| small | ~460MB | máy yếu / ổ đĩa hẹp |
| tiny / base | 75–140MB | test nhanh, kém chính xác |
| large-v3 | ~3GB | chính xác tối đa, rất chậm |

## Dịch "xịn" bằng AI (tùy chọn — nâng bản dịch lên mức lồng tiếng chuyên nghiệp)

Mặc định tool dịch bằng Google Translate (miễn phí, không key). Muốn bản dịch tự nhiên
hơn hẳn (văn nói bản xứ, tự lược từ đệm, xưng hô nhất quán, hiểu ngữ cảnh):

1. Vào <https://aistudio.google.com> → đăng nhập Google → **Get API key** (miễn phí, không cần thẻ).
2. Mở `app/config.py`, dán key vào `GEMINI_API_KEY = "..."` (hoặc đặt biến môi trường `GEMINI_API_KEY`).
3. Chạy lại — kết quả phiên âm sẽ hiện nhãn **AI (Gemini)** thay vì Google.

Key lỗi/hết hạn mức thì tool tự rơi về Google Translate, không bao giờ gãy.
Không cần key thì mọi thứ vẫn chạy như cũ. Transcript cũng được tự lược từ đệm
văn nói (um, you know, like…) trước khi dịch — kể cả khi dùng Google.

### Hết quota Gemini? Có 2 nguồn AI dự phòng (tool tự nhảy nguồn)

Dịch AI và ✨ AI biên kịch chạy theo **chuỗi dự phòng**: Gemini (xoay hết các key
trong `GEMINI_API_KEYS`) → Groq → Ollama. Nguồn nào nghẽn/hết quota là tự nhảy
nguồn kế, không phải làm gì cả. Kích hoạt nguồn dự phòng (chọn 1 hoặc cả 2):

- **Groq** — key free KHÔNG cần thẻ tại <https://console.groq.com> → API Keys,
  dán vào `GROQ_API_KEY` trong `app/config.py`. Hạn ~14.400 lượt/ngày, gần như
  không bao giờ đụng trần.
- **Ollama** — AI chạy **ngay trên máy, vô hạn, không cần mạng**: cài từ
  <https://ollama.com>, rồi chạy `ollama pull qwen2.5:7b` (~4,7GB, 1 lần).
  Tool tự phát hiện khi Ollama đang mở, không cần cấu hình gì.

Riêng **giọng đọc Gemini TTS** (nhóm ⭐) là dịch vụ riêng chỉ Gemini có — hết
quota thì đợi reset (nửa đêm giờ Mỹ) hoặc thêm key vào `GEMINI_API_KEYS`.

## Voice Cloning — nhân bản giọng (tùy chọn, chạy local, miễn phí)

Trang /admin sẽ hiện mục **Giọng nhân bản** khi máy đã cài engine. Tải lên mẫu giọng
5–15 giây + gõ transcript của mẫu → giọng `🎤 tên` xuất hiện trong mọi dropdown giọng đọc
(đọc văn bản + lồng video). Cài engine (1 lần, ~7GB, cần Python ≥3.10):

```bash
# macOS (Windows tương tự với py -3.12 -m venv)
brew install python@3.12 ffmpeg
python3.12 -m venv "$HOME/Library/Application Support/whisper_stt/f5env"
"$HOME/Library/Application Support/whisper_stt/f5env/bin/pip" install f5-tts
# tải model tiếng Việt (ViVoice 1000h):
D="$HOME/Library/Application Support/whisper_stt/f5_vi"; mkdir -p "$D"
curl -L -o "$D/vocab.txt" https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice/resolve/main/config.json
curl -L -o "$D/model.pt"  https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice/resolve/main/model_last.pt
```

Tốc độ: ~6x chậm hơn thời gian thực (video 20 phút ≈ 1,5–2 giờ) — hợp văn bản ngắn/intro;
video dài nên dùng giọng edge. **Điều khoản**: model CC-BY-NC-SA (phi thương mại), chỉ clone
giọng của mình/người đã đồng ý, ghi rõ audio do AI tạo khi đăng công khai.

## Ghi chú vận hành

- Job giữ trong RAM (tối đa 50 job gần nhất — chỉnh trong `app/config.py`); audio lồng tiếng nằm trong job, xóa job là giải phóng RAM.
- Transcript rất dài (>60k ký tự) web sẽ từ chối lồng tiếng — dùng CLI.
- Muốn đổi cổng/giọng/giới hạn mặc định: sửa một chỗ duy nhất `app/config.py`.
