#!/bin/bash
echo "Starting Emergency NLP System on Render..."

# Set environment variables
export FLASK_APP=Lomba.py
export FLASK_ENV=production

# For local testing, you can test the health endpoint first:
# python -c "
# import os
# from Lomba import app
# print('Testing Flask app...')
# with app.test_client() as client:
#     response = client.get('/health')
#     print(f'Health check: {response.status_code}')
# "

echo "Starting Gunicorn server..."
echo "App module: Lomba:app"
echo "Port: $PORT"

# Start Gunicorn with the correct app reference
exec gunicorn \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --worker-class sync \
    --timeout 120 \
    --keep-alive 60 \
    --max-requests 100 \
    --max-requests-jitter 10 \
    --log-level info \
    Lomba:app