#!/bin/bash
set -e

echo "=== Building FoodWaves on Vercel ==="

python3 -m venv .venv

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python manage.py collectstatic --noinput

# Mirror static files inside staticfiles/static to guarantee direct resolution
mkdir -p staticfiles/static
cp -r staticfiles/restaurant staticfiles/static/ 2>/dev/null || true
cp -r staticfiles/admin staticfiles/static/ 2>/dev/null || true

echo "=== Static files collected successfully ==="
