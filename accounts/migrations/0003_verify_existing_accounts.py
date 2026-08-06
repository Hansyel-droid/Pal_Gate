from django.db import migrations


def mark_existing_verified(apps, schema_editor):
    """
    Accounts that already existed before the email-verification step was
    added were vetted some other way (created by an admin, or registered
    back when sign-up was a single step). Leaving them at the field's
    default of False would misrepresent them as half-finished sign-ups.

    Walk-in records have no email address at all, so there is nothing to
    call verified — they stay False.
    """
    User = apps.get_model('accounts', 'User')
    User.objects.exclude(email='').update(email_verified=True)


def unmark(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.update(email_verified=False)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_user_email_verified_emailotp'),
    ]

    operations = [
        migrations.RunPython(mark_existing_verified, unmark),
    ]
