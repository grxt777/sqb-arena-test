#!/bin/bash
# ATM Monitor — Startup Script

echo "🚀 Starting ATM Monitor..."

echo "📦 Installing dependencies..."
python3 -m pip install -r requirements.txt

export PYTHONPATH=$PYTHONPATH:$(pwd)/api

echo "🌐 Starting FastAPI server on http://localhost:8000"
echo "   Dashboard: http://localhost:8000/dashboard/index.html"
echo "   Swagger:   http://localhost:8000/docs"
echo "   Import ATM: POST /api/atms/import (XLSX)"
echo "================================================"

python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
