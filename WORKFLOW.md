# WORKFLOW — Whisper STT hoạt động ra sao

## 0. Kiến trúc tổng thể

```
Trình duyệt (static/)  ──HTTP──▶  Tầng web (server.py)  ──gọi hàm──▶  Tầng lõi (core/)
index / admin / login             chỉ routing + phiên               engine · translate · tts
                                  đăng nhập, KHÔNG xử lý            · video · jobs · users
                                  nghiệp vụ

CLI (cli.py) ─────────────────────gọi thẳng────────────────────────▶  Tầng lõi (core/)
```

Mọi cấu hình (cổng, model, giọng, giới hạn) nằm ở một chỗ: `app/config.py`.

## 1. Đăng nhập & phân quyền

```
Người dùng mở bất kỳ trang nào
  └─ chưa có phiên (cookie) ──▶ đá về /login
       └─ POST /api/login {username, password}
            ├─ users.py so mật khẩu (băm PBKDF2, chống dò: sai chờ 1s)
            ├─ đúng ──▶ tạo token phiên (12h) ──▶ Set-Cookie
            └─ vai trò: user  = trang chính (phiên âm/dịch/lồng tiếng)
                        admin = thêm /admin (job, model, quản lý tài khoản)
```

Tài khoản lưu `users.json` (tự tạo `admin/admin123` lần đầu, hoặc seed từ
biến môi trường `WHISPER_USERS` cho môi trường bị reset ổ đĩa).

## 2. Nền tảng phiên âm (nằm trong pha transcribe của mọi job prepare)

- Whisper chạy **batched** (8 đoạn song song) + **VAD** bỏ khoảng lặng + đa luồng CPU.
- Từng segment xong là cập nhật tiến độ (giây đã xử lý / tổng giây) → UI vẽ % + ETA.
- **Điểm mấu chốt:** Whisper cho timestamp CHÍNH XÁC từng từ — words được gom thành
  các ĐOẠN (cue) theo câu/nhịp nghỉ, dùng cho cả phụ đề SRT lẫn căn khớp lồng tiếng.

## 3. LUỒNG CHÍNH 2 BƯỚC (từ v2.1 — đã bịt lỗ hổng căn thời gian)

```
BƯỚC 1 (bấm 1 lần): chọn file + ngôn ngữ đích + model → "Bắt đầu"
  └─ job "prepare":
       pha model      : nạp Whisper
       pha transcribe : phiên âm NGUYÊN VĂN ngôn ngữ gốc (timestamp từng từ)
       └─ gom words thành N ĐOẠN {start, end, câu}
       pha translate  : dịch TỪNG ĐOẠN (gộp 40 đoạn/request) — GIỮ timestamp
       └─ file gốc được giữ lại server cho bước 2 (không phải upload lại)
⏸ DỪNG — người dùng xem/sửa:
  tab "Bản dịch (sửa được)": 1 dòng = 1 đoạn, sửa chữ thoải mái (đừng thêm/xóa dòng)
  tab Transcript gốc / SRT gốc / SRT dịch — tải .txt, .srt bất kỳ lúc nào
BƯỚC 2 (bấm 1 lần): chọn giọng + tốc độ → "Tạo giọng + ghép video"
  └─ job "render":
       pha tts      : đọc TỪNG ĐOẠN (6 luồng song song)
       pha assemble : đặt từng clip vào ĐÚNG start gốc trên track trắng
                      ├─ clip dài hơn chỗ trống → ffmpeg nén tốc độ vừa khít
                      └─ chỗ nhạc/im lặng gốc → giữ im lặng
       pha mux      : ghép track đã căn vào video (copy hình, không re-encode)
KẾT QUẢ: video mp4 lồng tiếng + audio m4a riêng + SRT gốc + SRT dịch + text
```

Đã kiểm chứng bằng máy: câu gốc ở giây 0.0 / 11.7 / 22.7 → giọng lồng rơi đúng
0.0 / 12.1 / 22.9 (lệch < 0.5s), khoảng im lặng giữ nguyên.

## 4. NHÂN BẢN GIỌNG (voice clone) — đường đi của một giọng

```
CÀI (1 lần):  python3 setup_clone.py   ── hoặc /admin → "Tải model giọng Việt"
  ├─ engine : chọn PyTorch CUDA/CPU/MPS đúng máy (thử thật) + pip install f5-tts
  │           ghi nhớ đường dẫn Python của engine vào data/clone_python.txt
  └─ model  : data/f5_vi/{model.pt, vocab.txt}
               tải vào .part → RESUME khi đứt mạng → kiểm tra nội dung → rename
               sai/hỏng ⇒ xóa, nhảy mirror (hf-mirror.com), không bao giờ để file nửa vời

TẠO GIỌNG: /admin → upload mẫu
  ├─ ffmpeg : highpass 60Hz → bỏ im lặng → 24kHz mono s16 → cắt tối đa 12 giây
  ├─ script : người dùng gõ, HOẶC tool tự nghe bằng Whisper
  │           (mẫu bị cắt ⇒ BẮT BUỘC nghe lại, vì script cũ không còn khớp audio)
  └─ meta.json + sample.wav (bản nghe thử tạo nền, không chặn trang)

ĐỌC (mỗi lần lồng tiếng):
  tiến trình web ──HTTP 127.0.0.1──▶ clone_worker (tiến trình RIÊNG giữ model trong RAM)
    ├─ gửi từng đoạn kèm CHỈ SỐ GỐC + tốc độ riêng (thẻ cảm xúc → nhanh/chậm)
    ├─ worker ghi %04d.wav.part → rename: đếm file = tiến độ thật
    ├─ 1 đoạn lỗi ⇒ ghi vào `failed`, ĐỌC TIẾP các đoạn sau (không giết job)
    ├─ xong: đọc lại riêng các đoạn `failed`, hạ xuống CPU cho chắc
    └─ hậu kỳ từng đoạn (thuần Python): cắt im lặng → cân bằng âm lượng → fade 10ms
  KẾT QUẢ: list clip ĐÚNG SỐ ĐOẠN (đoạn trống/lỗi = b"" → assemble giữ im lặng đúng chỗ)
```

Vì sao tách tiến trình: model chiếm vài GB và có thể sập (hết VRAM, driver lỗi). Sập thì
server web vẫn sống, tool tự khởi động lại worker; cổng bị chiếm thì tự nhảy cổng.
Log: `data/logs/clone_worker.log`. Tắt/nạp engine chủ động trong /admin.

## 5. Các engine giọng khác

edge-tts (online, nhanh nhất) · VieNeu (local) · Gemini TTS / persona (online, giàu cảm xúc)
· ElevenLabs (tùy chọn, có key) · `say:` (offline macOS). Hết quota hoặc mất mạng thì
chuỗi dự phòng trong `app/config.py` tự đổi sang giọng gần nhất, không bao giờ tắc tiếng.

## 6. Hệ thống JOB NỀN (xương sống của web)

- Mọi việc nặng chạy thread riêng, UI chỉ poll `/api/job?id=` mỗi giây.
- Job dict: `{state, phase, done, total, result, audio/video, error}` —
  mỗi pha reset bộ đếm, UI tự tính ETA từ tốc độ thực tế.
- Giữ tối đa 50 job gần nhất; xóa job = giải phóng RAM/file video kèm theo.
- Mạng chập chờn không giết job — server vẫn chạy tiếp, F5 vẫn poll lại được.

## 7. Trang QUẢN TRỊ (/admin — cần vai trò admin)

- Trạng thái: phiên bản, uptime, thống kê job đang chạy/xong/lỗi.
- Model: đã tải về đĩa? đã nạp RAM? + nút tải/nạp trước từng model.
- Tài khoản: tạo/xóa user (chặn tự xóa mình, chặn xóa admin cuối).
- Job: bảng realtime, nghe lại audio, xóa job.
