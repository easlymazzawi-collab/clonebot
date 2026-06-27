# Telegram Forum → Share-Link System

Hệ thống gồm 2 thành phần chạy song song:

| Thành phần | Công nghệ | Mục đích |
|---|---|---|
| **Cloner** | Telethon (user account) | Clone forum sang định dạng share-link |
| **Share Bot** | aiogram 3 (bot) | Phục vụ media khi user bấm link |

---

## Kiến trúc

```
Forum Nguồn                     Forum Đích (mới)
┌─────────────┐   Cloner    ┌──────────────────────────────┐
│ Topic A     │ ──────────▶ │ Topic A                      │
│  [video]    │             │  [thumbnail.jpg]             │
│  [photo]    │             │  "Nhấp link để xem: t.me/..." │
│  [doc]      │             │  [photo]                     │
└─────────────┘             │  "Nhấp link để xem: t.me/..." │
                            └──────────────────────────────┘
                                          │
                                    Link click
                                          ▼
                            Storage Channel (private)
                            ┌──────────────────────────────┐
                            │ [original video]             │
                            │ [original doc]               │
                            └──────────────────────────────┘
                                          │
                                    Bot trả media
                                          ▼
                                    User nhận file
```

---

## Cài đặt

```bash
pip install -r requirements.txt
cp .env.example .env
# Điền thông tin vào .env
```

### Cấu hình `.env`

```env
# Tài khoản Telegram của bạn (để chạy Cloner)
API_ID=28030819
API_HASH=your_api_hash
PHONE=+84xxxxxxxxx

# Bot Token (tạo qua @BotFather)
BOT_TOKEN=123456789:AAxxxxxxxx
BOT_USERNAME=your_bot_username

# Kênh lưu trữ riêng tư (bot phải là admin ở đây)
STORAGE_CHANNEL=-1001234567890

# Danh sách admin (user_id, cách nhau dấu phẩy)
ADMIN_IDS=123456789,987654321
```

### Chuẩn bị Storage Channel

1. Tạo một **kênh riêng tư** trên Telegram
2. Thêm **bot** của bạn làm admin (quyền: post messages)
3. Thêm **tài khoản user** của bạn làm admin
4. Lấy channel ID (dùng @userinfobot hoặc API) và điền vào `STORAGE_CHANNEL`

---

## Chạy

```bash
# Menu tương tác
python3 main.py

# Chỉ bot
python3 main.py bot

# Chỉ cloner (tương tác)
python3 main.py clone

# Cả hai (bot nền + cloner tương tác)
python3 main.py both
```

---

## Tính năng

### Cloner (Telethon)

- Clone toàn bộ cấu trúc topic (tên, icon, màu) từ forum nguồn sang đích
- Với mỗi tin nhắn có media:
  - **Video** → tải thumbnail → gửi ảnh thumbnail + caption link
  - **Ảnh** → gửi ảnh trực tiếp + caption link  
  - **Tài liệu / Audio / Voice / Animation** → gửi file + caption link
  - **Text thuần** → copy nguyên format (in đậm, link, emoji premium...)
- Hỗ trợ album (gom nhóm ảnh/video)
- Resume tự động nếu bị gián đoạn (state files)
- Quản lý nhiều phiên song song
- Filter nội dung: tất cả / chỉ media / chỉ video / chỉ ảnh
- Tùy chọn ẩn tên người gửi khi lưu vào storage

### Share Bot (aiogram 3)

#### User

| Lệnh | Mô tả |
|---|---|
| `/start` | Chào mừng + hướng dẫn |
| `/start TOKEN` | Nhận media từ link chia sẻ |

Khi user bấm link `t.me/bot?start=TOKEN`, bot gửi ngay bản gốc chất lượng cao.

#### Admin

| Lệnh | Mô tả |
|---|---|
| `/admin` | Mở admin panel (inline buttons) |
| `/stats` | Thống kê: users, media, lượt truy cập |
| `/users` | Danh sách 20 user gần nhất |
| `/broadcast` | Phát tin tới tất cả users (hỗ trợ media) |
| `/push TOKEN CHAT_ID [copy\|forward]` | Đẩy media tới chat bất kỳ |
| `/ban USER_ID [lý do]` | Cấm user |
| `/unban USER_ID` | Bỏ cấm user |
| `/addadmin USER_ID` | Thêm admin |
| `/removeadmin USER_ID` | Xóa admin |
| `/delete TOKEN` | Xóa entry media khỏi database |
| `/cancel` | Hủy thao tác hiện tại |

#### Forward ẩn danh / có tên

- **`/push TOKEN CHAT_ID copy`** — gửi không có "Forwarded from" (ẩn nguồn)
- **`/push TOKEN CHAT_ID forward`** — forward thông thường (hiện nguồn)

#### Quick Store (Admin)

Admin có thể **forward bất kỳ media nào** đến bot → bot tự động lưu vào storage channel và trả về link chia sẻ kèm nút "Push (copy)" / "Push (forward)".

---

## Cấu trúc file

```
telegram_media_share/
├── config.py       # Cấu hình từ .env
├── database.py     # SQLite async (media, users, admins, broadcasts)
├── cloner.py       # Telethon forum cloner
├── bot.py          # aiogram 3 share-link bot
├── main.py         # Entry point
├── requirements.txt
└── .env.example
```

### File state (tự sinh khi chạy)

```
cloner_session.session    # Phiên Telethon
media_share.db            # Database SQLite
cloner_state_*.txt        # Trạng thái resume mỗi topic
cloner_topicmap_*.txt     # Mapping topic nguồn → đích
cloner_meta_*.json        # Metadata phiên làm việc
```

---

## Caption format

Khi video được clone sang forum đích:

```
[Thumbnail image]

Caption gốc (nếu có)

📎 Nhấp để xem: https://t.me/BOT?start=TOKEN
```

Khi không có caption gốc:

```
[Thumbnail image]

Nhấp vào link để xem:
https://t.me/BOT?start=TOKEN
```

---

## Lưu ý

- Tài khoản Telegram user cần có quyền xem source forum và gửi vào storage channel
- Bot cần là admin của storage channel (quyền post messages)
- Nếu source forum bật "Restrict saving content", cloner sẽ bỏ qua các tin nhắn đó
- Token có 12 ký tự (a-zA-Z0-9), đủ entropy cho hàng tỷ entries
