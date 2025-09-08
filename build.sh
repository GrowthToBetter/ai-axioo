#!/usr/bin/env bash
# build.sh - Render Build Script for Emergency NLP System

set -o errexit

echo "🚀 Starting Render deployment build..."

# Update system packages
echo "📦 Updating system packages..."
apt-get update

# Install system dependencies for audio processing
echo "🔊 Installing system dependencies..."
apt-get install -y \
    portaudio19-dev \
    python3-pyaudio \
    flac \
    ffmpeg \
    libportaudio2 \
    libportaudiocpp0 \
    libasound-dev \
    build-essential

# Install Python dependencies
echo "🐍 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Set up environment
echo "🔧 Setting up environment..."

# Create necessary directories
mkdir -p logs
mkdir -p temp
mkdir -p audio

# Set permissions
chmod +x start_render.sh

echo "✅ Build completed successfully!"
echo "🚨 Emergency NLP System ready for deployment"