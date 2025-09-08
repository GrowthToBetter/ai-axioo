#!/usr/bin/env bash
# start_render.sh - Render Start Script

set -o errexit

echo "🚨 Starting Emergency NLP System on Render..."

# Set environment variables for production
export FLASK_APP=Lomba.py
export FLASK_ENV=production
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Create necessary directories
mkdir -p logs temp audio

# Start the application with Gunicorn
echo "🔧 Starting Flask application with Gunicorn..."
exec gunicorn \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --worker-class sync \
    --worker-connections 1000 \
    --timeout 120 \
    --keepalive 2 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --capture-output \
    "Lomba:app"