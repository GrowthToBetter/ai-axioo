#!/bin/bash
echo "Starting Emergency NLP System on Render..."

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | xargs)
fi

# Start the application with Gunicorn
echo "Starting Gunicorn server..."
gunicorn --config gunicorn.conf.py Lomba:flask_app