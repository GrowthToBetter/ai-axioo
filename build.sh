#!/bin/bash
echo "Starting build process..."

# Install dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt

# Setup environment variables
echo "Setting up environment variables..."
export FLASK_APP=Lomba.py
export FLASK_ENV=production


# Remove unnecessary files
echo "Cleaning up unnecessary files..."
rm -f *.pyc __pycache__/*.pyc *.pyo *.pyd
find . -type d -name "__pycache__" -exec rm -rf {} +

echo "Build completed successfully!"