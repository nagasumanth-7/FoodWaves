#!/bin/bash
set -e

echo "=== Building Food Waves on Vercel ==="
python3 -m pip install -r requirements.txt
python3 manage.py collectstatic --noinput --clear
echo "=== Static files collected successfully ==="

