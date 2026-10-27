
#!/bin/bash
set -e

echo "=== Building FoodWaves on Vercel ==="

python3 -m venv .venv

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python manage.py collectstatic --noinput

echo "=== Static files collected successfully ==="
