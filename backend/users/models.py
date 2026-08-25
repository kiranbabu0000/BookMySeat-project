import uuid

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import models


class PendingSignup(models.Model):
    """Temporarily holds signup data until OTP verification succeeds.

    The password is stored as a Django password hash — never in plaintext.
    """

    key = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150)
    email = models.EmailField()
    password_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return 'Pending signup: {}'.format(self.username)

    @classmethod
    def create_from_form(cls, username, email, raw_password):
        """Create a PendingSignup with the password securely hashed."""
        return cls.objects.create(
            username=username,
            email=email,
            password_hash=make_password(raw_password),
        )


class NameChange(models.Model):
    """A record of a user changing the display name on their profile."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='name_changes',
    )
    old_first = models.CharField(max_length=150, blank=True, default='')
    old_last = models.CharField(max_length=150, blank=True, default='')
    new_first = models.CharField(max_length=150, blank=True, default='')
    new_last = models.CharField(max_length=150, blank=True, default='')
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return '{}: {} {} -> {} {}'.format(
            self.user, self.old_first, self.old_last, self.new_first, self.new_last
        )
