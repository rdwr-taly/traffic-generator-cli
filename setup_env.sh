#!/bin/bash
# Basic environment setup for local development
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
echo "Environment ready. Run 'uvicorn container_control:app --host 0.0.0.0 --port 8080' to start." 
