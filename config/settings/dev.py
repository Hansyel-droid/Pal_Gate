from .base import *

DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '192.168.1.19', '192.168.1.13', '192.168.1.12' ]

# Use console email in development
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'