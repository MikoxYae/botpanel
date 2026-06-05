# 🌸 Yae Miko Dashboard

**Telegram Bot VPS Manager** — Track users, payments, monthly reports for hosted Telegram bots.

Live: `http://140.245.234.199:5000` | Login: `admin` / *(set your own password in Settings)*

---

## ✨ Features

| Feature | Description |
|---|---|
| 👥 **Users** | Add/edit/delete bot users, track start dates |
| 💰 **Payments** | Mark paid/pending per month, per-month notes |
| 📊 **Monthly Reports** | Full year overview with revenue chart |
| 📋 **Live Logs** | Activity log with auto-refresh + filter |
| 🔗 **Quick Links** | Custom sidebar buttons (GitHub, etc.) |
| 🗑 **Trash** | Soft-delete with restore option |
| 📥 **CSV Export** | Monthly payment export |

---

## 🚀 Deployment on VPS (Current Setup)

### Prerequisites

```bash
apt update && apt install -y python3 python3-pip python3-venv git postgresql postgresql-contrib sshpass
```

### 1. Clone & Setup

```bash
git clone https://github.com/MikoxYae/botpanel.git /opt/botpanel
cd /opt/botpanel
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
mkdir -p /opt/botpanel/logs
```

### 2. PostgreSQL Setup

```bash
sudo -u postgres psql -c "CREATE USER botpanel WITH PASSWORD 'yourpassword';"
sudo -u postgres psql -c "CREATE DATABASE botpanel OWNER botpanel;"
```

Initialize DB:
```bash
source venv/bin/activate
BOTPANEL_DB_URL="postgresql://botpanel:yourpassword@localhost/botpanel" \
  python3 -c "from app import init_db; init_db()"
```

### 3. Systemd Service ✅ (Current Setup)

```bash
cat > /etc/systemd/system/botpanel.service << 'EOF'
[Unit]
Description=Yae Miko Dashboard — Telegram VPS Manager
After=network.target

[Service]
User=root
WorkingDirectory=/opt/botpanel
Environment=SECRET_KEY=your-strong-secret-key-here
Environment=BOTPANEL_DB_URL=postgresql://user:pass@host/db
Environment=LOG_FILE=/opt/botpanel/logs/activity.log
Environment=PORT=5000
ExecStart=/opt/botpanel/venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 app:app \
    --access-logfile /opt/botpanel/logs/access.log \
    --error-logfile /opt/botpanel/logs/error.log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable botpanel
systemctl start botpanel
systemctl status botpanel
```

### 4. Update / Redeploy

```bash
cd /opt/botpanel && git pull origin main && systemctl restart botpanel
```

---

## 🐳 Docker Deployment

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/logs
ENV PORT=5000
ENV LOG_FILE=/app/logs/activity.log
EXPOSE 5000
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "app:app", \
     "--access-logfile", "/app/logs/access.log", \
     "--error-logfile", "/app/logs/error.log"]
```

### docker-compose.yml

```yaml
version: "3.9"
services:
  db:
    image: postgres:15
    restart: always
    environment:
      POSTGRES_DB: botpanel
      POSTGRES_USER: botpanel
      POSTGRES_PASSWORD: yourpassword
    volumes:
      - pgdata:/var/lib/postgresql/data

  web:
    build: .
    restart: always
    ports:
      - "5000:5000"
    environment:
      BOTPANEL_DB_URL: postgresql://botpanel:yourpassword@db/botpanel
      SECRET_KEY: your-strong-secret-key
      LOG_FILE: /app/logs/activity.log
    volumes:
      - ./logs:/app/logs
    depends_on:
      - db

volumes:
  pgdata:
```

```bash
# Start
docker compose up -d

# View logs
docker compose logs -f web

# Update
git pull && docker compose build web && docker compose up -d web
```

---

## 🖥️ tmux — Keep Running After SSH Disconnect

```bash
# Create session
tmux new -s botpanel

# Inside tmux — run manually if not using systemd:
cd /opt/botpanel && source venv/bin/activate
gunicorn -w 2 -b 0.0.0.0:5000 app:app

# Detach (keeps running): Ctrl+B then D
# Reattach:
tmux attach -t botpanel

# List all sessions:
tmux ls

# Kill:
tmux kill-session -t botpanel
```

## 🖥️ screen — Alternative to tmux

```bash
screen -S botpanel          # create
# Detach: Ctrl+A then D
screen -r botpanel          # reattach
screen -ls                  # list
```

> **Production tip:** Use **systemd** (above) — it auto-starts on reboot and restarts on crash.
> tmux/screen are for one-off manual testing only.

---

## 📁 Project Structure

```
botpanel/
├── app.py                  # Flask app — models, routes, logic
├── requirements.txt        # Python deps
├── README.md               # This file
├── static/
│   ├── css/style.css       # Dark navy theme
│   └── js/main.js
├── templates/
│   ├── base.html           # Layout + sidebar
│   ├── login.html          # Login page
│   ├── dashboard.html      # Overview + chart
│   ├── users.html          # User list + payment status by month
│   ├── add_user.html       # Add user form
│   ├── edit_user.html      # Edit + backfill missing records
│   ├── payments.html       # Payments by month/year + notes
│   ├── monthly.html        # Annual report
│   ├── logs.html           # Live activity log viewer
│   ├── trash.html          # Soft-deleted users
│   └── settings.html       # Quick links + password change
└── logs/
    └── activity.log        # Auto-created on first action
```

---

## 🗄️ Database Models

| Model | Key Fields |
|---|---|
| **Admin** | username, password_hash |
| **BotUser** | name, telegram_id, bot_name, plan, amount, start_date, deleted_at |
| **Payment** | user_id, month, year, amount, paid, paid_date, note |
| **QuickLink** | title, url, icon |

---

## 🛠️ Nginx Reverse Proxy + SSL (Optional)

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
apt install nginx certbot python3-certbot-nginx
# Save config above to /etc/nginx/sites-available/botpanel
ln -s /etc/nginx/sites-available/botpanel /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d yourdomain.com
```
