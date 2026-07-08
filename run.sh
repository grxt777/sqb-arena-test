#!/bin/bash
# ATM Cash Intelligence - Startup Script

echo "🚀 Starting ATM Cash Intelligence..."

# 1. Install dependencies
echo "📦 Checking and installing dependencies..."
python3 -m pip install -r requirements.txt

# 2. Set PYTHONPATH to include the api directory
export PYTHONPATH=$PYTHONPATH:$(pwd)/api

# 3. Start the server
echo "🌐 Starting FastAPI server on http://localhost:8000"
python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
