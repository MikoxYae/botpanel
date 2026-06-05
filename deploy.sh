#!/bin/bash
# BotPanel VPS Deploy Script
set -e

APP_DIR="/opt/botpanel"
SERVICE_NAME="botpanel"
PORT=5000

echo "📦 Updating code..."
cd $APP_DIR
git pull origin main

echo "📚 Installing dependencies..."
source venv/bin/activate
pip install -r requirements.txt -q

echo "🔄 Restarting service..."
systemctl restart $SERVICE_NAME
systemctl enable $SERVICE_NAME

echo "✅ Deploy complete! Running on port $PORT"
systemctl status $SERVICE_NAME --no-pager
