#!/bin/bash
# Emergency NLP System Startup Script - Supabase Edition

echo "🚨 Starting Emergency NLP System with Supabase..."

# Install dependencies
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

# Setup environment variables
export FLASK_APP=emergency_nlp_system.py
export FLASK_ENV=production

# Load environment variables dari .env file
if [ -f .env ]; then
    echo "📋 Loading environment variables from .env..."
    export $(cat .env | xargs)
fi

# Test Supabase connection
echo "🔗 Testing Supabase connection..."
python -c "
from emergency_nlp_system import SupabasePostgreSQLManager
manager = SupabasePostgreSQLManager()
conn = manager.get_connection()
if conn:
    print('✅ Supabase connection successful')
    conn.close()
else:
    print('❌ Supabase connection failed')
    exit(1)
"

# Start the application
echo "🔧 Starting Flask webhook server..."
python emergency_nlp_system.py --mode server --port 5000 &

echo "✅ Emergency NLP System started successfully!"
echo "📱 WhatsApp webhook: http://your-domain:5000/webhook/whatsapp"
echo "📊 API Reports: http://your-domain:5000/api/reports"
echo "🏥 Health Check: http://your-domain:5000/health"
echo "🗄️  Database: Supabase PostgreSQL"
