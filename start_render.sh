#!/bin/bash

echo "🚀 Starting Emergency NLP System on Render..."
echo "📦 Environment: Production"
echo "🔌 Port: $PORT"

# Pastikan PORT terisi
if [ -z "$PORT" ]; then
  echo "❌ Error: PORT environment variable is not set"
  exit 1
fi

echo "⚙️  Starting Gunicorn server..."
echo "➡️  App module: Lomba:app"
echo "🔧 Workers: 1 (sync), Timeout: 120s"

# Jalankan Gunicorn
gunicorn \
  --bind 0.0.0.0:$PORT \
  --workers 1 \
  --worker-class sync \
  --timeout 120 \
  --keep-alive 5 \
  --max-requests 1000 \
  --max-requests-jitter 100 \
  --log-level info \
  --access-logfile - \
  --error-logfile - \
  Lomba:app

# Tangkap status exit
if [ $? -ne 0 ]; then
  echo "❌ Gunicorn failed to start"
  exit 1
fi