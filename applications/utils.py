import uuid
import os


def upload_or_cr(instance, filename):
    """
    Superseded by upload_official_receipt / upload_vehicle_registration when
    the single OR/CR upload was split into the two documents it always was.

    Kept because migration 0002 names this function directly, and Django
    imports the referenced callable when it loads that migration — deleting
    it breaks `migrate` on a fresh database. Nothing current writes here;
    rows created before the split still point into documents/or_cr/ and
    still resolve.
    """
    ext = filename.split('.')[-1].lower()
    return f'documents/or_cr/{uuid.uuid4().hex}.{ext}'


def upload_official_receipt(instance, filename):
    ext = filename.split('.')[-1].lower()
    return f'documents/official_receipt/{uuid.uuid4().hex}.{ext}'


def upload_vehicle_registration(instance, filename):
    ext = filename.split('.')[-1].lower()
    return f'documents/vehicle_registration/{uuid.uuid4().hex}.{ext}'


def upload_license(instance, filename):
    ext = filename.split('.')[-1].lower()
    return f'documents/license/{uuid.uuid4().hex}.{ext}'


def upload_cor(instance, filename):
    ext = filename.split('.')[-1].lower()
    return f'documents/cor/{uuid.uuid4().hex}.{ext}'


def upload_auth(instance, filename):
    ext = filename.split('.')[-1].lower()
    return f'documents/auth/{uuid.uuid4().hex}.{ext}'


# The 2x2 ID photo. Same shape as the document callables above, but it is
# the only upload that is rendered inline (admin review, gate scan card)
# rather than downloaded behind a "View" link.
def upload_photo_2x2(instance, filename):
    ext = filename.split('.')[-1].lower()
    return f'documents/photo_2x2/{uuid.uuid4().hex}.{ext}'
