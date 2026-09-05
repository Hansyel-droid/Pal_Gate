from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='127.0.0.1,localhost', cast=Csv())

# Needed whenever the browser reaches this server over a different
# scheme/host than Django sees on its own end — e.g. a Cloudflare
# Tunnel/ngrok URL (browser sees https://..., Django gets a plain http://
# request from the tunnel client on localhost), or any reverse proxy that
# terminates TLS in front of Django. Without a matching entry here,
# Django's CSRF check rejects every POST (login, forms, etc.) with a
# scheme mismatch, even though the request is legitimate.
# Comma-separated, each entry must include the scheme, e.g.
# CSRF_TRUSTED_ORIGINS=https://your-tunnel-name.trycloudflare.com
_csrf_trusted_origins = config('CSRF_TRUSTED_ORIGINS', default='')
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in _csrf_trusted_origins.split(',') if o.strip()
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'crispy_forms',
    'crispy_bootstrap5',
    'axes',
    'anymail',
    # Your apps
    'accounts',
    'applications',
    'appointments',
    'sticker_admin',
    'gate',
    'api',
]

MIDDLEWARE = [
    'accounts.middleware.AbandonedSignupCleanupMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'axes.middleware.AxesMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'accounts.middleware.IdleTimeoutMiddleware',
    'accounts.middleware.CampusPolicyMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'accounts.context_processors.notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ── Database ─────────────────────────────────────────────────────────────────
# SQLite by default (zero setup, the right call for an unattended long-term
# deployment — see the note below). Set SUPABASE_DB_HOST in .env to switch to
# Supabase's hosted Postgres instead — this exists for the Render+Supabase
# demo path specifically, NOT as a recommendation to abandon SQLite for the
# real deployment. Supabase's free tier has no backups and Render's own free
# Postgres deletes itself after 30 days, so treat this path as temporary
# (demo/defense) unless every service involved is on a paid tier.
_supabase_db_host = config('SUPABASE_DB_HOST', default='')

if _supabase_db_host:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'HOST': _supabase_db_host,
            'NAME': config('SUPABASE_DB_NAME', default='postgres'),
            'USER': config('SUPABASE_DB_USER', default='postgres'),
            'PASSWORD': config('SUPABASE_DB_PASSWORD'),
            'PORT': config('SUPABASE_DB_PORT', default='5432'),
            'OPTIONS': {
                # Supabase's pooler (port 6543, "Transaction" mode) sits in
                # front of Postgres for exactly this kind of small app — the
                # direct connection (port 5432) works too but caps out on
                # concurrent connections much sooner. Either works; the
                # pooler is the one Supabase's own dashboard recommends for
                # a web app instead of a persistent script.
                'sslmode': 'require',
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {
                # Take the write lock at BEGIN instead of at the first write.
                #
                # This is not a micro-optimisation — without it, concurrent
                # writes fail outright. SQLite's default DEFERRED transaction
                # only grabs a read lock at BEGIN and tries to upgrade to a
                # write lock later. When two connections both hold a read
                # lock and both try to upgrade, one MUST fail immediately —
                # busy_timeout is deliberately skipped for that case, because
                # waiting could only deadlock. So a burst of simultaneous
                # submissions produced instant "database is locked" errors no
                # matter how high busy_timeout was set. Measured: 60
                # concurrent bookings -> 5 succeeded, 55 crashed.
                #
                # IMMEDIATE makes each writing transaction queue at BEGIN,
                # where busy_timeout DOES apply, so they serialise and wait
                # their turn instead of failing.
                'transaction_mode': 'IMMEDIATE',
                'init_command': 'PRAGMA busy_timeout=20000;',
            },
        }
    }

# ── SQLite concurrency notes ─────────────────────────────────────────────────
# The two settings that make concurrent writes survivable (transaction_mode
# and busy_timeout) live in DATABASES['default']['OPTIONS'] above.
#
# Measured effect of transaction_mode=IMMEDIATE, 60 simultaneous booking
# submissions against one database:
#     before -> 5 succeeded, 55 raised "database is locked"
#     after  -> 60 succeeded, 0 errors
# At 100 simultaneous: 0 errors, worst-case request ~1.6s. Correctness held
# in both cases (no overbooking) — the failure mode was availability, not
# bad data.
#
# Follow-up test using threading.Barrier to force literal same-instant
# submission (harsher than any real traffic — real requests always have some
# network/OS jitter between them) found the next ceiling: at busy_timeout=5s,
# 500 simultaneous submissions started producing "database is locked" again
# (79 of 500 failed) because some writers queued longer than 5s waiting their
# turn. Raising busy_timeout to 20s (above) fixed it:
#     500 truly-simultaneous  -> 0 errors, worst-case wait  ~9.7s
#     1000 truly-simultaneous -> 0 errors, worst-case wait ~18.5s
#     2000 truly-simultaneous -> 545 of 2000 still fail (27%) — this is the
#                                 real ceiling at this setting.
# Practically: this app would need ~1,500+ applicants clicking submit in the
# same literal second before anyone sees an error, with individual waits
# growing into the 10-20s range as that ceiling is approached. A registration
# rush spread over even a few seconds of real network jitter is nowhere near
# this. If a future registration ever opens at a single synchronized moment
# for a very large cohort, the cheap mitigations are: raise busy_timeout
# further, stagger the opening by college/year level, or add a lightweight
# "submitting…" queue at the page level — before reaching for Postgres.
#
# NOTE: WAL mode was tried and deliberately reverted. It depends on a
# shared-memory (-shm) file alongside the database, and on at least one
# real filesystem type (a FUSE-backed mount, encountered during testing)
# that single PRAGMA call left the connection unable to read OR write
# afterwards — not a clean failure, an actually-broken connection. Cheap
# hosting sometimes involves network-backed or overlay storage with the
# same kind of quirk, so it stays off rather than risk that in production.
# If the eventual host turns out to have ordinary local disk, enabling WAL
# there would raise the write ceiling further and is worth re-testing.

AUTH_USER_MODEL = 'accounts.User'

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# ── Password Security ────────────────────────────────────────────────────────
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
]

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
    # Ours: none of the above knows what the account's password already
    # is, so a "reset" that types the old password back used to succeed
    # and report the password changed.
    {'NAME': 'accounts.validators.NotCurrentPasswordValidator'},
]

# ── Localisation ─────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Manila'
USE_I18N = True
USE_TZ = True

# ── Static & Media ───────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

# STATICFILES_STORAGE used to live here and had stopped doing anything:
# Django removed that setting in 5.1 in favour of STORAGES, and silently
# ignores it, so this project was running on the plain StaticFilesStorage
# with WhiteNoise's compression and cache-busting hashes both inactive.
#
# The manifest backend requires `collectstatic` to have run before anything
# resolving a static file will render — DEPLOYMENT.md already does this in
# both the first-install and the update path.
#
# 'default' (uploaded documents) is local disk unless SUPABASE_STORAGE_BUCKET
# is set, in which case uploads go to Supabase Storage instead — needed on
# Render, which has no persistent disk: every uploaded file would be wiped on
# the next redeploy or restart otherwise. Same temporary/demo caveat as the
# database switch above applies here.
_supabase_bucket = config('SUPABASE_STORAGE_BUCKET', default='')

if _supabase_bucket:
    STORAGES = {
        'default': {
            'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    }
    AWS_STORAGE_BUCKET_NAME = _supabase_bucket
    AWS_ACCESS_KEY_ID = config('SUPABASE_S3_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = config('SUPABASE_S3_SECRET_ACCESS_KEY')
    # Project's S3-compatible endpoint, from Supabase dashboard ->
    # Storage -> S3 Connection. Looks like:
    #   https://<project-ref>.supabase.co/storage/v1/s3
    AWS_S3_ENDPOINT_URL = config('SUPABASE_S3_ENDPOINT_URL')
    AWS_S3_REGION_NAME = config('SUPABASE_S3_REGION', default='us-east-1')
    # Supabase's S3 gateway expects path-style addressing
    # (endpoint/bucket/key), not the virtual-hosted-style
    # (bucket.endpoint/key) boto3 defaults to.
    AWS_S3_ADDRESSING_STYLE = 'path'
    AWS_DEFAULT_ACL = 'private'
    # Deliberately left at its default (True): these are government IDs and
    # OR/CR documents. applications.views.serve_document is the ONLY place
    # that reads them — it opens the file server-side via Django's storage
    # API and streams the bytes through an authenticated, ownership-checked
    # view (FileResponse), so the browser never sees a storage URL at all.
    # If AWS_QUERYSTRING_AUTH were False and the bucket public, that
    # authentication and ownership check would be bypassable by anyone who
    # obtained a direct object URL. Leaving this True means that even if
    # `.url` is ever called somewhere by mistake, it produces a short-lived
    # signed link rather than a permanently public one — the bucket itself
    # must stay Private in the Supabase dashboard, not Public.
    # AWS_QUERYSTRING_AUTH = True  (this is the boto3 default — not set here)
    MEDIA_URL = '/media/'  # unused for these fields; nothing calls .url on them
else:
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    }
    MEDIA_URL = '/media/'
    MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Crispy Forms ─────────────────────────────────────────────────────────────
CRISPY_ALLOWED_TEMPLATE_PACKS = 'bootstrap5'
CRISPY_TEMPLATE_PACK = 'bootstrap5'

# ── Auth Redirects ───────────────────────────────────────────────────────────
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# The university's mail domain. Defined ahead of the email block below
# because it is the domain the system's own addresses are built from,
# as well as the one applicant self-registration is restricted to.
REGISTRATION_EMAIL_DOMAIN = config(
    'REGISTRATION_EMAIL_DOMAIN',
    default='psu.palawan.edu.ph'
)

# Base URL for links inside outgoing email (the registration code message's
# "open this on any device" link, for one). No trailing slash. Not derived
# from the request, because the registration OTP email is composed in
# otp.py, which mailing a code from a background job or a shell wouldn't
# have a request to pull it from — a fixed setting works the same way
# everywhere this runs, dev or prod.
SITE_URL = config('SITE_URL', default='https://palsu-gate.onrender.com')

# ── Email ────────────────────────────────────────────────────────────────────
# Default is the console backend — emails print to the server log/terminal
# instead of actually sending, which is fine for local dev and for campus
# LAN deployments that haven't set up real SMTP yet.
#
# Two ways to send real email, and which one works depends on where this
# runs:
#
# - SMTP (EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend): works
#   on a real server or a campus LAN box with unrestricted outbound traffic.
#   Does NOT work on Render's free tier — Render blocks outbound traffic to
#   SMTP ports (25/465/587) on free web services to fight spam abuse, so
#   this backend will hang and 500 every request that tries to send email.
#
# - Brevo via Anymail (EMAIL_BACKEND=anymail.backends.brevo.EmailBackend):
#   sends over Brevo's HTTPS API instead of SMTP, so it works anywhere,
#   including Render's free tier. Requires BREVO_API_KEY in .env and a
#   sender address verified in the Brevo dashboard (Settings → Senders,
#   Domains & Dedicated IPs) — verifying a single sender address is enough,
#   you do NOT need to prove DNS control of the whole domain. Free tier is
#   300 emails/day, no expiry, no card required.
EMAIL_BACKEND = config(
    'EMAIL_BACKEND',
    default='django.core.mail.backends.console.EmailBackend'
)
EMAIL_HOST = config('EMAIL_HOST', default='')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)

# Only read when EMAIL_BACKEND is the Brevo/Anymail backend above — harmless
# to leave blank otherwise.
ANYMAIL = {
    'BREVO_API_KEY': config('BREVO_API_KEY', default=''),
}
# Everything the system sends — sign-up codes, password reset links,
# application status notices — goes out from the university's own domain.
# A .local address is not deliverable and, more to the point, mail asking
# someone to click a link and set a password is exactly the mail people
# are trained to distrust when it arrives from an address that has
# nothing to do with the institution. Override in .env with whatever
# mailbox the campus relay is willing to send as.
DEFAULT_FROM_EMAIL = config(
    'DEFAULT_FROM_EMAIL',
    default=f'PalawanSU Gate System <noreply@{REGISTRATION_EMAIL_DOMAIN}>'
)

# Where to send a person the automated flows cannot help: staff accounts
# with no address on file, and applicants locked out of the mailbox they
# signed up with. Printed on the password reset page and in the reset
# email, so it has to be a mailbox somebody actually reads.
SUPPORT_EMAIL = config(
    'SUPPORT_EMAIL',
    default=f'gate-support@{REGISTRATION_EMAIL_DOMAIN}'
)

# How long a password reset link stays good. Django's default is three
# days, which is a long time for a single-click account takeover to sit
# in an inbox — and nobody who has just clicked "forgot password" needs
# three days. One hour is stated verbatim in the email we send.
PASSWORD_RESET_TIMEOUT = config(
    'PASSWORD_RESET_TIMEOUT', default=3600, cast=int
)

# ── Applicant Registration (campus email + one-time code) ────────────────────
# Self-registration is limited to REGISTRATION_EMAIL_DOMAIN above, and the
# account stays inactive until the applicant types back the code we email
# them. That gives every portal account a verified, reachable address — which
# both the status-notification flow (applications/notifications.py) and
# password recovery depend on.
OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = config('OTP_EXPIRY_MINUTES', default=10, cast=int)
OTP_MAX_ATTEMPTS = config('OTP_MAX_ATTEMPTS', default=5, cast=int)
OTP_RESEND_COOLDOWN_SECONDS = config(
    'OTP_RESEND_COOLDOWN_SECONDS',
    default=60,
    cast=int
)

# ── Cache (used by django-ratelimit for login/API rate limiting) ─────────────
# Without this, Django falls back to an in-memory cache that is NOT shared
# between gunicorn worker processes — each worker would count rate-limit hits
# separately, so e.g. "10 login attempts/min" would actually allow closer to
# 10 x number-of-workers once deployed. A file-based cache is one shared
# location every worker on the same machine reads/writes, with no extra
# service (Redis/Memcached) to install on a small/free-tier VM.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': BASE_DIR / 'cache',
    }
}

# ── Session Security ─────────────────────────────────────────────────────────
SESSION_COOKIE_AGE = 28800               # 8 hours max
# False, not True: with this on, Django sends the session cookie with no
# Max-Age/Expires at all and leaves "when does this expire" entirely up to
# the browser. Desktop browsers hold onto it until you actually quit, but
# mobile browsers routinely kill and relaunch their process when the app
# is backgrounded (e.g. switching to Mail to read a verification code) —
# and several treat that relaunch as "the browser closed", wiping the
# cookie. That surfaced as real sign-ups losing their in-progress OTP
# session mid-verification on a phone. SESSION_COOKIE_AGE (8h) plus the
# 30-min idle timeout below already bound how long a session can live, so
# this isn't relying on "browser close" for security in the first place.
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_HTTPONLY = True          # No JS access to session cookie
SESSION_SAVE_EVERY_REQUEST = True       # Refresh expiry on activity
IDLE_TIMEOUT = 1800                     # 30 min idle timeout (seconds)

# How often AbandonedSignupCleanupMiddleware (accounts/middleware.py) runs
# accounts.cleanup_abandoned_signups. There's no task scheduler or cron job
# on this deployment, so this is what actually keeps the registration
# email's promise that an unverified account "will be removed
# automatically" — it rides along on ordinary site traffic instead.
ABANDONED_SIGNUP_CLEANUP_INTERVAL = 1800  # 30 minutes

# ── Message Tags ──────────────────────────────────────────────────────────────
# Django's default tag for messages.error() is the literal string 'error',
# but templates/base.html only defines .alert-danger (Bootstrap's naming,
# which the rest of the app's alert CSS follows) — there is no .alert-error
# rule. Every error message was rendering as <div class="alert alert-error">
# with none of the .alert-danger background/border/color applied, i.e. an
# alert box with no visible background at all. This remaps it so every
# messages.error() call actually gets styled instead of quietly vanishing.
from django.contrib.messages import constants as message_constants
MESSAGE_TAGS = {
    message_constants.ERROR: 'danger',
}

# ── Account Lockout (django-axes) ────────────────────────────────────────────
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.5                 # 30 minutes in hours
AXES_LOCKOUT_PARAMETERS = ['ip_address', 'username']
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = 'accounts/lockout.html'
AXES_VERBOSE = False

# ── Reverse Proxy ────────────────────────────────────────────────────────────
# There is no reverse proxy in front of Django yet (see DEPLOYMENT.md), so by
# default we do NOT trust the X-Forwarded-For header — anyone could forge it
# to spoof their IP. Flip this only once a trusted proxy (nginx, etc.) is
# actually terminating requests and setting that header itself.
#
# This is a single switch for "is there exactly one trusted proxy in front of
# us", and EVERYTHING that needs to know the client's real address hangs off
# it. It used to feed only gate.audit.get_client_ip, which left the two
# libraries that matter most reading REMOTE_ADDR — see below.
TRUST_X_FORWARDED_FOR = config('TRUST_X_FORWARDED_FOR', default=False, cast=bool)

if TRUST_X_FORWARDED_FOR:
    # nginx terminates TLS and proxies to us over plain http, so the only
    # evidence the original request was HTTPS is this header. Without it
    # request.is_secure() is False behind the proxy, and SECURE_SSL_REDIRECT
    # (below, under HTTPS_ENABLED) 301s to https -> nginx forwards that back
    # to us as http -> we 301 again, forever. The site becomes unreachable at
    # exactly the moment HTTPS is switched on.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    # django-ratelimit and django-axes each resolve the client IP
    # themselves, and both default to REMOTE_ADDR — neither has ever
    # consulted TRUST_X_FORWARDED_FOR. Behind nginx REMOTE_ADDR is 127.0.0.1
    # for every request on campus, which collapses every per-IP throttle into
    # one shared bucket. Concretely: AXES_LOCKOUT_PARAMETERS includes
    # 'ip_address', so five bad logins from any one person would lock out
    # every account in the system for AXES_COOLOFF_TIME, and the 10/m login
    # limit would be a campus-wide quota rather than a per-attacker one.
    #
    # Both are pointed at our own resolver rather than configured separately.
    # The tempting knobs don't actually work here:
    #   - AXES_IPWARE_PROXY_COUNT is inert unless django-ipware is installed,
    #     and it isn't (not in requirements.txt) — axes then falls back to a
    #     hardcoded REMOTE_ADDR that no axes setting can override.
    #   - RATELIMIT_IP_META_KEY='HTTP_X_FORWARDED_FOR' passes the *raw* header
    #     through, so a proxied request hands "1.2.3.4, 10.0.0.5" to
    #     ipaddress.ip_network() and raises.
    # A dotted path is resolved and called with the request by both libraries,
    # which sidesteps both problems and keeps one implementation of "who is
    # the client" shared with the audit log.
    AXES_CLIENT_IP_CALLABLE = 'gate.audit.throttle_client_ip'
    RATELIMIT_IP_META_KEY = 'gate.audit.throttle_client_ip'

# ── API Keys / device authentication ──────────────────────────────────────────
# No insecure fallback on purpose: a missing .env value must fail loudly,
# not silently fall back to a key that's sitting in source control history.
#
# Each physical device gets its own named secret rather than one key shared
# by every device. A secret now does two jobs: it's what a signed request
# (see require_device_auth in api/authentication.py) is verified against,
# and its name is a scope — DEVICE_ALLOWED_ENDPOINTS says which URL each
# device may call at all, so a leaked gate-scanner secret can't be replayed
# against the registration endpoint and vice versa.
#
# These are the exact same two values the single API_KEYS list used to hold
# — nothing was rotated, they were only unlabeled before. Must also be set
# on Render's own environment variables (not just this local .env) before
# deploying the backend change that reads them, or the app fails at startup.
GATE_SCANNER_KEY = config('GATE_SCANNER_KEY')
REGISTRATION_DEVICE_KEY = config('REGISTRATION_DEVICE_KEY')

DEVICE_SECRETS = {
    'gate_scanner': GATE_SCANNER_KEY,
    'registration_device': REGISTRATION_DEVICE_KEY,
}

DEVICE_ALLOWED_ENDPOINTS = {
    'gate_scanner': {'scan_tag'},
    'registration_device': {'register_uid'},
}

# Back-compat for any device still sending a bare X-API-Key with no device
# identity attached (gate_status, and scan_tag/register_uid until both
# devices are reflashed with signed-request firmware) — see
# require_api_key / require_device_auth in api/authentication.py.
API_KEYS = list(DEVICE_SECRETS.values())

# ── RFID tag authentication ──────────────────────────────────────────────────
# Master key for deriving each NTAG215's PWD_AUTH password from its UID.
# See api/rfid_auth.py for the derivation and the reasoning behind it.
#
# No fallback, for the same reason as API_KEYS above — but the consequences
# of getting this one wrong are worse, so two warnings:
#
#   1. THIS VALUE MUST NEVER CHANGE ONCE TAGS ARE IN THE FIELD. Every issued
#      tag is physically locked with a password derived from this key. Change
#      it and the server starts deriving passwords that no existing tag will
#      accept, while the tags stay locked with the old one — which nothing
#      can now compute. There is no remote recovery: each tag would have to
#      be collected and unlocked with its old password, and if that password
#      is gone the tag's user memory is unreachable for good. Rotating this
#      is a physical recall, not a config change.
#   2. It is a different secret from API_KEYS and must not be set to the same
#      value. API keys are handed out per gate device and get reflashed when
#      one is suspected compromised; this key is the root of every tag
#      password in the system and belongs only on the server.
RFID_MASTER_KEY = config('RFID_MASTER_KEY')

# ── Admin Branding ───────────────────────────────────────────────────────────
ADMIN_SITE_HEADER = 'PalawanSU Gate System Administration'
ADMIN_SITE_TITLE = 'PalawanSU Admin'
ADMIN_INDEX_TITLE = 'System Administration'

# ── Logging ──────────────────────────────────────────────────────────────────
# The 'logs' directory isn't tracked in git (git doesn't track empty folders),
# so a fresh clone — like Render's build — won't have it. The FileHandler
# below fails immediately with FileNotFoundError if the directory is missing,
# which crashes every manage.py command including collectstatic/migrate
# during deploy. Creating it here, before LOGGING is defined, guarantees it
# exists no matter where or how many times the repo is cloned.
(BASE_DIR / 'logs').mkdir(parents=True, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'ERROR',
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'logs/errors.log',
            'formatter': 'verbose',
        },
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file', 'console'],
            'level': 'ERROR',
            'propagate': True,
        },
    },
}

# ── Security Headers (always on, safe for HTTP too) ──────────────────────────
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# ── HTTPS-only settings (only when HTTPS=True in .env) ───────────────────────
HTTPS_ENABLED = config('HTTPS_ENABLED', default=False, cast=bool)
if HTTPS_ENABLED:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
