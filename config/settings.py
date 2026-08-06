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
    # Your apps
    'accounts',
    'applications',
    'appointments',
    'sticker_admin',
    'gate',
    'api',
]

MIDDLEWARE = [
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
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

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
            # write lock later. When two connections both hold a read lock
            # and both try to upgrade, one MUST fail immediately —
            # busy_timeout is deliberately skipped for that case, because
            # waiting could only deadlock. So a burst of simultaneous
            # submissions produced instant "database is locked" errors no
            # matter how high busy_timeout was set. Measured: 60 concurrent
            # bookings -> 5 succeeded, 55 crashed.
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
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

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

# ── Email ────────────────────────────────────────────────────────────────────
# Default is the console backend — emails print to the server log/terminal
# instead of actually sending, which is fine for local dev and for campus
# LAN deployments that haven't set up real SMTP yet. To send real email in
# production, set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# in .env and fill in the EMAIL_HOST_* values below (e.g. a university
# mail relay or a transactional provider). Nothing breaks if these are left
# blank while still on the console backend.
EMAIL_BACKEND = config(
    'EMAIL_BACKEND',
    default='django.core.mail.backends.console.EmailBackend'
)
EMAIL_HOST = config('EMAIL_HOST', default='')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
DEFAULT_FROM_EMAIL = config(
    'DEFAULT_FROM_EMAIL',
    default='PalSU Gate System <noreply@palsu-gate.local>'
)

# ── Applicant Registration (campus email + one-time code) ────────────────────
# Self-registration is limited to official PalSU addresses, and the account
# stays inactive until the applicant types back the code we email them. That
# gives every portal account a verified, reachable address — which the whole
# status-notification flow (applications/notifications.py) depends on.
REGISTRATION_EMAIL_DOMAIN = config(
    'REGISTRATION_EMAIL_DOMAIN',
    default='psu.palawan.edu.ph'
)
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
# to spoof their IP in the audit log. Flip this only once a trusted proxy
# (nginx, etc.) is actually terminating requests and setting that header itself.
TRUST_X_FORWARDED_FOR = config('TRUST_X_FORWARDED_FOR', default=False, cast=bool)

# ── API Keys ─────────────────────────────────────────────────────────────────
# No insecure fallback on purpose: a missing .env value must fail loudly,
# not silently fall back to a key that's sitting in source control history.
API_KEYS = config('API_KEYS', cast=Csv())

# ── Admin Branding ───────────────────────────────────────────────────────────
ADMIN_SITE_HEADER = 'PalSU Gate System Administration'
ADMIN_SITE_TITLE = 'PalSU Admin'
ADMIN_INDEX_TITLE = 'System Administration'

# ── Logging ──────────────────────────────────────────────────────────────────
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
