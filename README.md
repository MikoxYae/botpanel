# BotPanel — Telegram VPS Bot Manager

A Python/Flask dashboard to manage Telegram bot users hosted on your VPS.

## Features
- **User Management** — Add, edit, delete users with Telegram ID and bot info
- **Payment Tracking** — Mark paid/unpaid per user per month
- **Monthly Reports** — View and export data month-by-month
- **CSV Export** — Download payment records for any month
- **Revenue Charts** — Visual revenue trend across months

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Default login: `admin` / `admin123` — **change after first login**.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | changeme | Flask session secret |
| `DATABASE_URL` | sqlite:///botpanel.db | Database URL |
| `PORT` | 5000 | Port to run on |

## Deploy with systemd

```bash
# /etc/systemd/system/botpanel.service
[Unit]
Description=BotPanel
After=network.target

[Service]
User=root
WorkingDirectory=/opt/botpanel
Environment="SECRET_KEY=your-secret-here"
ExecStart=/opt/botpanel/venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```
