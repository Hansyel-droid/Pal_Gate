from .base import *

DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']

# Use console email in development
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'