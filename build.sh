#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python -m playwright install chromium
python manage.py migrate
python manage.py collectstatic --noinput
