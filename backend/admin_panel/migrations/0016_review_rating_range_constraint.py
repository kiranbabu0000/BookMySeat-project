from django.conf import settings
from django.db import migrations, models


def clamp_review_ratings(apps, schema_editor):
    """Fix any existing review ratings outside the 1-5 range.

    PostgreSQL's smallint accepts -32768..32767 but the application only
    allows 1-5.  Without a CheckConstraint, reviews could be stored with
    out-of-range values through the admin form, import scripts, or direct
    DB edits.  This clamps them before the constraint is added.
    """
    Review = apps.get_model('admin_panel', 'Review')
    bad = list(Review.objects.filter(rating__lt=1) | Review.objects.filter(rating__gt=5))
    if bad:
        for r in bad:
            r.rating = max(1, min(5, r.rating))
            r.save(update_fields=['rating'])
        print(f'Fixed {len(bad)} review(s) with out-of-range rating.')


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('admin_panel', '0015_alter_paymenttransaction_refund_id'),
        ('movies', '0028_add_cancelled_at'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(clamp_review_ratings, noop),
        migrations.AddConstraint(
            model_name='review',
            constraint=models.CheckConstraint(condition=models.Q(('rating__gte', 1), ('rating__lte', 5)), name='review_rating_range'),
        ),
    ]
