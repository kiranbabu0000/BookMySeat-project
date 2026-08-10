from django.conf import settings
from django.db import models


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
