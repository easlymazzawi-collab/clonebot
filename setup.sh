#!/usr/bin/env bash
# Setup script for TG Forum Clone (Linux / macOS / WSL)
set -e

cd "$(dirname "$0")"

echo "========================================"
echo "  TG Forum Clone - Setup"
echo "========================================"
echo

# 1. Copy config if missing
if [ ! -f "config/settings.json" ]; then
    echo "[1/3] Tạo config/settings.json ..."
    cp config/settings.example.json config/settings.json
    echo "      → Hãy sửa config/settings.json trước khi chạy."
else
    echo "[1/3] config/settings.json đã có."
fi

# 2. Install / upgrade Python deps
echo "[2/3] Cài dependencies ..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

# 3. Quick import check
echo "[3/3] Kiểm tra Telethon ..."
python - <<'EOF'
import telethon
from shared.telethon_compat import CreateForumTopicRequest  # noqa: F401
print(f"      Telethon {telethon.__version__} OK")
EOF

echo
echo "========================================"
echo "  Xong! Chạy:"
echo "    python run.py"
echo "  Mở trình duyệt: http://localhost:8080"
echo "========================================"
