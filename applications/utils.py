import uuid
import os


def upload_or_cr(instance, filename):
    ext = filename.split('.')[-1].lower()
    return f'documents/or_cr/{uuid.uuid4().hex}.{ext}'


def upload_license(instance, filename):
    ext = filename.split('.')[-1].lower()
    return f'documents/license/{uuid.uuid4().hex}.{ext}'


def upload_cor(instance, filename):
    ext = filename.split('.')[-1].lower()
    return f'documents/cor/{uuid.uuid4().hex}.{ext}'


def upload_auth(instance, filename):
    ext = filename.split('.')[-1].lower()
    return f'documents/auth/{uuid.uuid4().hex}.{ext}'