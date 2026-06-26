# TG Forum Clone — Control Panel

Giao diện quản lý hệ thống clone Telegram Forum, gồm 3 thành phần phối hợp: **Userbot**, **Bot** và **Forum**.

## Tính năng

### Userbot (Telethon)
- Clone toàn bộ topics + messages từ forum nguồn sang forum đích
- Video → chỉ lấy thumbnail, không clone file nặng
- Album giữ nguyên nhóm (1 API call / album)
- Ẩn nguồn với `drop_author=True`
- Resume qua state file sau khi ngắt kết nối
- Batch 50 msg/call, flood wait tự retry

### Bot (python-telegram-bot)
- Lưu `file_id` gốc từ forum nguồn vào SQLite
- Tạo token `alb_XXXXX` cho từng album/media
- Khi user bấm link bot → trả album gốc, ẩn nguồn
- Giữ nguyên album (không tách lẻ)
- Giữ caption gốc khi trả media

### Caption Editor
- Thêm link bot vào cuối mỗi bài cloned
- Template: `{caption_goc} — Nhấp vào link này để xem: {bot_link}`
- Streaming edit, resume được, giữ premium emoji entities

## Giao diện HTML

Mở `index.html` bằng trình duyệt — không cần server.

### Các trang
| Trang | Mô tả |
|-------|-------|
| Dashboard | Tổng quan thống kê, pipeline, log thời gian thực |
| Workflow | Sơ đồ kiến trúc và từng bước hoạt động |
| Userbot | Cấu hình API ID/Hash, session, tùy chỉnh |
| Bot | Token, caption template, thống kê lượt xem |
| Forum | Quản lý kênh nguồn/đích, topic map |
| Clone Topics | Chạy/dừng tiến trình, theo dõi tiến độ |
| Caption Editor | Edit caption hàng loạt theo topic |
| Media Manager | Database albums, file_id map, thumbnail preview |
| Logs | Log toàn bộ hoạt động, bộ lọc |
| Cài đặt | Database, hiệu năng, bảo mật |

## Cài đặt Python

```bash
pip install telethon python-telegram-bot aiohttp cryptg aiosqlite
```

## Cấu trúc dự án (sẽ phát triển)

```
clonebot/
├── index.html              # Giao diện quản lý
├── userbot/
│   ├── clone_forum.py      # Backup forum (Phase 1 + 2)
│   └── caption_editor.py   # Caption editor v3.1
├── bot/
│   ├── bot_main.py         # Bot handler
│   └── media_db.py         # SQLite database
└── config/
    └── settings.json       # Cấu hình chung
```
