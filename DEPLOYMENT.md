# PalSU Gate System — Deployment Guide

## Requirements
- Python 3.10+
- Windows Server or any machine on the university local network

## Setup Steps

### 1. Copy project to server
Place the entire `palsu_gate/` folder on the server.

### 2. Create virtual environment
```bash
cd palsu_gate
python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate    # Linux/Mac
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure .env
Edit `.env` with your server's real values:
```
SECRET_KEY=<generate with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())">
DEBUG=False
ALLOWED_HOSTS=192.168.x.x,your-server-hostname
HTTPS_ENABLED=False
API_KEYS=your-main-gate-key,your-side-gate-key
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

### 5. Collect static files
```bash
python manage.py collectstatic --noinput
```

### 6. Run migrations
```bash
python manage.py migrate
```

### 7. Create superuser
```bash
python manage.py createsuperuser
```
Then go to `/palsu-system-admin-2025/` and set the user's role to `admin`.

### 8. Start server

**For local network (simple):**
```bash
python manage.py runserver 0.0.0.0:8000
```
Access at: `http://192.168.x.x:8000`

**For production (recommended):**
```bash
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
```

## ESP32 Configuration
Update your Arduino sketch with:
- Server IP: your server's local IP
- Port: 8000
- API Key header: `X-API-Key: your-main-gate-key`
- Endpoints:
  - Scan: `POST http://192.168.x.x:8000/api/scan/`
  - Register UID: `POST http://192.168.x.x:8000/api/register-uid/`

## Daily Maintenance
Run backup manually or schedule with Task Scheduler:
```bash
python backup.py
```

Run temp file cleanup:
```bash
python manage.py cleanup_temp_files
```

## Admin Panel
URL: `/palsu-system-admin-2025/`
Use to: create admin/security accounts, view audit logs, manage all data.
