from django.db import models
from django.contrib.auth.models import User
from movies.models import Movie, Theater, Seat, Booking


class Genre(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Language(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CastMember(models.Model):
    ROLE_CHOICES = [
        ('hero', 'Hero'),
        ('heroine', 'Heroine'),
        ('villain', 'Villain'),
        ('supporting', 'Supporting'),
        ('guest', 'Guest Appearance'),
        ('other', 'Other'),
    ]
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='cast_members')
    name = models.CharField(max_length=255)
    character_name = models.CharField(max_length=255, blank=True, null=True)
    image = models.ImageField(upload_to='cast/', blank=True, null=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='other')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} in {self.movie.name}'


class Theatre(models.Model):
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    contact = models.CharField(max_length=50, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    facilities = models.TextField(blank=True, null=True, help_text="Comma-separated list of facilities")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Screen(models.Model):
    theatre = models.ForeignKey(Theatre, on_delete=models.CASCADE, related_name='screens')
    name = models.CharField(max_length=100)
    capacity = models.PositiveIntegerField(default=0)
    seat_layout = models.TextField(blank=True, null=True, help_text="Describe seat layout (e.g., A1-A20, B1-B20)")

    class Meta:
        ordering = ['theatre', 'name']
        unique_together = ['theatre', 'name']

    def __str__(self):
        return f'{self.name} - {self.theatre.name}'


class Show(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
        ('sold_out', 'Sold Out'),
        ('paused', 'Paused'),
    ]
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='shows')
    theatre = models.ForeignKey(Theatre, on_delete=models.CASCADE, related_name='shows')
    screen = models.ForeignKey(Screen, on_delete=models.CASCADE, related_name='shows')
    date = models.DateField()
    time = models.TimeField()
    ticket_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    class Meta:
        ordering = ['date', 'time']

    def __str__(self):
        return f'{self.movie.name} - {self.theatre.name} - {self.date} {self.time}'


class Trailer(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='trailers')
    title = models.CharField(max_length=255, blank=True, null=True)
    url = models.URLField(max_length=500, help_text="YouTube URL")
    is_featured = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_featured', 'title']

    def __str__(self):
        return f'Trailer for {self.movie.name}'


class MovieImage(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='gallery_images')
    image = models.ImageField(upload_to='movies/gallery/')
    caption = models.CharField(max_length=255, blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'Image for {self.movie.name}'


class AdminProfile(models.Model):
    ROLE_CHOICES = [
        ('super_admin', 'Super Admin'),
        ('admin', 'Admin'),
        ('staff', 'Staff'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='staff')
    department = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['user__username']

    def __str__(self):
        return f'{self.user.username} - {self.role}'


class AdminPermission(models.Model):
    admin_profile = models.ForeignKey(AdminProfile, on_delete=models.CASCADE, related_name='permissions')
    module = models.CharField(max_length=100)
    can_view = models.BooleanField(default=False)
    can_create = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)

    class Meta:
        unique_together = ['admin_profile', 'module']
        verbose_name = 'Admin Permission'
        verbose_name_plural = 'Admin Permissions'

    def __str__(self):
        return f'{self.admin_profile.user.username} - {self.module}'


class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=255)
    module = models.CharField(max_length=100)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'

    def __str__(self):
        return f'{self.action} by {self.user.username if self.user else "Unknown"}'


class GSTSlab(models.Model):
    """Configurable GST slabs keyed on the taxable order value.

    Slab selection: the first slab (by display_order) whose range contains the
    taxable amount wins. A slab with a blank max_amount means "no upper limit".
    """
    min_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Upper bound (inclusive). Leave empty for no upper limit.")
    rate = models.DecimalField(max_digits=5, decimal_places=2, help_text="GST percentage for this slab")
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order']

    def __str__(self):
        upper = self.max_amount if self.max_amount is not None else '\u221e'
        return f'\u20b9{self.min_amount}-{upper} @ {self.rate}%'


class PricingConfig(models.Model):
    """Singleton holding the platform and miscellaneous fee settings."""
    platform_fee_per_ticket = models.DecimalField(max_digits=10, decimal_places=2, default=5.00)
    misc_fee_per_booking = models.DecimalField(max_digits=10, decimal_places=2, default=2.50)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pricing Configuration'
        verbose_name_plural = 'Pricing Configurations'

    def __str__(self):
        return f'Platform \u20b9{self.platform_fee_per_ticket}/ticket, Misc \u20b9{self.misc_fee_per_booking}/booking'


class Coupon(models.Model):
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_uses = models.PositiveIntegerField(default=0)
    used_count = models.PositiveIntegerField(default=0)
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.code


class Notification(models.Model):
    TYPE_CHOICES = [
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('success', 'Success'),
        ('danger', 'Danger'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='notifications')
    title = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(max_length=50, choices=TYPE_CHOICES, default='info')
    is_read = models.BooleanField(default=False)
    link = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Review(models.Model):
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    booking = models.ForeignKey(Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviews')
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    comment = models.TextField()
    is_approved = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False, help_text="Hidden from public view by admin")
    is_reported = models.BooleanField(default=False, help_text="Flagged as inappropriate by users")
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['movie', 'user']

    def __str__(self):
        return f'{self.user.username} - {self.movie.name} ({self.rating}/5)'


class Payment(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='payment')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=50, default='online')
    transaction_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')
    paid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_at']

    def __str__(self):
        return f'Payment {self.transaction_id or self.id} - {self.status}'
