#!/bin/bash
echo "Starting Emergency NLP System on Render..."

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | xargs)
fi

# Start the application with Gunicorn
echo "Starting Gunicorn server..."
exec gunicorn --config gunicorn.conf.py "Lomba:flask_app" \
    --bind "0.0.0.0:${PORT:-5000}" \
    --workers "${WEB_CONCURRENCY:-2}" \
    --timeout 120
