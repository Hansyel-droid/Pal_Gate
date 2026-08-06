# PalSU Gate System — Deployment Guide (Oracle Cloud)

**Who this is for:** whoever sets this up on the real server. Commands are
shown exactly as typed. Where something needs *your* details (an IP,
a domain, a path) it's written in `CAPS` — replace it, don't type it
literally.

This replaces the old LAN-only guide. The system now runs on a real
Oracle Cloud virtual machine, reachable from anywhere — not just the
campus network — so a few extra pieces (a firewall, HTTPS) are part of
setup that a LAN deployment didn't need.

---

## 0. Choosing the VM (do this once, in the Oracle Cloud console)

Oracle's Always Free tier actually offers **two different free machines** —
pick one:

| | AMD **E2.1.Micro** | Ampere **A1.Flex** (ARM) |
|---|---|---|
| Power | 1/8 of a CPU core, 1 GB RAM | Up to 4 full CPU cores, 24 GB RAM |
| Reliability of getting one | Always available instantly | Sometimes shows "Out of host capacity" — a known, temporary Oracle issue; retrying later or trying a different Availability Domain usually works within a day or two |
| Recommended for this app | Only if Ampere truly won't provision | **Yes — this is the one to use** |

**Use Ampere (A1.Flex).** The load testing done on this app found that CPU
cores — not the database — are what limits how many people can be served
at the exact same moment. The Micro shape's 1/8 core would make that limit
quite low; Ampere's real cores give this app actual headroom. It's still
100% free, forever, under Always Free — just request a smaller slice of it
(e.g. 2 OCPUs / 12 GB) rather than the full 4/24, so there's room to run a
second free instance later if ever needed.

When creating the instance:
- **Region:** Singapore, if offered — closest to Palawan.
- **Image:** Ubuntu 22.04 (canonical, "aarch64"/arm64 build if using Ampere).
- **Shape:** Ampere A1.Flex, 2 OCPU / 12 GB to start (adjustable later).
- **SSH key:** generate one if you don't have one (`ssh-keygen -t ed25519`
  on your own laptop), paste the **public** key (`.pub` file) into the
  console. Keep the private key safe — it's the only way to log in.
- Note the instance's **public IP address** once it's running.

---

## 1. Open the firewall (two separate layers — both are needed)

Oracle blocks traffic in two places, and it's easy to only remember one:

**A. The cloud firewall (Security List)** — in the console: your VCN →
Security Lists → Default Security List → Add Ingress Rules:
- Source `0.0.0.0/0`, TCP, destination port `80`
- Source `0.0.0.0/0`, TCP, destination port `443`
(Port 22/SSH is already open by default.)

**B. The instance's own firewall (iptables)** — Oracle's Ubuntu images
ship with `iptables` rules that block everything except SSH, even though
you just opened it in the console above. SSH in and run:
```bash
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```
Skipping step B is the single most common reason "I opened the port but
it still doesn't load" happens on Oracle specifically.

---

## 2. First login and server prep

```bash
ssh -i /path/to/your/private-key ubuntu@YOUR_VM_PUBLIC_IP

sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip nginx certbot python3-certbot-nginx git sqlite3 ufw
```

---

## 3. Get the code onto the server

```bash
cd ~
git clone YOUR_REPO_URL palsu_gate
cd palsu_gate
```
(No git remote yet? `scp -r` the project folder from your laptop instead —
same end result, just a manual copy instead of a repo you can `git pull`
to update later. Setting up a repo is worth doing before this step if at
all possible — see Section 10.)

---

## 4. Virtual environment and dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 5. Configure `.env`

```bash
cp .env.example .env
nano .env
```

Fill in:
```
SECRET_KEY=<generate: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())">
DEBUG=False
ALLOWED_HOSTS=YOUR_VM_PUBLIC_IP,your-domain.if-you-have-one
CSRF_TRUSTED_ORIGINS=
HTTPS_ENABLED=False
TRUST_X_FORWARDED_FOR=False
API_KEYS=<generate a random key per gate — see ESP32 section below>
BACKUP_DIR=
```
Leave `HTTPS_ENABLED=False` and `CSRF_TRUSTED_ORIGINS` blank for now —
those get set correctly in Section 9, after HTTPS is actually working.
Setting them too early locks you out before there's a certificate to
back it up.

For `EMAIL_*` values, see the existing "Applicant Email Notifications"
section further down — that part hasn't changed.

**About `BACKUP_DIR`:** the VM's disk is real and persists, so backups
work correctly with no `BACKUP_DIR` set — but they'd still live on the
*same machine* as the live data, which doesn't help if the VM itself is
ever lost or misconfigured. If you want a genuinely offsite copy with
no extra cost, `rclone` synced to a Google Drive account is a common
free option — worth doing once things are stable, not a blocker for
initial deployment.

---

## 6. Static files, database, admin account

```bash
python manage.py collectstatic --noinput
python manage.py migrate
python manage.py createsuperuser
```
Then visit `/palsu-system-admin-2025/` later and set that user's role to
`admin`.

---

## 7. Run gunicorn as a real service (systemd)

**First, check how many CPU cores this VM actually has:**
```bash
nproc
```
Gunicorn's worker count should be **2 × cores + 1**. This matters — load
testing this exact app found that using *more* workers than the machine
has cores makes things slower, not faster, because they end up fighting
each other for the same CPU instead of running in parallel. If `nproc`
says `2`, use 5 workers. If it says `4`, use 9. Don't reuse a number from
a different machine.

Create the service file:
```bash
sudo nano /etc/systemd/system/palsu-gate.service
```
```ini
[Unit]
Description=PalSU Gate System (gunicorn)
After=network.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=/home/ubuntu/palsu_gate
EnvironmentFile=/home/ubuntu/palsu_gate/.env
ExecStart=/home/ubuntu/palsu_gate/venv/bin/gunicorn config.wsgi:application \
    --bind 127.0.0.1:8000 \
    --workers YOUR_WORKER_COUNT
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
Notice it binds to `127.0.0.1`, not `0.0.0.0` — gunicorn should never be
reachable directly from the internet. Only nginx (next section) should
be able to reach it; the public internet talks to nginx, which talks to
gunicorn locally.

```bash
sudo systemctl daemon-reload
sudo systemctl enable palsu-gate
sudo systemctl start palsu-gate
sudo systemctl status palsu-gate
```
You want `active (running)` in green. This is also the exact command
`RUNBOOK.md` tells an operator to run if the site ever needs restarting.

---

## 8. nginx as the public-facing reverse proxy

```bash
sudo nano /etc/nginx/sites-available/palsu-gate
```
```nginx
server {
    listen 80;
    server_name YOUR_VM_PUBLIC_IP your-domain.if-you-have-one;

    # The applicant portal allows uploads up to 10 MB (OR/CR, licence,
    # COR). nginx's own default cap is 1 MB — every document upload would
    # silently fail with this line missing.
    client_max_body_size 12M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
(Static files don't need a separate nginx location — WhiteNoise already
serves them efficiently straight from Django with far-future caching, so
there's nothing extra to configure there.)

```bash
sudo ln -s /etc/nginx/sites-available/palsu-gate /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```
Visit `http://YOUR_VM_PUBLIC_IP` — the login page should load. If it
doesn't, re-check Section 1 (both firewall layers) before anything else.

---

## 9. HTTPS (only once you have a domain pointed at this VM)

A domain has to exist and its DNS **A record** has to point at
`YOUR_VM_PUBLIC_IP` before this step works — Let's Encrypt verifies
ownership by reaching the domain over the internet. If PSU IT hasn't set
up a subdomain yet, skip this section for now and stay on plain HTTP —
it's not ideal, but the site still works for testing/defense purposes.
Revisit this the moment a domain exists.

```bash
sudo certbot --nginx -d your-domain.psu.palawan.edu.ph
```
Certbot edits the nginx config automatically to add the certificate and a
redirect from HTTP to HTTPS. Confirm it worked:
```bash
curl -I https://your-domain.psu.palawan.edu.ph
```

Now flip the settings that only make sense once HTTPS is actually live:
```bash
nano .env
```
```
HTTPS_ENABLED=True
TRUST_X_FORWARDED_FOR=True
CSRF_TRUSTED_ORIGINS=https://your-domain.psu.palawan.edu.ph
ALLOWED_HOSTS=your-domain.psu.palawan.edu.ph
```
`TRUST_X_FORWARDED_FOR=True` is only safe now because nginx is the *only*
thing that can reach Django directly (gunicorn is bound to 127.0.0.1) —
if that weren't true, a client could forge its own IP in the audit log.
```bash
sudo systemctl restart palsu-gate
```

Certbot installs its own renewal timer automatically. Confirm it's there:
```bash
sudo certbot renew --dry-run
```

---

## 10. Lock down the instance's own firewall properly

Section 1 opened specific ports with raw `iptables` to get started. Once
everything works, switch to `ufw` for something easier to audit later:
```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```
Port 8000 (gunicorn) should **not** appear in this list — it's only bound
to `127.0.0.1` (Section 7), so it was never reachable from outside
anyway. This is just a second layer confirming that.

---

## 11. Daily Maintenance (cron)

```bash
crontab -e
```
```cron
0 2 * * *   cd /home/ubuntu/palsu_gate && venv/bin/python backup.py
0 3 * * 0   cd /home/ubuntu/palsu_gate && venv/bin/python manage.py cleanup_temp_files
0 4 1 * *   cd /home/ubuntu/palsu_gate && venv/bin/python manage.py expire_stickers
```
Same three jobs `RUNBOOK.md` already describes — this is just how they
get registered on this specific machine. Verify one runs cleanly by hand
first:
```bash
cd /home/ubuntu/palsu_gate && venv/bin/python backup.py
```

*(If this project is ever instead hosted on a Windows machine, the
original Task Scheduler instructions for the same three jobs are kept at
the bottom of this document for reference.)*

---

## 12. Deploying an update later

```bash
cd /home/ubuntu/palsu_gate
git pull
source venv/bin/activate
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate
sudo systemctl restart palsu-gate
```

---

## ESP32 gate devices — one thing to plan for

The gate scanners currently talk to the server over plain HTTP on the
local campus network. Once the server is a public HTTPS domain instead of
a LAN IP, the ESP32 firmware needs to speak HTTPS too (`WiFiClientSecure`
instead of a plain `WiFiClient`) — plain HTTP requests to an HTTPS-only
server will simply fail. This is a firmware change on the gate devices,
not a Django change, and is worth testing with one gate device before
rolling it out to all of them. Update the sketch with:
- Server address: the domain (once HTTPS is live) or the VM's public IP
  (if still on plain HTTP)
- API Key header: `X-API-Key: <the key generated for this gate in API_KEYS>`
- Endpoints unchanged: `/api/scan/`, `/api/register-uid/`, `/api/gate-status/`

If a key is ever suspected compromised, generate a new one, update `.env`,
restart the service, and reflash every device using the old key.

---

## Applicant Email Notifications — LIVE (configured)

**Status: real email is configured and verified working**, sending
through the university's Google Workspace account
(`202380158@psu.palawan.edu.ph`) via Gmail's SMTP server.

The system emails applicants when their appointment is scheduled, their
application is approved or rejected, and when their sticker is issued
(`applications/notifications.py`). This only fires for applicants with an
email on file — every account created through the portal has one, since
sign-up requires verifying a one-time code sent to a
`@psu.palawan.edu.ph` address.

Current `.env` values needed:
```
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=202380158@psu.palawan.edu.ph
EMAIL_HOST_PASSWORD=<Google App Password — NOT the account's real login password>
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=PalSU Gate System <202380158@psu.palawan.edu.ph>
```

**If this ever needs to be redone:**
1. Log into the sending Google account → turn on **2-Step Verification**.
2. Go to **myaccount.google.com/apppasswords** → generate a new app
   password.
3. Put it in `EMAIL_HOST_PASSWORD` (16 characters, no spaces) — never the
   account's normal login password.
4. If Google doesn't offer an app password option, the Workspace admin
   has it disabled org-wide — ask PSU IT to enable it for this account.
5. Verify with a real send:
   ```bash
   python manage.py shell -c "from django.core.mail import send_mail; send_mail('Test', 'It works.', 'FROM@ADDRESS', ['TO@ADDRESS'])"
   ```
   `1` means Gmail accepted it — check the inbox (including spam).

A failed send never blocks the admin action that triggered it — failures
are written to `logs/errors.log` instead.

---

## Admin Panel
URL: `/palsu-system-admin-2025/`
Use to: create admin/security accounts, view audit logs, manage all data.

---

## Appendix: Windows Task Scheduler (only if hosting on Windows instead)

For each job (`backup.py`, `manage.py cleanup_temp_files`,
`manage.py expire_stickers`):
1. Task Scheduler → **Create Task** (not "Basic Task").
2. General tab: name it, check **Run whether user is logged on or not**
   and **Run with highest privileges**.
3. Triggers tab: set the schedule (daily/weekly/monthly, matching the
   cron table in Section 11).
4. Actions tab → **Start a program**:
   - Program: `C:\path\to\palsu_gate\venv\Scripts\python.exe`
   - Arguments: `backup.py` (or the relevant `manage.py` command)
   - Start in: `C:\path\to\palsu_gate`
5. Save, then right-click → **Run** once to confirm it completes without
   error.

Command-line equivalent:
```bat
schtasks /create /tn "PalSU Gate - Daily Backup" ^
  /tr "C:\path\to\palsu_gate\venv\Scripts\python.exe C:\path\to\palsu_gate\backup.py" ^
  /sc daily /st 02:00 /ru SYSTEM
```
Verify: `schtasks /query /tn "PalSU Gate - Daily Backup" /v /fo LIST` —
`Last Result` of `0` means success.
