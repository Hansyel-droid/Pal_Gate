from .base import *

DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '192.168.1.19', '192.168.1.13', '192.168.1.12', '192.168.1.23', '192.168.1.10', '192.168.1.133', '10.122.53.133', '10.88.212.133', '192.168.137.164', '192.168.1.32' ]

# Use console email in development
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'