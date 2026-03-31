#!/usr/bin/env bash
set -o errexit

export PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/.playwright

pip install -r requirements.txt
python -m playwright install chromium
python manage.py migrate
python manage.py collectstatic --noinput
