# Hosting Options for PalawanSU Gate System

Researched August 2026. Free tiers change often — re-check before committing.

## What this app actually needs

Two requirements rule out most "free hosting" you'll find:

1. **Runs Python** — Django needs a continuously running Python process.
   Free shared/cPanel hosting is PHP-only.
2. **A disk that survives restarts** — `db.sqlite3` and `media/` (uploaded
   OR/CRs, licences, CORs) are files. If the filesystem resets on
   redeploy, every application and document is lost.

A third thing that matters for *us* specifically: **outbound data**.
Reviewing one applicant means an admin downloading ~4.5 MB of documents.
Tight egress caps get hit fast.

---

## Usable free options

### 1. Oracle Cloud — Always Free ⭐ best free option

| | |
|---|---|
| What you get | Real VM, ~1 GB RAM, ~50 GB storage |
| Region | **Singapore available** (~50ms to Palawan) |
| Egress | ~10 TB/month — effectively unlimited for us |
| Expires | Never |
| Code changes | **None** |

**The catch:** Signup is strict — card verification often fails on the
first try, and accounts have been reclaimed for being "idle." The common
fix is upgrading to Pay-As-You-Go with a spending limit set; Always Free
resources stay free, but it stops the idle reclaim. Also the most setup
work of any option here (nginx, systemd, HTTPS all configured by hand).

### 2. Google Cloud — Always Free (e2-micro)

| | |
|---|---|
| What you get | Real VM, 30 GB persistent disk |
| Region | **US only** (Oregon / Iowa / South Carolina), ~200ms away |
| Egress | **1 GB/month**, then ~$0.12/GB |
| Code changes | **None** |

**The catch:** That 1 GB egress cap is the problem. At ~4.5 MB of
documents per applicant review, an admin working through ~200
applications uses roughly 900 MB — before any page loads. You'd exceed it
during a registration window. Requires a credit card, so overages bill
silently. Set a budget alert if you use this.

### 3. Cloudflare Tunnel + your own laptop

| | |
|---|---|
| What you get | Real public HTTPS URL, no signup, no card |
| Code changes | **None** (already working today) |

**The catch:** Only up while your laptop is on, awake, and online. The URL
changes every restart, so `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` need
updating each time. Fine for a scheduled demo or team testing; not
something you can hand to the university.

### 4. Vultr free tier

| | |
|---|---|
| What you get | 10 GB SSD, 0.5 GB RAM, 2 TB bandwidth |
| Code changes | **None** |

**The catch:** 0.5 GB RAM is tight for Django — workable, but you're near
the edge. Newer programme, so less track record than the others.

---

## Won't work — and why

| Platform | Why it fails |
|---|---|
| **Vercel** | Ephemeral filesystem (DB + documents wiped). Also caps uploads at 4.5 MB — our app allows 10 MB. No place for scheduled jobs. |
| **Render (free)** | Persistent disks are a **paid** feature; free tier wipes files on every deploy. Also spins down after 15 min idle → 30–60s cold start. Free Postgres **self-deletes after 30 days** (+14-day grace). |
| **Supabase** | Not app hosting — it's a database/storage service, so you'd still need somewhere to run Django. Free projects **pause after 7 days of inactivity**. |
| **InfinityFree / 000webhost / ByetHost** | PHP-only shared hosting. No Python runtime at all. |
| **PythonAnywhere (free)** | Blocks outbound connections except an allowlist → **Gmail SMTP won't work** → OTP emails never send → nobody can register. |
| **Heroku (student credit)** | Ephemeral filesystem, same as Vercel. Would need Postgres + external object storage. |

---

## Paid, if free proves painful

| Option | Cost | Why consider it |
|---|---|---|
| **Railway** | ~$5–10/mo | **Persistent volumes**, Singapore region, auto-detects Django, HTTPS handled for you. Least setup work of anything here. Needs `DATA_DIR` pointed at the mounted volume (see below). |
| **Vultr / Linode / DigitalOcean** | ~$5/mo | Plain VPS, Singapore regions. Same setup as Oracle but no signup drama. |
| **Render (paid)** | ~$10/mo | Service + disk. Simple, but pricier than a VPS for the same thing. |

Avoid **paid Google Cloud / AWS / Azure** for this — $13–15/mo for
equivalent specs, and billing complex enough that it's easy to leave
something running by accident.

---

## Student credits worth checking first

- **Azure for Students** — $100 credit, **no credit card required**.
  Roughly a year of a small VM. Strongest free-ish option if you qualify.
- **GitHub Student Developer Pack** — check
  [education.github.com/pack](https://education.github.com/pack) directly;
  offers change (DigitalOcean's participation ended in 2026).

---

## Important: where the data lives

On a **VM** (Oracle, Google, Vultr, university server), the defaults are
already correct — `db.sqlite3` and `media/` sit in the project folder on a
real disk. Nothing to change.

On a **container platform** (Railway), the project folder is replaced on
every deploy. The database and media folder must be moved onto the mounted
volume — e.g. `/data/db.sqlite3` and `/data/media` — or everything is lost
on the first redeploy. It works fine right up until you push an update,
which makes it an easy mistake to miss.

---

## Recommendation

1. **Ask PalawanSU's IT office first.** Costs nothing to ask. If they host it,
   it outlives our graduation, sits on their network and their backups,
   and no student's card is involved.
2. If not — **Oracle Always Free (Singapore)** for free, or **Railway**
   (~$5–10/mo) if we'd rather skip most server administration.
3. **Keep SQLite either way.** Measured at 100+ writes/second with zero
   errors at 200 simultaneous submissions — far beyond what a university
   vehicle registry needs. Switching to Postgres would also require moving
   file storage externally, which is days of rework for no benefit at this
   scale.
