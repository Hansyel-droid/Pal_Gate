# PalawanSU Gate System — Operations Runbook

**Who this is for:** whoever keeps the system running day to day. You do
not need to be a programmer to follow this. Where a command is shown, type
it exactly as written.

**Before you need this:** fill in the blanks below and keep a printed copy
somewhere the IT office can find it.

| | |
|---|---|
| Website address | `_______________________________` |
| Server address / how to log in | `_______________________________` |
| Project folder on the server | `_______________________________` |
| Backup location | `_______________________________` |
| Who to call | `_______________________________` |

---

## 1. The website is down

Work through these in order. Stop as soon as it's working again.

### 1.1 Confirm it's really down

Open the site on a phone using mobile data (not campus WiFi). If it loads
on mobile data but not on a campus computer, the problem is the campus
network, not this system — contact PalawanSU IT.

### 1.2 Restart the application

This fixes most problems and is safe to do at any time. Nothing is lost.

**Linux server:**
```bash
sudo systemctl restart palsu-gate
sudo systemctl status palsu-gate
```
You want to see `active (running)` in green.

**Windows server:**
Open Task Manager → Services tab → find the PalawanSU Gate service →
right-click → Restart.

Wait 30 seconds, then reload the website.

### 1.3 Restart the whole server

If restarting the application didn't help, reboot the machine. The system
is set up to start again on its own after a reboot.

Wait 2–3 minutes after it comes back, then check the website again.

### 1.4 Check the disk isn't full

A full disk stops the system from writing anything and looks like a
mysterious failure.

**Linux:** `df -h`
**Windows:** open File Explorer → This PC → look at the drive's free space

If the disk is nearly full, see section 4.

### 1.5 Look at the error log

```
logs/errors.log
```
Open it and look at the last few entries — the newest are at the bottom.
Send the last 20 lines to whoever is supporting the system. Don't worry
about understanding it; the point is to hand over the exact message.

---

## 2. Restoring from backup

**Do this only if data has actually been lost or the database is
damaged.** Restoring undoes everything that happened since the backup was
made.

### 2.1 Stop the application first

Never restore while the system is running.

**Linux:** `sudo systemctl stop palsu-gate`
**Windows:** Task Manager → Services → stop the PalawanSU Gate service

### 2.2 Set the damaged database aside — do not delete it

Rename it rather than deleting, in case it's still partly readable:

```
db.sqlite3   →   db.sqlite3.damaged-2026-08-06
```

### 2.3 Copy the backup into place

Go to your backup location. Inside you'll find two folders:

- `database/` — files named `db_backup_YYYYMMDD_HHMMSS.sqlite3`
- `media/` — the uploaded documents

Pick the **most recent** database file (the highest date in the name) and
copy it into the project folder, renaming it to exactly:

```
db.sqlite3
```

### 2.4 Restore the documents too

Copy everything from the backup's `media/` folder into the project's
`media/` folder, choosing "keep both" / "skip existing" if asked. The
uploaded files never change once created, so existing ones are already
correct.

**Restoring the database without doing this** leaves every application
pointing at documents that no longer exist.

### 2.5 Start the application and check

Start the service again (reverse of 2.1), open the website, log in as an
administrator, and confirm the Applications list looks right.

---

## 3. Checking backups are actually working

**Do this once a month. It takes two minutes.**

Backups that silently stopped working are the single most common way
organisations lose data — nobody notices until the day they need one.

1. Go to your backup location, open the `database/` folder.
2. Look at the newest file's date. **It should be from today or
   yesterday.** If it's older, backups have stopped — see 3.1.
3. Check there are multiple files, not just one.
4. Check the `media/` folder isn't empty.

### 3.1 If backups have stopped

Run the backup by hand to see the error:

**Linux:**
```bash
cd /path/to/palsu_gate
venv/bin/python backup.py
```

**Windows:**
```
cd C:\path\to\palsu_gate
venv\Scripts\python.exe backup.py
```

It prints what it did, or an explanation of what went wrong. Common
causes: the backup drive isn't plugged in or mounted, the network share
is unavailable, or the disk is full.

---

## 4. The disk is filling up

Three things grow over time. In order of how much space they use:

**Uploaded documents (`media/`)** — grows with each application. This is
real data; don't delete it. If space is short, move older backups off the
machine instead.

**Old backups** — the system keeps the last 30 database snapshots
automatically. The document mirror keeps everything on purpose. If space
is tight, copy the backup folder somewhere else (external drive, cloud
storage) and then clear out what you moved.

**Logs (`logs/`)** — safe to delete files older than a few months.

---

## 5. Routine jobs — what should be running

Three scheduled jobs keep the system healthy. If any stops, nothing breaks
immediately, but problems build up quietly.

| Job | How often | What happens if it stops |
|---|---|---|
| `backup.py` | Daily | No new backups — you find out at the worst time |
| `manage.py cleanup_temp_files` | Weekly | Abandoned half-finished uploads pile up |
| `manage.py expire_stickers` | Monthly | Expired stickers keep working at the gate |

To check they're set up: see the "Daily Maintenance" section of
`DEPLOYMENT.md`.

---

## 6. Common questions

**An applicant says they never got their email.**
Ask them to check their spam folder first. Emails go only to
`@psu.palawan.edu.ph` addresses. If nobody at all is receiving email, the
sending account's app password may have been revoked — see "Applicant
Email Notifications" in `DEPLOYMENT.md`.

**Someone is locked out of their account.**
Five wrong passwords locks an account for 30 minutes. It unlocks by
itself — just wait. An administrator can also clear it from the admin
panel under Axes → Access attempts.

**A guard says a valid sticker is being denied at the gate.**
Check the application's status in the admin panel. Only stickers with
status **Sticker Issued** are allowed through. If it says Expired, the
person needs to renew.

**We need to add a new staff account.**
Admin panel → Accounts → Users → Add. Set the role to `admin` for sticker
office staff or `security` for guards.

**A superuser account (made with `createsuperuser`) can log into the admin
panel but not the app's own staff pages (Sticker Station, Applications,
etc.).** These are two separate things: Django's built-in "superuser" flag
only grants access to the admin panel itself. The app's own pages check a
different field — `role` — which a fresh superuser doesn't have set. Fix:
admin panel → Accounts → Users → find the account → set `role` to `admin`
or `security` → Save.

**Someone left the university and should lose access.**
Don't delete the account — that would erase their history. Admin panel →
Users → find them → untick **Active** → Save.

---

## 7. What this system does NOT do

Worth knowing so nobody assumes otherwise:

- **Payment is not tracked.** Cashier payment happens entirely outside the
  system. Staff must check the physical receipt before issuing a sticker.
- **RFID tags are matched by ID only.** The system checks that a tag's ID
  is registered; it does not cryptographically verify the tag is genuine.
  A duplicated tag would currently be accepted.
- **There is no automatic uptime alert.** If the site goes down at 2am,
  nobody is notified until someone tries to use it.
