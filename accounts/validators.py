"""
Password rules specific to this system, on top of Django's stock set in
AUTH_PASSWORD_VALIDATORS.
"""

from django.core.exceptions import ValidationError


class NotCurrentPasswordValidator:
    """
    Refuse a "new" password that is the one already on the account.

    Django's own validators only look at the password's shape — length,
    how common it is, how much it resembles the username. None of them
    knows what the account's password currently is, so "reset" happily
    accepted the same password back and reported success. Somebody who
    resets because they suspect their password is known would come away
    believing they had changed it.

    Registered globally rather than added to one form, so it holds
    wherever a password is set: the reset link, the Django admin's change
    form, and any change-password screen added later.
    """

    def validate(self, password, user=None):
        # No user means there is no current password to clash with —
        # Django calls validators that way from `manage.py` and other
        # code paths that validate a password on its own.
        #
        # An unsaved user is the sign-up form: `user` there is the
        # not-yet-created account, whose password is still empty. There
        # is nothing to be "the same as" yet.
        if user is None or user.pk is None:
            return

        # A walk-in record with set_unusable_password() has no password a
        # new one could repeat, and check_password() on one is always
        # False anyway — stated here so the intent is not mistaken for an
        # oversight.
        if not user.has_usable_password():
            return

        if user.check_password(password):
            raise ValidationError(
                'Your new password must be different from your current '
                'one. Choose a password you have not used on this '
                'account before.',
                code='password_unchanged',
            )

    def get_help_text(self):
        return 'Your new password must be different from your current one.'
