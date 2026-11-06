# HỒ SƠ BÀN GIAO & ĐẶC TẢ NHIỆM VỤ DỰ ÁN (PROJECT HANDOVER SPEC FOR AGENT)
> **Dự án:** Whisper STT & Voice Clone Studio (Bản Doanh nghiệp - Enterprise Edition)  
> **Ngôn ngữ & Nền tảng:** Python 3.11+, PyTorch (CUDA / MPS / CPU), F5-TTS, Chatterbox, Whisper, Ollama, Threading Web Server.  
> **Mục tiêu tài liệu:** Cung cấp đầy đủ bối cảnh, cấu trúc mã nguồn, các phần việc đã hoàn thành và danh sách các đầu việc kỹ thuật cần triển khai tiếp theo cho AI Agent nhận nhiệm vụ.

---

## 1. TỔNG QUAN HỆ THỐNG & BỐI CẢNH DỰ ÁN (CONTEXT)

Hệ thống là giải pháp **sản xuất âm thanh số On-premise (chạy hoàn toàn cục bộ, không gửi dữ liệu ra ngoài)**, bao gồm 3 chức năng chính:
1. **Speech-to-Text (STT):** Nhận dạng lời thoại, bóc băng tự động bằng mô hình Whisper (`large-v3-turbo` / `small`).
2. **Voice Clone (Nhân bản giọng nói):** 
   - Tiếng Việt: Mô hình **F5-TTS ViVoice** (1.000 giờ huấn luyện, ~1.35GB trọng số EMA).
   - Tiếng Anh: Mô hình **Chatterbox** (tối ưu hóa cảm xúc đa chiều trên GPU).
3. **AI Biên kịch (Script Annotation):** Tự động phân tích kịch bản văn bản và gán thẻ cảm xúc/nhịp điệu (`[vui]`, `[buồn]`, `[hào hứng]`, `[kịch tính]`, `[nghỉ]`, v.v.) bằng mô hình ngôn ngữ chạy cục bộ (Ollama).

### Triết lý triển khai (Deployment Philosophy):
- **Windows Portable (Zero-Install):** Đóng gói trọn gói Python 3.11, CUDA, FFmpeg và model vào một thư mục. Người dùng chỉ cần nhấp đúp `Start-Windows.bat` là chạy.
- **Mô hình Doanh nghiệp (Client-Server):** 
  - Máy chủ (Host có GPU): Chạy tác vụ AI nặng và Web Server (port 8765).
  - Máy trạm nhân viên (Clients): Kết nối qua mạng LAN/VPN bằng trình duyệt, chỉ được dùng giọng đã duyệt, **bị khóa cứng tính năng thêm/xóa giọng (Host-Only Security)**.

---

## 2. CÁC HẠNG MỤC ĐÃ HOÀN THÀNH (STATUS QUO)

Agent tiếp theo **KHÔNG CẦN LÀM LẠI** các phần sau (đã kiểm thử và chạy ổn định):
1. **Lọc tạp âm & nhạc nền tệp mẫu (`app/core/refpick.py`):**
   - Thuật toán đo tỷ số SNR thích nghi (Adaptive SNR Filter), tự động chọn đoạn sạch nhất từ 4.8s – 10.5s từ audio/video của khách, triệt tiêu tiếng nhạc nền BGM dính vào giọng clone.
2. **Kiểm soát nhịp độ phát âm (`app/core/clone_worker.py` v2.6):**
   - Khống chế tốc độ đọc tiếng Việt chuẩn xác **0.18s – 0.21s / từ** (khoảng 280–330 từ/phút).
   - Cố định khoảng lặng tự nhiên: dấu phẩy `~0.15s`, dấu chấm `~0.24s`. Đã loại bỏ hoàn toàn hiện tượng nuốt âm ở đuôi câu hoặc ngắt câu vô lý.
3. **Cơ chế bảo mật máy chủ (`app/web/server.py`):**
   - Hàm `_is_host_request()` và `_require_host_admin()` kiểm tra IP Socket và Loopback. Chỉ cho phép thêm/xóa/duyệt hồ sơ giọng clone khi thao tác trực tiếp trên máy chủ.
4. **Hệ thống thương hiệu Doanh nghiệp (`editions/starlight/`):**
   - Cơ chế nạp overlay `_edition_load()` và `_brand_html()`. Kích hoạt bằng file `editions/ACTIVE`. Tự động chèn logo, brand.css, brand.js lên giao diện.
5. **Gói kiểm thử đã xuất xưởng:**
   - Đã đóng gói `BAN_GIAO_KHACH/BanTest_VoiceViet_WinGPU.zip` (7.3 GB) phục vụ đợt test GPU cho đối tác (Phạm Nhật Đan).

---

## 3. BẢN ĐỒ CÁC TỆP TIN TRỌNG TÂM (CODEBASE MAP)

```text
whisper_stt/
├── app/
│   ├── config.py             # Cấu hình tập trung (model, port, đường dẫn, danh mục giọng)
│   ├── core/
│   │   ├── expressive.py     # Lõi AI biên kịch, xử lý thẻ cảm xúc, âm học F5-TTS
│   │   ├── translate.py      # Bộ điều phối gọi LLM (Ollama / Groq / Gemini)
│   │   ├── clone.py          # Quản lý hồ sơ giọng, giám sát worker, tạo preview sample
│   │   ├── clone_worker.py   # Tiến trình ngầm HTTP giữ model F5-TTS trong RAM (v2.6)
│   │   ├── refpick.py        # Thuật toán SNR bóc tách đoạn mẫu âm thanh sạch nhất
│   │   └── engine.py         # Tải và chạy mô hình Whisper STT
│   └── web/
│       ├── server.py         # ThreadingHTTPServer, API routes, Host-Only Auth, Edition overlay
│       └── static/           # Giao diện Web UI gốc (index.html, admin.html, login.html)
├── editions/
│   ├── ACTIVE                # Chứa chuỗi định danh edition đang chạy (vd: "starlight")
│   └── starlight/            # Bộ nhận diện thương hiệu Enterprise (edition.json, static/brand.css, brand.js)
├── setup_ai.py               # Script tự động tải và kích hoạt Ollama local
├── run.py                    # Entrypoint khởi động server chính
└── BAN_GIAO_KHACH/           # Thư mục chứa gói bàn giao, hồ sơ tài liệu ISO và sơ đồ
```

---

## 4. DANH SÁCH NHIỆM VỤ CẦN TRIỂN KHAI (BACKLOG TASKS)

Đây là các nhiệm vụ cốt lõi người dùng yêu cầu thực hiện để nâng cao chất lượng sản phẩm (đặc biệt theo **Option B**: nâng cấp AI biên kịch + làm giàu biểu cảm tự nhiên như người thật).

---

### NHIỆM VỤ 1: NÂNG CẤP AI BIÊN KỊCH THÔNG MINH (OPTION B)

#### Vấn đề hiện tại:
- Hiện đang cấu hình mặc định chạy `qwen2.5:3b`. Model 3B hiểu ngữ cảnh tiếng Việt hạn chế, gán thẻ cảm xúc bị nông hoặc sai lệch.
- Hàm `_annotate_chunk_fast()` trong `app/core/expressive.py` chia kịch bản thành từng lô 12 câu riêng rẽ để gửi cho LLM. AI bị "mù ngữ cảnh", không biết tổng thể bài viết là thể loại gì (quảng cáo, tâm sự hay truyện trinh thám).
- Prompt liệt kê hơn 35 thẻ khiến model nhỏ bị quá tải định dạng.

#### Yêu cầu kỹ thuật cho Agent:
1. **Nâng cấp Model mặc định:**
   - Trong `app/config.py` và `setup_ai.py`: Đổi `OLLAMA_MODEL` mặc định từ `qwen2.5:3b` sang **`qwen2.5:7b`** (hoặc `qwen2.5:7b-instruct-q4_K_M` ~4.7GB).
   - Đảm bảo lệnh `setup_ai.py` kiểm tra: nếu chưa có bản 7b thì tự động gọi `ollama pull qwen2.5:7b`.
2. **Tái cấu trúc Pipeline biên kịch 2 bước (2-Pass Pipeline):**
   - **Bước 1 (Macro Analysis - Đọc toàn bài):** Gửi trích đoạn/toàn bộ kịch bản cho LLM xác định:
     * *Thể loại:* `quang_cao` (quảng cáo), `ke_chuyen` (kể chuyện/audiobook), `tin_tuc` (tin tức/bình luận), `tam_su` (tâm tình/podcast).
     * *Tông giọng chủ đạo:* Nhịp điệu và cảm xúc nền của toàn bài.
   - **Bước 2 (Micro Tagging - Gán thẻ theo mạch):** Gửi từng đoạn kịch bản kèm theo ngữ cảnh đã chốt ở Bước 1. 
   - **Thu gọn danh sách thẻ vàng (Curated Golden Tags):** Rút gọn danh sách thẻ yêu cầu LLM gán xuống còn **8–10 thẻ có tác động âm học rõ rệt nhất**:
     * `[vui]`, `[buồn]`, `[hào hứng]`, `[kịch tính]`, `[thì thầm]`, `[nghiêm túc]`, `[dịu dàng]`, `[châm biếm]`, `[bình thường]`.
     * Thẻ ngắt nhịp: `[nghỉ]`, `[nghỉ dài]`.
   - **Chuẩn hóa đầu ra (Output Validation):** Yêu cầu LLM trả về cấu trúc phân tách rõ ràng (hoặc JSON mảng câu), có cơ chế regex dự phòng để không bao giờ làm mất hoặc thay đổi chữ trong kịch bản của người dùng.

---

### NHIỆM VỤ 2: TỐI ƯU HÓA BIỂU CẢM VOICE CLONE TỰ NHIÊN (HUMAN-LIKE NUANCE)

#### Vấn đề hiện tại:
- F5-TTS là mô hình khuếch tán non-autoregressive, cảm xúc chủ yếu phụ thuộc vào âm sắc của đoạn mẫu tham chiếu (`ref_audio`).
- Hiện tại trong `_CLONE_EMO_FX` (`app/core/expressive.py`), các cảm xúc mới chỉ được xử lý bằng các quy tắc thô (thay đổi âm lượng $\pm 1..3\text{ dB}$, tốc độ nhanh/chậm, cao độ F0 cơ bản). Giọng đọc nghe vẫn còn hơi đều đều, thiếu tính "người".

#### Yêu cầu kỹ thuật cho Agent:
1. **Khai thác triệt để bộ mẫu đa cảm xúc (Multi-Style Reference Mapping):**
   - Khi người dùng thêm giọng bằng file dài (1–3 phút), `app/core/refpick.py` đã có cơ chế phân tích trích xuất:
     * Mẫu chính: `data/voice_profiles/<name>/ref.wav`
     * Mẫu hào hứng: `data/voice_profiles/<name>/styles/hype.wav`
     * Mẫu trầm: `data/voice_profiles/<name>/styles/calm.wav`
     * Mẫu xoay vòng: `styles/alt1.wav`, `alt2.wav`
   - **Nhiệm vụ:** Kiểm tra và hoàn thiện liên kết trong `app/core/clone_worker.py` và `expressive.py`:
     * Khi gặp câu có thẻ `[hào hứng]`, `[vui]`: Ưu tiên nạp mẫu `hype.wav` của chính người đó làm ref.
     * Khi gặp câu `[buồn]`, `[thì thầm]`, `[dịu dàng]`: Ưu tiên nạp mẫu `calm.wav`.
     * Khi gặp câu kể chuyện thông thường: Xoay vòng giữa `ref.wav` và các `alt*.wav` để giọng không bị lặp âm đơn điệu.
2. **Làm mềm ngữ điệu đuôi câu tiếng Việt:**
   - Nhận diện các từ đệm/từ cảm thán ở cuối câu trong tiếng Việt (*"nhé", "nha", "nè", "chứ", "à", "ơi", "đấy"*).
   - Tự động điều chỉnh đường cong năng lượng (fade-out nhẹ) và hạ nhẹ cao độ ở cuối câu để tạo nhịp thở tự nhiên, tránh cảm giác máy móc ngắt đột ngột.

---

### NHIỆM VỤ 3: HOÀN THIỆN TÍNH NĂNG DOANH NGHIỆP TRÊN GIAO DIỆN `/admin`

#### Vấn đề hiện tại:
- Sơ đồ kiến trúc (ERD & BPMN) đã thiết kế bảng `GEN_JOBS` (Sổ nhật ký Audit Log) và phân quyền Quản lý phòng (`manager`) / Nhân viên (`staff`). Tuy nhiên trên giao diện web `/admin`, phần hiển thị nhật ký kịch bản chưa trực quan.

#### Yêu cầu kỹ thuật cho Agent:
1. **Giao diện Audit Log cho Trưởng phòng:**
   - Trong `app/web/static/admin.html` và `app/web/server.py`:
   - Bổ sung bảng "Lịch sử kịch bản đã sinh (Audit Log)": hiển thị thời gian, tên tài khoản nhân viên, tên giọng clone đã dùng, số ký tự kịch bản, và một trình phát audio nhỏ để Trưởng phòng bấm nghe lại file thành phẩm.
2. **Cơ chế duyệt giọng (Pending Voice Approval):**
   - Khi nhân viên tải lên một giọng clone mới từ máy trạm, giọng đó phải ở trạng thái `pending` (chờ duyệt).
   - Chỉ khi Quản trị viên/Trưởng phòng đăng nhập trực tiếp trên máy chủ (`Host-Only`) bấm nút "Phê duyệt" thì giọng đó mới xuất hiện trong danh mục chọn giọng của các máy trạm khác.

---

## 5. CÁC NGUYÊN TẮC BẮT BUỘC KHI VIẾT MÃ NGUỒN (CONSTRAINTS)

Agent làm việc trên codebase này **BẮT BUỘC TUÂN THỦ**:
1. **Bảo toàn phong cách chú thích:** Tất cả comment mã nguồn trong project đều viết bằng tiếng Việt không dấu (ASCII no-diacritics), ví dụ: `# kiem tra quyen may chu`. Giữ nguyên quy ước này.
2. **Không làm hỏng tính tương thích Windows Portable:**
   - Không được thêm các thư viện yêu cầu cài đặt C++ Build Tools nặng trên máy khách nếu không thể đóng gói dạng binary wheel độc lập.
   - Luôn sử dụng đường dẫn tương đối qua `Path(__file__).resolve().parent` hoặc `os.path.join`.
3. **Giữ nguyên 100% nội dung kịch bản gốc của người dùng:** Bất kỳ hàm AI biên kịch nào cũng chỉ được chèn thêm thẻ biểu cảm `[...]` hoặc dấu `*nhấn mạnh*`, tuyệt đối không được tự ý viết lại (rewrite) hay làm mất chữ của kịch bản gốc.
4. **Cơ chế Fallback nhiều tầng:** Nếu Ollama bị tắt hoặc lỗi, hệ thống phải tự động rơi xuống bộ phân tích quy tắc nhanh (`_heuristic_annotate`) mà không bao giờ được phép crash server.

---

## 6. QUY TRÌNH KIỂM THỬ (VERIFICATION CHECKLIST)

Sau khi chỉnh sửa mã nguồn, Agent cần chạy các bước kiểm tra sau:

1. **Kiểm tra cú pháp Python:**
   ```bash
   python3 -m py_compile app/core/expressive.py app/core/translate.py app/core/clone_worker.py app/web/server.py
   ```
2. **Kiểm thử AI Biên Kịch độc lập:**
   ```bash
   python3 -c "from app.core import expressive; res, note = expressive.auto_annotate('Hôm nay trời rất đẹp. Đừng lo lắng nhé.'); print('KẾT QUẢ:', res); print('GHI CHÚ:', note)"
   ```
3. **Kiểm thử sinh giọng Clone với thẻ cảm xúc mới:**
   ```bash
   python3 test_voices.py
   ```
4. **Kiểm tra trạng thái server và giao diện:**
   - Khởi động server: `python3 run.py --port 8765`
   - Kiểm tra API login và duyệt giọng trên `/admin`.

---
*Tài liệu được lập ngày 31/10/2026 bởi Phát Lê — Sẵn sàng chuyển giao cho Agent tiếp theo thực thi.*
