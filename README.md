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

## Cài đặt

```bash
pip install -r requirements.txt
cp config/settings.example.json config/settings.json
# Sửa config/settings.json — điền API_ID, API_HASH, phone, bot token
```

Hoặc dùng biến môi trường:

```bash
export TG_API_ID=12345678
export TG_API_HASH=your_hash
export TG_PHONE="+84..."
export BOT_TOKEN=123456:ABC...
```

## Chạy

```bash
python run.py
```

Mở trình duyệt: **http://localhost:8080**

## Cấu trúc thư mục

```
clonebot/
├── run.py                  # Khởi động server
├── index.html              # Dashboard UI
├── backend/main.py         # FastAPI REST API
├── userbot/
│   ├── clone_forum.py      # Clone forum (Phase 1 + 2)
│   ├── caption_editor.py   # Edit caption hàng loạt
│   ├── media_scanner.py    # Harvest file_id qua bot
│   └── telegram_client.py  # Telethon session
├── bot/
│   ├── bot_main.py         # Bot trả media
│   └── media_db.py         # SQLite album map
├── shared/                 # Config, logger, progress
└── config/settings.json    # Cấu hình (tạo từ example)
```

## API chính

| Endpoint | Mô tả |
|----------|-------|
| `GET /api/status` | Trạng thái userbot, bot, clone |
| `GET /api/stats` | Thống kê tổng hợp |
| `POST /api/userbot/connect` | Kết nối Telethon |
| `POST /api/bot/start` | Khởi động bot polling |
| `POST /api/clone/start` | Bắt đầu clone forum |
| `POST /api/clone/stop` | Dừng clone |
| `POST /api/caption/start` | Edit caption theo topic |
| `POST /api/media/scan` | Scan + harvest file_id |
| `GET /api/media/albums` | Danh sách albums |
| `POST /api/config` | Lưu cấu hình |

## Quy trình sử dụng

1. **Cấu hình** — Tab Userbot + Bot: điền API credentials và bot token → Lưu
2. **Kết nối** — Nút "Kiểm tra" trên Userbot và Bot
3. **Clone** — Tab Clone Topics: nhập forum nguồn/đích → Chạy Clone
4. **Scan media** — Tab Media Manager: harvest file_id từ forum gốc
5. **Caption** — Tab Caption Editor: nhập link bắt đầu + template → Bắt đầu Edit

## Lưu ý

- Lần đầu chạy Userbot cần xác thực OTP Telegram (chạy `python run.py` trong terminal có tương tác)
- Bot phải được userbot **/start** trước khi harvest file_id
- Video khi clone chỉ gửi **thumbnail** nếu bật `video_thumbnail_only`
- Progress clone/caption lưu tự động — có thể resume sau khi dừng
