#!/bin/bash
echo "Starting Emergency NLP System on Render..."
echo "Starting Gunicorn server..."

export FLASK_APP=Lomba.py
export FLASK_ENV=production

exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 Lomba:app