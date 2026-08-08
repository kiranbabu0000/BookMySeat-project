from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone

RESERVATION_HOLD_SECONDS = 300


class Movie(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('coming_soon', 'Coming Soon'),
        ('now_showing', 'Now Showing'),
        ('archived', 'Archived'),
        ('hidden', 'Hidden'),
    ]
    CATEGORY_CHOICES = [
        ('movie', 'Movie'),
        ('laughing_therapy', 'Laughing Therapy'),
        ('live_concert', 'Live Concert'),
    ]
    name = models.CharField(max_length=255, db_index=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='movie', db_index=True)
    image = models.ImageField(upload_to="movies/", blank=True)
    thumbnail = models.ImageField(upload_to="movies/thumbnails/", blank=True, null=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1, validators=[MinValueValidator(0), MaxValueValidator(10)])
    cast = models.TextField()
    description = models.TextField(blank=True, null=True)
    duration = models.PositiveIntegerField(blank=True, null=True, help_text="Duration in minutes")
    release_date = models.DateField(blank=True, null=True, db_index=True)
    certificate = models.CharField(max_length=20, blank=True, null=True, help_text="e.g., U, UA, A")
    banner = models.ImageField(upload_to="movies/banners/", blank=True, null=True)
    director = models.CharField(max_length=255, blank=True, null=True)
    producer = models.CharField(max_length=255, blank=True, null=True)
    writer = models.CharField(max_length=255, blank=True, null=True)
    music_director = models.CharField(max_length=255, blank=True, null=True)
    cinematographer = models.CharField(max_length=255, blank=True, null=True)
    production_company = models.CharField(max_length=255, blank=True, null=True)
    story = models.TextField(blank=True, null=True)
    trailer_url = models.URLField(max_length=500, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    imdb_rating = models.DecimalField(max_digits=3, decimal_places=1, blank=True, null=True, validators=[MinValueValidator(0), MaxValueValidator(10)])
    show_on_homepage = models.BooleanField(default=True, help_text="Show this movie on the homepage")
    is_deleted = models.BooleanField(default=False, help_text="Soft delete flag")
    genres = models.ManyToManyField('admin_panel.Genre', blank=True, related_name='movies')
    languages = models.ManyToManyField('admin_panel.Language', blank=True, related_name='movies')

    class Meta:
        ordering = ['-release_date', 'name']

    def __str__(self):
        return self.name

class SeatCategory(models.Model):
    """Configurable seat band (e.g. SILVER rows A-C, GOLD rows D-G, PLATINUM rows H-Z)."""
    name = models.CharField(max_length=50, unique=True)
    row_start = models.CharField(max_length=2, default='A', help_text="First row letter covered by this category")
    row_end = models.CharField(max_length=2, default='Z', help_text="Last row letter covered by this category")
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class ShowPrice(models.Model):
    """Per-show, per-seat-category ticket price catalog."""
    theater = models.ForeignKey('Theater', on_delete=models.CASCADE, related_name='prices')
    category = models.ForeignKey(SeatCategory, on_delete=models.CASCADE, related_name='prices')
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ['theater', 'category']
        ordering = ['category__display_order', 'category__name']

    def __str__(self):
        return f'{self.theater_id} {self.category.name} @ {self.price}'


class Theater(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
        ('sold_out', 'Sold Out'),
        ('paused', 'Paused'),
    ]
    name = models.CharField(max_length=255)
    movie = models.ForeignKey(Movie,on_delete=models.CASCADE,related_name='theaters')
    time= models.DateTimeField(db_index=True)
    screen_name = models.CharField(max_length=100, blank=True, default='Main', help_text="Screen shown for this show (e.g. Screen 1)")
    ticket_price = models.DecimalField(max_digits=10, decimal_places=2, default=250)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', help_text="Public booking visibility status, kept in sync with the admin Show")
    seat_revision = models.PositiveIntegerField(default=0, help_text="Incremented whenever any seat state changes for this show")
    layout_spec = models.JSONField(default=dict, blank=True, help_text="Snapshot of the screen seat-layout definition used to generate this show's seats")

    def bump_seat_revision(self):
        self.__class__.objects.filter(pk=self.pk).update(
            seat_revision=models.F('seat_revision') + 1
        )

    def __str__(self):
        return f'{self.name} - {self.movie.name} at {self.time}'

class Seat(models.Model):
    SEAT_TYPE_CHOICES = [
        ('standard', 'Standard'),
        ('wheelchair', 'Wheelchair'),
        ('couple', 'Couple'),
    ]
    theater = models.ForeignKey(Theater,on_delete=models.CASCADE,related_name='seats')
    seat_number = models.CharField(max_length=10)
    is_booked=models.BooleanField(default=False, db_index=True)
    seat_type = models.CharField(max_length=20, choices=SEAT_TYPE_CHOICES, default='standard')
    category = models.ForeignKey('SeatCategory', on_delete=models.SET_NULL, null=True, blank=True, related_name='seats', help_text="Pricing band this seat belongs to")
    row_label = models.CharField(max_length=5, blank=True, default='', help_text="Display row label, e.g. A, B1, R-1")
    row_idx = models.PositiveIntegerField(default=0, help_text="Row index starting at 0 (0 is closest to the screen)")
    col_idx = models.PositiveIntegerField(default=0, help_text="Column index within the row starting at 0 (left to right)")
    side = models.CharField(max_length=10, choices=[('left', 'Left'), ('right', 'Right'), ('center', 'Center')], default='center', help_text="Screen side of the aisle gap")
    gap_before = models.BooleanField(default=False, help_text="Render an aisle gap before this seat within its row")
    is_best_view = models.BooleanField(default=False, help_text="Premium 'best view' seats for the screen")
    couple_group = models.PositiveIntegerField(default=0, help_text="Couple-pair group id; seats sharing a non-zero group must be bought together")

    class Meta:
        unique_together = ['theater', 'seat_number']
        ordering = ['row_idx', 'col_idx']

    def __str__(self):
        return f'{self.seat_number} in {self.theater.name}'

class Booking(models.Model):
    STATUS_CHOICES = [
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ]
    user=models.ForeignKey(User,on_delete=models.CASCADE)
    seat=models.OneToOneField(Seat,on_delete=models.CASCADE)
    movie=models.ForeignKey(Movie,on_delete=models.CASCADE)
    theater=models.ForeignKey(Theater,on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='confirmed', help_text="Booking lifecycle status")
    reservation = models.ForeignKey('Reservation', on_delete=models.SET_NULL, null=True, blank=True, related_name='bookings')
    booking_ref = models.CharField(max_length=20, unique=True, blank=True, editable=False)
    booked_at=models.DateTimeField(auto_now_add=True, db_index=True)
    seat_category = models.CharField(max_length=50, blank=True, default='', help_text="Snapshotted seat category at booking time")
    ticket_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Snapshotted per-seat ticket price")
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Snapshotted GST percentage")
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Snapshotted GST amount for this seat")
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Snapshotted platform fee share")
    misc_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Snapshotted miscellaneous fee share")
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Snapshotted coupon discount share")
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Snapshotted amount charged for this seat")
    scanned_at = models.DateTimeField(null=True, blank=True, help_text="When the ticket QR was first scanned at the venue")
    scan_count = models.PositiveIntegerField(default=0, help_text="Number of times this ticket QR has been scanned")

    class Meta:
        ordering = ['-booked_at', '-id']
        indexes = [
            models.Index(fields=['status', 'booked_at']),
            models.Index(fields=['user', 'booked_at']),
            models.Index(fields=['movie', 'booked_at']),
            models.Index(fields=['theater', 'booked_at']),
        ]

    def __str__(self):
        return f'Booking by{self.user.username} for {self.seat.seat_number} at {self.theater.name}'


class Reservation(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('booked', 'Booked'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    token = models.CharField(max_length=64, unique=True, db_index=True, help_text="Secure reservation token")
    booking_ref = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False, db_index=True, help_text="Transaction-level booking ID shared by all seats (e.g. BMS39DBA878)")
    ticket_count = models.PositiveIntegerField(default=0, help_text="Number of tickets booked in this reservation")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='seat_reservations')
    show = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name='reservations')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', db_index=True)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    coupon = models.ForeignKey('admin_panel.Coupon', on_delete=models.SET_NULL, null=True, blank=True, related_name='reservations')
    coupon_code = models.CharField(max_length=50, blank=True)
    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    convenience_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Platform fee + miscellaneous fee combined")
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Platform fee (per ticket)")
    misc_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Miscellaneous fee (per booking)")
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="GST percentage applied")
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    scanned_at = models.DateTimeField(null=True, blank=True, help_text="When the ticket QR was first scanned at the venue")
    scan_count = models.PositiveIntegerField(default=0, help_text="Number of times this ticket QR has been scanned")

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['show', 'status']),
            models.Index(fields=['status', 'expires_at']),
        ]

    def __str__(self):
        return f'Reservation {self.token[:8]} by {self.user.username}'

    @property
    def is_active(self):
        return self.status == 'active' and self.expires_at > timezone.now()

    @property
    def seat_numbers(self):
        return list(self.reserved_seats.select_related('seat').values_list('seat__seat_number', flat=True))


class ReservedSeat(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name='reserved_seats')
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE, related_name='reserved_seat')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['seat__seat_number']

    def __str__(self):
        return f'{self.seat.seat_number} in {self.reservation.token[:8]}'


class Wishlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='wishlist')
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='wishlisted_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['user', 'movie']

    def __str__(self):
        return f'{self.user.username} -> {self.movie.name}'


class TicketDownload(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ticket_downloads')
    booking_ref = models.CharField(max_length=20, db_index=True)
    movie = models.CharField(max_length=255, blank=True)
    downloaded_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ['-downloaded_at']
        indexes = [
            models.Index(fields=['user', 'downloaded_at']),
        ]

    def __str__(self):
        return f'{self.user.username} downloaded {self.booking_ref}'


class EmailOutbox(models.Model):
    """Database-backed asynchronous email queue (transactional outbox pattern).

    Confirmation emails are enqueued here with a single fast INSERT during the
    booking request, then actually sent by the ``process_email_outbox``
    management command so a slow SMTP server never blocks the booking.
    Failed deliveries are retried automatically with exponential backoff up to
    ``max_attempts``.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    recipient = models.EmailField(db_index=True)
    subject = models.CharField(max_length=255)
    plain_body = models.TextField(blank=True, default='')
    html_body = models.TextField(blank=True, default='')
    pdf_filename = models.CharField(max_length=255, blank=True, default='')
    pdf_attachment = models.BinaryField(null=True, blank=True, help_text="Generated PDF M-ticket (application/pdf)")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    attempts = models.PositiveIntegerField(default=0, help_text="Delivery attempts so far")
    max_attempts = models.PositiveIntegerField(default=6, help_text="Attempts before the message is marked failed")
    next_attempt_at = models.DateTimeField(db_index=True, default=timezone.now, null=True, blank=True, help_text="When the message becomes eligible for delivery; NULL once permanently failed")
    last_error = models.TextField(blank=True, default='')
    locked_at = models.DateTimeField(null=True, blank=True, help_text="When a worker claimed this message")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'next_attempt_at']),
        ]

    def __str__(self):
        return f'Email to {self.recipient} [{self.status}]'