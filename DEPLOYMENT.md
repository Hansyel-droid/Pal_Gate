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
Copy `.env.example` to `.env` and fill in real values — `.env` is gitignored
and should never be committed:
```
SECRET_KEY=<generate with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())">
DEBUG=False
ALLOWED_HOSTS=192.168.x.x,your-server-hostname
HTTPS_ENABLED=False
TRUST_X_FORWARDED_FOR=False
API_KEYS=<generate a random key per gate — see below>
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

**There is no insecure default for `API_KEYS` or `DEBUG` anymore** — a
missing/misconfigured `.env` will now fail to start instead of silently
falling back to something guessable. Generate a real random key per gate:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**About `HTTPS_ENABLED=False`:** with no TLS certificate in front of the
server, login credentials and session cookies currently travel in
plaintext over the campus network. That's an accepted short-term risk for
an internal LAN deployment, but it should not stay that way — once a
certificate is available (even a self-signed one issued by an internal CA,
or a free one via a reverse proxy like Caddy/nginx + Let's Encrypt if the
server is reachable for ACME), put that reverse proxy in front of Django
and flip `HTTPS_ENABLED=True`. Only set `TRUST_X_FORWARDED_FOR=True` once
that reverse proxy is actually the only thing that can reach Django
directly — otherwise a client can forge its own IP in the audit log.

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
- API Key header: `X-API-Key: <the key you generated for this gate in API_KEYS>`
- Endpoints:
  - Scan: `POST http://192.168.x.x:8000/api/scan/`
  - Register UID: `POST http://192.168.x.x:8000/api/register-uid/`
  - Gate status: `GET http://192.168.x.x:8000/api/gate-status/`

If a key is ever suspected to be compromised (e.g. a device is lost or the
source is shared), generate a new one, update `.env`, restart the server,
and reflash every device that used the old key — old keys are rejected
immediately since there's no fallback.

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
