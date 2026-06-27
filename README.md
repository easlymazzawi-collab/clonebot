# TG Forum Clone

Hệ thống clone Telegram Forum gồm **Userbot** (clone nội dung), **Bot** (trả media ẩn nguồn) và **Dashboard HTML** kết nối API.

## Kiến trúc

```
Forum Nguồn
    ↓ Userbot (Telethon)
Forum Đích — thumbnail video, album giữ nguyên, ẩn nguồn
    ↓ Bot harvest file_id
SQLite DB (token → file_ids)
    ↓ Caption Editor
"{caption gốc} — Nhấp vào link này để xem: t.me/Bot?start=alb_xxx"
    ↓ User bấm link
Bot trả album gốc, ẩn tên nguồn
```

## Yêu cầu

- Python 3.10+
- Telethon `>=1.34.0,<2.0.0`
- Tài khoản Telegram với API credentials (lấy tại [my.telegram.org](https://my.telegram.org))
- Bot Telegram (tạo qua [@BotFather](https://t.me/BotFather))

## Cài đặt

```bash
# Clone về máy
git clone https://github.com/easlymazzawi-collab/clonebot.git
cd clonebot

# Chạy script setup (Linux / macOS / WSL)
bash setup.sh
```

Hoặc cài thủ công:

```bash
pip install -r requirements.txt
cp config/settings.example.json config/settings.json
# Sửa config/settings.json — điền api_id, api_hash, phone, bot token/username
```

## Cấu hình biến môi trường (tuỳ chọn)

Thay vì sửa file JSON, có thể dùng biến môi trường (ưu tiên hơn file):

```bash
export TG_API_ID=12345678
export TG_API_HASH=your_api_hash
export TG_PHONE="+84901234567"
export BOT_TOKEN="123456:ABCdef..."
```

## Chạy

```bash
python run.py
```

Mở trình duyệt: **http://localhost:8080**

> **Lần đầu kết nối Userbot** sẽ yêu cầu nhập OTP Telegram trong terminal.
> Chạy `python run.py` từ terminal có tương tác (không dùng background service lần đầu).

## Cấu trúc thư mục

```
clonebot/
├── run.py                  # Entry point — khởi động server FastAPI
├── setup.sh                # Script cài đặt (Linux / macOS / WSL)
├── index.html              # Dashboard UI
├── requirements.txt        # Python dependencies
├── config/
│   ├── settings.example.json   # Template cấu hình
│   └── settings.json           # Cấu hình thực tế (tự tạo, không commit)
├── backend/
│   └── main.py             # FastAPI REST API
├── userbot/
│   ├── clone_forum.py      # Clone forum (Phase 1: topics + Phase 2: messages)
│   ├── caption_editor.py   # Edit caption hàng loạt theo topic
│   ├── media_scanner.py    # Harvest file_id qua bot forward
│   └── telegram_client.py  # Telethon session singleton
├── bot/
│   ├── bot_main.py         # Bot PTB — trả media qua deep link
│   └── media_db.py         # SQLite: token → file_ids, view_count
└── shared/
    ├── config.py            # Load/save settings, env override
    ├── link_parser.py       # Parse Telegram message links
    ├── logger.py            # In-memory log buffer cho dashboard
    ├── progress.py          # State file helpers (resume clone/caption)
    └── telethon_compat.py   # Compat shim cho các phiên bản Telethon
```

## API

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/api/status` | Trạng thái userbot, bot, tiến trình clone/caption |
| `GET` | `/api/stats` | Thống kê tổng hợp (albums, files, views) |
| `GET` | `/api/logs` | Log gần nhất (`?limit=200&level=err`) |
| `DELETE` | `/api/logs` | Xóa log |
| `GET` | `/api/config` | Đọc cấu hình hiện tại |
| `POST` | `/api/config` | Lưu cấu hình |
| `POST` | `/api/userbot/connect` | Kết nối Telethon userbot |
| `POST` | `/api/userbot/disconnect` | Ngắt kết nối userbot |
| `POST` | `/api/bot/start` | Khởi động bot polling |
| `POST` | `/api/bot/stop` | Dừng bot |
| `POST` | `/api/clone/start` | Bắt đầu clone forum |
| `POST` | `/api/clone/stop` | Dừng clone |
| `GET` | `/api/clone/progress` | Tiến trình clone hiện tại |
| `GET` | `/api/sessions` | Danh sách phiên clone đã lưu |
| `POST` | `/api/caption/start` | Edit caption theo topic |
| `POST` | `/api/caption/stop` | Dừng caption edit |
| `GET` | `/api/caption/progress` | Tiến trình caption hiện tại |
| `POST` | `/api/media/scan` | Scan + harvest file_id của 1 topic |
| `GET` | `/api/media/albums` | Danh sách albums trong DB |
| `GET` | `/api/media/stats` | Thống kê albums/files/views |

## Quy trình sử dụng

1. **Cấu hình** — Tab Userbot + Bot: điền API credentials và bot token → Lưu
2. **Kết nối** — Nút "Kiểm tra" trên Userbot; nút "Khởi động" trên Bot
3. **Start bot** — Userbot phải gửi `/start` cho bot trước khi harvest file_id
4. **Clone** — Tab Clone Topics: nhập forum nguồn/đích → Chạy Clone
5. **Scan media** — Tab Media Manager: chọn topic → Scan (harvest file_id gốc)
6. **Caption** — Tab Caption Editor: nhập link đầu/cuối + template → Bắt đầu Edit

## Lưu ý quan trọng

- **OTP lần đầu**: chạy `python run.py` trong terminal có tương tác; sau khi xác thực session được lưu lại, các lần sau tự động.
- **Bot harvest**: userbot phải đã gửi `/start` cho bot **trước** khi chạy scan.
- **Video clone**: nếu bật `video_thumbnail_only`, chỉ gửi thumbnail (ảnh tĩnh) thay vì video đầy đủ.
- **Resume**: tiến trình clone và caption được lưu tự động — có thể dừng và tiếp tục bất kỳ lúc nào.
- **Session file**: `session_v24.session` được tạo ở thư mục gốc (đã bỏ qua trong `.gitignore`).

## Lỗi thường gặp

### `ImportError: CreateForumTopicRequest`

Phiên bản Telethon không tương thích. Chạy:

```bash
pip install --upgrade "telethon>=1.34.0,<2.0.0"
```

### `RuntimeError: Chưa cấu hình API_ID / API_HASH`

Kiểm tra `config/settings.json` hoặc biến môi trường `TG_API_ID` / `TG_API_HASH`.

### Dashboard hiện bot "Stopped" dù đã start

Đã được sửa trong phiên bản hiện tại. Đảm bảo đang dùng code mới nhất.
