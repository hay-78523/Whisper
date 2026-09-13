# -*- coding: utf-8 -*-
"""Cau hinh tap trung — moi hang so chinh cua ung dung nam o day.

Mot so gia tri override duoc bang bien moi truong (huu ich khi deploy
Docker / Hugging Face Spaces): WHISPER_DEFAULT_MODEL, WHISPER_USERS.
"""

import os

# --- Web server ---
DEFAULT_HOST = "127.0.0.1"   # doi thanh "0.0.0.0" khi can truy cap tu may khac trong LAN
DEFAULT_PORT = 8765

# --- Dang nhap ---
# Tai khoan luu trong users.json (tu tao lan dau voi admin/admin123 — doi ngay!).
# Quan ly tai khoan trong trang /admin.
ADMIN_SESSION_HOURS = 12     # phien dang nhap het han sau bay nhieu gio

# --- Whisper (phien am) ---
MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
DEFAULT_MODEL = os.environ.get("WHISPER_DEFAULT_MODEL", "large-v3-turbo")
BATCH_SIZE = 8               # so doan xu ly song song trong batched inference

# --- TTS (long tieng) ---
DEFAULT_VOICE = "edge:vi-VN-HoaiMyNeural"
TTS_CHUNK_CHARS = 2500       # do dai moi khuc gui edge-tts
TTS_CONCURRENCY = 6          # so khuc tao song song
MAX_TTS_CHARS = 200000       # ~1 cuon truyen vua / lan long tieng
MAX_UPLOAD_MB = 2000         # tran dung luong file upload (chong day o dia)
VIENEU_PRECISION = "fp32"    # chat luong toi da; "int8" = nhanh hon ~1.6x neu may yeu
import platform

# =====================================================================
# VOICE CLONE (F5-TTS) — tinh nang CHINH cua tool
# ---------------------------------------------------------------------
# Cai dat 1 lenh:  python3 setup_clone.py        (xem lai:  --check)
# Moi thu so o day co the tinh chinh ngay tren trang /admin (luu vao
# data/clone_settings.json) — khong can sua file nay.
# =====================================================================

# --- Noi chua model tieng Viet ---
CLONE_MODEL_DIR = os.path.join("data", "f5_vi")

# Nguon tai: HF chinh -> hf-mirror (cho mang chan HF). Tai xong file nao cung
# duoc KIEM TRA NOI DUNG: model phai la checkpoint torch thuc su, vocab phai la
# bang ky tu thuc su. Trang loi HTML / con tro Git-LFS / file cat dang deu bi
# phat hien va tu tai lai tu nguon ke tiep (dung de "tai xong" ma dung thi loi).
_HF = "https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice/resolve/main/"
_HF_MIRROR = "https://hf-mirror.com/hynt/F5-TTS-Vietnamese-ViVoice/resolve/main/"
CLONE_ASSETS = {
    "model.pt": {
        "label": "Model giong Viet — ViVoice 1000h (~1.3GB)",
        "kind": "torch",
        "min_bytes": 500_000_000,
        "urls": [_HF + "model_last.pt", _HF_MIRROR + "model_last.pt"],
    },
    "vocab.txt": {
        "label": "Bang ky tu (vocab)",
        "kind": "vocab",
        "min_bytes": 200,
        # vocab.txt la file DUNG; config.json chi la duong lui cho ban repo cu
        # (neu tai ve la JSON thi bi loai ngay, khong ghi de file tot).
        "urls": [_HF + "vocab.txt", _HF_MIRROR + "vocab.txt", _HF + "config.json"],
    },
}
CLONE_DOWNLOAD_RETRY = 3     # so lan thu lai moi nguon khi mang dut

# --- Worker (tien trinh rieng giu model trong RAM) ---
CLONE_WORKER_PORT = 8081     # cong dau tien thu; bi chiem thi tu do len 8082...
CLONE_WORKER_PORT_TRIES = 10
CLONE_WORKER_BOOT_TIMEOUT = 300   # giay — lan dau nap torch + model rat lau
CLONE_WORKER_IDLE_EXIT = 0   # >0 = worker tu tat sau bay nhieu giay khong dung

# --- Chat luong sinh giong ---
# nfe_step: so buoc khuech tan. 16 = nhanh, 32 = chuan, 48 = min nhat (cham ~1.5x).
CLONE_NFE_STEP = 16 if platform.system() == "Darwin" else 32
CLONE_CFG_STRENGTH = 2.0     # do "giong mau" — cao qua de bi gat, thap qua thi nhoe
CLONE_SWAY_SAMPLING = -1.0   # -1 = mac dinh F5, cho phat am on dinh nhat
CLONE_CROSS_FADE = 0.12      # giay — noi cac khuc ben trong 1 cau cho lien mach
CLONE_TARGET_RMS = 0.1       # chuan hoa am luong ben trong F5
CLONE_SEED = -1              # -1 = ngau nhien; dat so cu the de tai lap ket qua
CLONE_DEVICE = "auto"        # auto | cuda | mps | cpu
CLONE_LOWERCASE = True       # model ViVoice duoc train chu thuong
CLONE_MAX_CHARS_PER_CUE = 220  # cau dai hon se tu cat theo dau cau truoc khi doc
CLONE_RETRY = 1              # so lan doc lai RIENG nhung doan bi loi (khong bo job)
CLONE_FAIL_RATIO = 0.35      # loi vuot ti le nay moi bao that bai ca job

# --- Hau ky am thanh (chay trong tien trinh chinh, thuan Python) ---
CLONE_POST_TRIM = True       # cat im lang dau/cuoi tung doan -> khop timestamp
CLONE_POST_NORMALIZE = True  # can bang am luong moi doan (het to nho that thuong)
CLONE_POST_PEAK_DBFS = -1.5  # dinh am luong sau chuan hoa
CLONE_POST_MAX_GAIN_DB = 12.0  # khong keo qua tay (tranh keo ca tieng on)
CLONE_POST_FADE_MS = 10      # fade in/out chong tieng "tach" khi lap track

# --- Mau giong tham chieu (ref) ---
CLONE_REF_MAX_SEC = 12.0     # F5 dat chat luong tot nhat voi mau 6-12 giay
CLONE_REF_MIN_SEC = 3.0      # ngan hon se canh bao
CLONE_REF_DENOISE = False    # bat neu mau co tieng on nen (co the lam mat hoi)
CLONE_REF_TRANSCRIBE_MODEL = "small"   # model Whisper dung de tu lay script mau
CLONE_SR = 24000             # F5 lam viec o 24kHz — khop luon voi track lap rap

# --- Dich "xin" bang AI (tuy chon, nang chat luong dich len muc long tieng chuyen nghiep) ---
# Lay key MIEN PHI (khong can the) tai https://aistudio.google.com -> "Get API key",
# roi dien vao giua 2 dau nhay duoi day, hoac dat bien moi truong GEMINI_API_KEY.
# Bo trong = dung Google Translate nhu binh thuong.
GEMINI_API_KEYS = "YOUR_GEMINI_KEY_1,YOUR_GEMINI_KEY_2"
GEMINI_API_KEY = "YOUR_GEMINI_KEY_HERE"
GEMINI_MODEL = "gemini-3.6-flash"

# --- AI du phong khi Gemini het quota (deu MIEN PHI, tool tu nhay nguon) ---
# Groq: key free KHONG can the tai https://console.groq.com -> API Keys.
# Han ~14.400 luot/ngay — gan nhu khong bao gio het.
# Co the nhap nhieu key cach nhau bang dau phay vao GROQ_API_KEYS (vd: "key1,key2") de xoay vong y het Gemini.
GROQ_API_KEY = "YOUR_GROQ_KEY_HERE"
GROQ_API_KEYS = "YOUR_GROQ_KEY_1,YOUR_GROQ_KEY_2"
GROQ_MODEL = "openai/gpt-oss-120b"   # model doi thi tool tu do model con song thay the
# Ollama: AI chay LOCAL vo han, khong can mang — cai tu https://ollama.com
# roi chay `ollama pull qwen2.5:7b`. Tool TU PHAT HIEN khi Ollama dang mo.
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = ""            # trong = tu dung model dau tien da tai ve

# --- ElevenLabs (tuy chon) — giong TTS hay nhat the gioi, goi free ~10k ky tu/thang ---
# 1. Dang ky free tai https://elevenlabs.io -> avatar -> API Keys -> tao key, dan vao day.
# 2. Vao Voice Library tren web ElevenLabs, bam "Add" giong nao thich (vd giong Viet
#    "Thắm") -> giong do TU HIEN trong dropdown cua tool, nghe thu duoc luon.
# Luu y goi free: dung ca nhan, can ghi cong ElevenLabs neu dang video.
ELEVEN_API_KEY = ""
ELEVEN_MODEL = "eleven_multilingual_v2"   # doc tot tieng Viet; doi "eleven_v3" neu tai khoan ho tro

# --- Giong doc: catalog PHAN TANG DO TUOI kieu ElevenLabs ---
# Moi giong: (id, ten, phong cach). Server tu an giong cua engine chua san sang
# (VieNeu chua cai / thieu key Gemini). Da loai cac giong tho tu truoc.
GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
# Giong AI Studio doc voi phong cach nay ca khi van ban KHONG co the bieu cam
GEMINI_TTS_STYLE = "tự nhiên và truyền cảm, như đang tâm tình kể chuyện với bạn thân"
# Het quota -> TU DONG doi sang giong free gan chat nhat, khong bao gio tac tieng
GEMINI_TTS_FALLBACK = {
    "Sulafat": "vieneu:Thục Đoan", "Aoede": "vieneu:Ngọc Huyền",
    "Kore": "vieneu:Ngọc Huyền", "Leda": "edge:vi-VN-HoaiMyNeural",
    "Gacrux": "vieneu:Quỳnh Anh", "Puck": "edge:vi-VN-NamMinhNeural",
    "Achird": "vieneu:Adam", "Fenrir": "edge:vi-VN-NamMinhNeural",
    "Charon": "edge:vi-VN-NamMinhNeural",
}
GEMINI_TTS_FALLBACK_DEFAULT = "edge:vi-VN-HoaiMyNeural"

# --- NHAN VAT ke chuyen (kieu roster ElevenLabs: James/Cassidy/Adam...) ---
# Moi nhan vat = giong AI Studio + CHI DAN DIEN XUAT rieng; het quota tu doi
# sang the than free (edge chinh tong / VieNeu) van giu chat nhan vat.
PERSONA_ORDER = ["tam", "tung", "trang", "hac", "tho", "kich", "vy", "hien", "giang", "vinh"]
# Roster TIENG ANH — mirror kieu ElevenLabs (Old/Husky/Dark Storyteller...)
PERSONA_ORDER_EN = ["oliver", "james", "adam", "cassidy", "hope", "vincent",
                    "chris", "mark", "peter", "grace",
                    "arabella", "ian", "wade", "bradford", "david",
                    "fred", "alex",
                    "margaret", "walter", "sophie", "rex", "daisy", "victor"]
_EN_WRAP = "Speak in English, in the style of %s: %s"
PERSONA_VOICES = {
    "tam":   {"name": "Tâm",   "style": "tâm tình ấm áp — chất Thắm",
              "gemini": "Sulafat",
              "gemini_style": "ấm áp tâm tình, chậm rãi thủ thỉ như kể chuyện cho bạn thân nghe",
              "fallback": "vieneu:Thục Đoan", "fallback_rate": 0},
    "tung":  {"name": "Tùng",  "style": "kể chuyện trầm khàn từng trải",
              "gemini": "Charon",
              "gemini_style": "trầm khàn từng trải, chậm rãi ấm áp như người ông kể chuyện bên bếp lửa",
              "fallback": "edge:vi-VN-NamMinhNeural", "fallback_rate": 0},
    "trang": {"name": "Trang", "style": "podcast rõ ràng khúc chiết",
              "gemini": "Kore",
              "gemini_style": "rõ ràng khúc chiết, tự tin chuyên nghiệp như dẫn podcast",
              "fallback": "edge:vi-VN-HoaiMyNeural", "fallback_rate": 0},
    "hac":   {"name": "Hắc",   "style": "truyện đêm u tối rùng rợn",
              "gemini": "Charon",
              "gemini_style": "trầm thấp u tối, chậm và rợn như kể truyện ma lúc nửa đêm",
              "fallback": "edge:vi-VN-NamMinhNeural", "fallback_rate": 0},
    "tho":   {"name": "Thơ",   "style": "truyện đêm khuya dịu dàng",
              "gemini": "Aoede",
              "gemini_style": "dịu dàng mềm mại, chậm và êm như đọc truyện đêm khuya",
              "fallback": "edge:vi-VN-HoaiMyNeural", "fallback_rate": 0},
    "kich":  {"name": "Kịch",  "style": "giật gân hồi hộp kịch tính",
              "gemini": "Fenrir",
              "gemini_style": "căng thẳng dồn dập, ngắt nhịp bất ngờ đầy kịch tính hồi hộp",
              "fallback": "edge:vi-VN-NamMinhNeural", "fallback_rate": 0},
    "vy":    {"name": "Vy",    "style": "social media năng lượng cao",
              "gemini": "Leda",
              "gemini_style": "năng lượng cao, hào hứng bắt trend như video mạng xã hội",
              "fallback": "edge:vi-VN-HoaiMyNeural", "fallback_rate": 0},
    "hien":  {"name": "Hiền",  "style": "người bạn đồng hành thân thiện",
              "gemini": "Achird",
              "gemini_style": "thân thiện gần gũi, vui vẻ tự nhiên như người bạn thân trò chuyện",
              "fallback": "edge:vi-VN-NamMinhNeural", "fallback_rate": 0},
    "giang": {"name": "Giang", "style": "thương hiệu sang trọng",
              "gemini": "Gacrux",
              "gemini_style": "sang trọng điềm đạm, chậm rãi đầy đẳng cấp như quảng cáo thương hiệu xa xỉ",
              "fallback": "edge:vi-VN-HoaiMyNeural", "fallback_rate": 0},
    "vinh":  {"name": "Vinh",  "style": "tự tin mạnh mẽ thuyết phục",
              "gemini": "Puck",
              "gemini_style": "tự tin mạnh mẽ, nhấn nhá chắc chắn đầy thuyết phục",
              "fallback": "edge:vi-VN-NamMinhNeural", "fallback_rate": 0},
    # ---- American storytellers ----
    "michael_us": {"name": "Michael", "style": "diễn giả truyền cảm hứng", "lang": "en",
                   "gemini": "Puck", "wrap": "Speak in English. You are a highly energetic, passionate and inspiring American motivational speaker. Use extreme vocal variety, enthusiasm, and deep emotion: %s",
                   "fallback": "edge:en-US-SteffanNeural", "fallback_rate": 0},
    "sarah_us":   {"name": "Sarah", "style": "nữ diễn viên kịch tính", "lang": "en",
                   "gemini": "Leda", "wrap": "Speak in English. You are a dramatic American actress performing an intense, emotional scene. Be highly expressive, dramatic, and let your voice shake with emotion: %s",
                   "fallback": "edge:en-US-JennyNeural", "fallback_rate": 0},
    "david_us":   {"name": "David", "style": "người kể chuyện giật gân", "lang": "en",
                   "gemini": "Fenrir", "wrap": "Speak in English. You are an intense, suspenseful American storyteller. Lower your voice to a thrilling whisper, pause dramatically, and build incredible tension: %s",
                   "fallback": "edge:en-US-GuyNeural", "fallback_rate": 0},
    "emily_us":   {"name": "Emily", "style": "cô gái trẻ vui tươi bùng nổ", "lang": "en",
                   "gemini": "Aoede", "wrap": "Speak in English. You are a very bubbly, cheerful and overly excited young American girl. Laugh lightly, sound incredibly joyful, bright and full of life: %s",
                   "fallback": "edge:en-US-AriaNeural", "fallback_rate": 0},
    "oliver_us":  {"name": "Oliver", "style": "ông lão kể chuyện trầm ấm", "lang": "en",
                   "gemini": "Charon", "wrap": "Speak in English. You are a wise, old American grandpa telling a slow, warm and nostalgic story by the fireplace. Sound deeply intimate, husky and caring: %s",
                   "fallback": "edge:en-US-ChristopherNeural", "fallback_rate": 0},
    "jessica_us": {"name": "Jessica", "style": "MC podcast linh hoạt", "lang": "en",
                   "gemini": "Kore", "wrap": "Speak in English. You are a sharp, witty, and highly engaging American podcast host. Be incredibly expressive, conversational, and use very natural human intonation: %s",
                   "fallback": "edge:en-US-MichelleNeural", "fallback_rate": 0},

    # ---- English storytellers ----
    "oliver":  {"name": "Oliver", "style": "Old Storyteller", "lang": "en",
                "gemini": "Charon", "wrap": _EN_WRAP,
                "gemini_style": "an old, wise storyteller — slow, raspy and warm, like memories by the fire",
                "fallback": "edge:en-GB-RyanNeural", "fallback_rate": 0},
    "james":   {"name": "James", "style": "Husky Storyteller", "lang": "en",
                "gemini": "Charon", "wrap": _EN_WRAP,
                "gemini_style": "a husky, low, intimate storyteller with gravelly warmth",
                "fallback": "edge:en-US-ChristopherNeural", "fallback_rate": 0},
    "adam":    {"name": "Adam", "style": "Dark Storyteller", "lang": "en",
                "gemini": "Fenrir", "wrap": _EN_WRAP,
                "gemini_style": "a dark, ominous storyteller — low, slow and chilling, like a midnight horror tale",
                "fallback": "edge:en-US-RogerNeural", "fallback_rate": 0},
    "cassidy": {"name": "Cassidy", "style": "Crisp Podcaster", "lang": "en",
                "gemini": "Kore", "wrap": _EN_WRAP,
                "gemini_style": "a crisp, clear, confident podcast host",
                "fallback": "edge:en-US-AnaNeural", "fallback_rate": 0},
    "hope":    {"name": "Hope", "style": "Social Media", "lang": "en",
                "gemini": "Leda", "wrap": _EN_WRAP,
                "gemini_style": "a high-energy social media creator — upbeat, fast and catchy",
                "fallback": "edge:en-US-AriaNeural", "fallback_rate": 0},
    "vincent": {"name": "Vincent", "style": "Suspenseful Storyteller", "lang": "en",
                "gemini": "Fenrir", "wrap": _EN_WRAP,
                "gemini_style": "a suspenseful thriller narrator — tense, urgent, with dramatic pauses",
                "fallback": "edge:en-US-GuyNeural", "fallback_rate": 0},
    "chris":   {"name": "Christopher", "style": "British Storyteller", "lang": "en",
                "gemini": "Achird", "wrap": _EN_WRAP,
                "gemini_style": "a warm British storyteller with a refined English accent",
                "fallback": "edge:en-GB-ThomasNeural", "fallback_rate": 0},
    "mark":    {"name": "Mark", "style": "Friendly Companion", "lang": "en",
                "gemini": "Achird", "wrap": _EN_WRAP,
                "gemini_style": "a friendly companion chatting casually with a close friend",
                "fallback": "edge:en-NZ-MitchellNeural", "fallback_rate": 0},
    "peter":   {"name": "Peter", "style": "Confident Storyteller", "lang": "en",
                "gemini": "Puck", "wrap": _EN_WRAP,
                "gemini_style": "a confident, persuasive storyteller with commanding presence",
                "fallback": "edge:en-US-EricNeural", "fallback_rate": 0},
    "grace":   {"name": "Grace", "style": "Late Night Reader", "lang": "en",
                "gemini": "Aoede", "wrap": _EN_WRAP,
                "gemini_style": "a gentle late-night reader — soft, slow and soothing",
                "fallback": "edge:en-US-AriaNeural", "fallback_rate": 0},
    "arabella": {"name": "Arabella", "style": "Romance Storyteller", "lang": "en",
                "gemini": "Sulafat", "wrap": _EN_WRAP,
                "gemini_style": "a romantic storyteller — soft, breathy, tender and dreamy",
                "fallback": "edge:en-US-MichelleNeural", "fallback_rate": 0},
    "jessica": {"name": "Jessica", "style": "Romance Storyteller · trưởng thành", "lang": "en",
                "gemini": "Gacrux", "wrap": _EN_WRAP,
                "gemini_style": "a warm, mature romantic narrator — velvety, heartfelt and intimate",
                "fallback": "edge:en-US-MichelleNeural", "fallback_rate": 0},
    "ian":     {"name": "Ian", "style": "Mystery Storyteller", "lang": "en",
                "gemini": "Charon", "wrap": _EN_WRAP,
                "gemini_style": "a mystery narrator — low, deliberate, full of intrigue and quiet menace",
                "fallback": "edge:en-IE-ConnorNeural", "fallback_rate": 0},
    "wade":    {"name": "Wade", "style": "Gravely Storyteller", "lang": "en",
                "gemini": "Charon", "wrap": _EN_WRAP,
                "gemini_style": "a gravelly, rough-voiced storyteller — weathered, raw and lived-in",
                "fallback": "edge:en-GB-RyanNeural", "fallback_rate": 0},
    "bradford": {"name": "Bradford", "style": "Articulated Storyteller", "lang": "en",
                "gemini": "Puck", "wrap": _EN_WRAP,
                "gemini_style": "an articulate storyteller with crisp enunciation and measured pacing",
                "fallback": "edge:en-IE-ConnorNeural", "fallback_rate": 0},
    "david":   {"name": "David", "style": "Newsreader", "lang": "en",
                "gemini": "Fenrir", "wrap": _EN_WRAP,
                "gemini_style": "a professional news anchor — clear, authoritative and steady",
                "fallback": "edge:en-US-SteffanNeural", "fallback_rate": 0},
    "fred":    {"name": "Frederick", "style": "Science Storyteller", "lang": "en",
                "gemini": "Achird", "wrap": _EN_WRAP,
                "gemini_style": "an enthusiastic science communicator — curious, clear and full of wonder",
                "fallback": "edge:en-CA-LiamNeural", "fallback_rate": 0},
    "alex":    {"name": "Alex", "style": "Social Media · nam", "lang": "en",
                "gemini": "Puck", "wrap": _EN_WRAP,
                "gemini_style": "a high-energy male social media creator — fast, punchy and fun",
                "fallback": "edge:en-AU-WilliamNeural", "fallback_rate": 0},
    "margaret": {"name": "Margaret", "style": "Grandma Storyteller · bà cụ ấm áp", "lang": "en",
                "gemini": "Gacrux", "wrap": _EN_WRAP,
                "gemini_style": "a warm elderly grandmother telling bedtime stories — slow, loving, with a gentle chuckle",
                "fallback": "edge:en-GB-SoniaNeural", "fallback_rate": 0},
    "walter":  {"name": "Walter", "style": "Wise Elder · ông lão hiền triết", "lang": "en",
                "gemini": "Charon", "wrap": _EN_WRAP,
                "gemini_style": "an old wise elder — very slow, warm and gravelly, with thoughtful pauses",
                "fallback": "edge:en-ZA-LukeNeural", "fallback_rate": 0},
    "sophie":  {"name": "Sophie", "style": "Whisper ASMR · thì thầm êm dịu", "lang": "en",
                "gemini": "Aoede", "wrap": _EN_WRAP,
                "gemini_style": "a soft ASMR whisper — extremely gentle, close to the microphone and calming",
                "fallback": "edge:en-IE-EmilyNeural", "fallback_rate": 0},
    "rex":     {"name": "Rex", "style": "Movie Trailer · hùng tráng", "lang": "en",
                "gemini": "Fenrir", "wrap": _EN_WRAP,
                "gemini_style": "an epic movie-trailer announcer — deep, dramatic and thunderous",
                "fallback": "edge:en-SG-WayneNeural", "fallback_rate": 0},
    "daisy":   {"name": "Daisy", "style": "Kids Show Host · MC thiếu nhi", "lang": "en",
                "gemini": "Leda", "wrap": _EN_WRAP,
                "gemini_style": "a cheerful children's show host — bright, playful and bouncy",
                "fallback": "edge:en-GB-MaisieNeural", "fallback_rate": 0},
    "victor":  {"name": "Victor", "style": "Villain · phản diện lạnh lùng", "lang": "en",
                "gemini": "Charon", "wrap": _EN_WRAP,
                "gemini_style": "a cold, elegant villain — smooth, menacing and softly threatening",
                "fallback": "edge:en-IN-PrabhatNeural", "fallback_rate": 0},
    "arthur":  {"name": "Arthur", "style": "British Gentleman · quý ông", "lang": "en",
                "gemini": "Charon", "wrap": _EN_WRAP,
                "gemini_style": "a sophisticated, polite British gentleman with a polished accent — elegant, charming and proper",
                "fallback": "edge:en-GB-ThomasNeural", "fallback_rate": 0},
    "beatrice": {"name": "Beatrice", "style": "British Lady · quý cô", "lang": "en",
                "gemini": "Gacrux", "wrap": _EN_WRAP,
                "gemini_style": "an elegant, articulate British lady with a refined RP accent — crisp, poised and warm",
                "fallback": "edge:en-IE-EmilyNeural", "fallback_rate": 0},
    "oliver_uk": {"name": "Oliver", "style": "UK Newsreader · thời sự", "lang": "en",
                "gemini": "Fenrir", "wrap": _EN_WRAP,
                "gemini_style": "a professional British news anchor — authoritative, clear and serious",
                "fallback": "edge:en-NZ-MitchellNeural", "fallback_rate": 0},
    "charlotte": {"name": "Charlotte", "style": "UK Storyteller · kể chuyện", "lang": "en",
                "gemini": "Leda", "wrap": _EN_WRAP,
                "gemini_style": "a lively and expressive British storyteller — dramatic, engaging and whimsical",
                "fallback": "edge:en-GB-LibbyNeural", "fallback_rate": 0},
}
VOICE_CATALOG = [
    ("Siêu tự nhiên · AI Studio (online)", [
        ("gemini:Sulafat", "Sulafat", "giống Thắm nhất — nữ ấm áp tâm tình"),
        ("gemini:Leda", "Leda", "nữ trẻ trung, năng động"),
        ("gemini:Aoede", "Aoede", "nữ, thư thái tự nhiên"),
        ("gemini:Kore", "Kore", "nữ, chắc giọng thuyết minh"),
        ("gemini:Gacrux", "Gacrux", "nữ, trưởng thành điềm đạm"),
        ("gemini:Puck", "Puck", "nam, hoạt bát vui vẻ"),
        ("gemini:Achird", "Achird", "nam, thân thiện gần gũi"),
        ("gemini:Fenrir", "Fenrir", "nam, sôi nổi mạnh mẽ"),
        ("gemini:Charon", "Charon", "nam, trầm rõ tài liệu"),
    ]),
    ("🇻🇳 Việt Nam", [
        ("edge:vi-VN-HoaiMyNeural", "Hoài My", "nữ · chuẩn mực (mặc định)"),
        ("vieneu:Trúc Ly", "Trúc Ly", "nữ trẻ · miền Bắc tự nhiên"),
        ("vieneu:Mai Anh", "Mai Anh", "nữ trẻ · miền Bắc, tin tức"),
        ("vieneu:Ngọc Huyền", "Ngọc Huyền", "nữ · miền Bắc tự nhiên"),
        ("vieneu:Đoan Trang", "Đoan Trang", "nữ · miền Bắc tự nhiên"),
        ("vieneu:Ngọc Trân", "Ngọc Trân", "nữ · miền Trung tự nhiên"),
        ("vieneu:Thùy Dung", "Thùy Dung", "nữ · miền Nam, tin tức"),
        ("vieneu:Ngọc Linh", "Ngọc Linh", "nữ trầm · kể chuyện Bắc"),
        ("vieneu:Quỳnh Anh", "Quỳnh Anh", "nữ trầm · audiobook Bắc"),
        ("vieneu:Thục Đoan", "Thục Đoan", "nữ trầm · kể chuyện Nam"),
        ("vieneu:Mỹ Duyên", "Mỹ Duyên", "nữ trầm · audiobook Nam"),
        ("vieneu:Kim Thanh", "Kim Thanh", "nữ trầm · audiobook Nam"),
        ("edge:vi-VN-NamMinhNeural", "Nam Minh", "nam · chuẩn mực"),
        ("vieneu:Adam", "Adam", "nam trẻ · miền Nam tự nhiên"),
        ("vieneu:Xuân Vĩnh", "Xuân Vĩnh", "nam trẻ · miền Nam tự nhiên"),
        ("vieneu:Thái Sơn", "Thái Sơn", "nam · kể chuyện Nam"),
        ("vieneu:Minh Triết", "Minh Triết", "nam · tin tức Nam"),
        ("vieneu:Đức Trí", "Đức Trí", "nam · audiobook Nam"),
        ("vieneu:Phạm Tuyên", "Phạm Tuyên", "nam · miền Bắc tự nhiên"),
        ("vieneu:Minh Đức", "Minh Đức", "nam · tin tức Bắc"),
        ("vieneu:Thanh Bình", "Thanh Bình", "nam · kể chuyện Bắc"),
        ("vieneu:Quang Sơn", "Quang Sơn", "nam · miền Trung tự nhiên"),
    ]),
    ("🇺🇸 Mỹ", [
        ("persona:michael_us", "Michael (AI Studio)", "🇺🇸 nam · diễn giả truyền cảm hứng, bùng nổ"),
        ("persona:sarah_us", "Sarah (AI Studio)", "🇺🇸 nữ · diễn viên kịch tính, dạt dào cảm xúc"),
        ("persona:david_us", "David (AI Studio)", "🇺🇸 nam · kể chuyện giật gân, hồi hộp"),
        ("persona:emily_us", "Emily (AI Studio)", "🇺🇸 nữ · cô gái trẻ vui tươi, tràn đầy sức sống"),
        ("persona:oliver_us", "Oliver (AI Studio)", "🇺🇸 nam · ông lão kể chuyện trầm ấm, hoài niệm"),
        ("persona:jessica_us", "Jessica (AI Studio)", "🇺🇸 nữ · MC podcast linh hoạt, lôi cuốn"),
        ("edge:en-US-AriaNeural", "Aria", "nữ · tự tin, tích cực, đầy năng lượng"),
        ("edge:en-US-JennyNeural", "Jenny", "nữ · thân thiện, ấm áp, thoải mái"),
        ("edge:en-US-GuyNeural", "Guy", "nam · đam mê, sôi nổi, nhiệt huyết"),
    ]),
    ("🇬🇧 Anh Quốc", [
        ("persona:arthur", "Arthur (AI Studio)", "quý ông lịch lãm · giàu cảm xúc"),
        ("persona:beatrice", "Beatrice (AI Studio)", "quý cô thanh lịch · giàu cảm xúc"),
        ("persona:oliver_uk", "Oliver (AI Studio)", "phát thanh viên · giàu cảm xúc"),
        ("persona:charlotte", "Charlotte (AI Studio)", "kể chuyện lôi cuốn · giàu cảm xúc"),
        ("edge:en-GB-LibbyNeural", "Libby (Microsoft)", "nữ · nhẹ nhàng"),
        ("edge:en-GB-MaisieNeural", "Maisie (Microsoft)", "nữ bé gái · trong trẻo"),
        ("edge:en-GB-RyanNeural", "Ryan (Microsoft)", "nam · lịch lãm"),
    ]),
    ("🌍 Ngôn ngữ khác", [
        ("edge:ja-JP-NanamiNeural", "Nanami", "nữ · 日本語"),
        ("edge:ko-KR-SunHiNeural", "SunHi", "nữ · 한국어"),
        ("edge:zh-CN-XiaoxiaoNeural", "Xiaoxiao", "nữ · 中文"),
        ("edge:fr-FR-DeniseNeural", "Denise", "nữ · Français"),
        ("edge:de-DE-KatjaNeural", "Katja", "nữ · Deutsch"),
        ("edge:es-ES-ElviraNeural", "Elvira", "nữ · Español"),
    ]),
]
# Danh sach phang (CLI + tuong thich nguoc)
COMMON_VOICES = [(v, "%s — %s" % (n, s)) for _, vs in VOICE_CATALOG for v, n, s in vs]

# --- Dich ---
TRANSLATE_LANGS = {
    "vi": "Tiếng Việt", "en": "English", "ja": "日本語", "ko": "한국어",
    "zh-CN": "中文", "fr": "Français", "de": "Deutsch", "es": "Español",
}
TRANSLATE_CHUNK_CHARS = 4000

# Ngon ngu dich -> giong doc mac dinh tuong ung
LANG_DEFAULT_VOICE = {
    "vi": "edge:vi-VN-HoaiMyNeural", "en": "edge:en-US-JennyNeural",
    "ja": "edge:ja-JP-NanamiNeural", "ko": "edge:ko-KR-SunHiNeural",
    "zh-CN": "edge:zh-CN-XiaoxiaoNeural", "fr": "edge:fr-FR-DeniseNeural",
    "de": "edge:de-DE-KatjaNeural", "es": "edge:es-ES-ElviraNeural",
}

# --- Job ---
JOB_KEEP = 50                # giu toi da bao nhieu job gan nhat trong bo nho
