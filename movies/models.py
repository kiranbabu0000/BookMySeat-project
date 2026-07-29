from django.db import models
from django.contrib.auth.models import User


class Movie(models.Model):
    STATUS_CHOICES = [
        ('coming_soon', 'Coming Soon'),
        ('now_showing', 'Now Showing'),
        ('ended', 'Ended'),
    ]
    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to="movies/")
    thumbnail = models.ImageField(upload_to="movies/thumbnails/", blank=True, null=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1)
    cast = models.TextField()
    description = models.TextField(blank=True, null=True)
    duration = models.PositiveIntegerField(blank=True, null=True, help_text="Duration in minutes")
    release_date = models.DateField(blank=True, null=True)
    certificate = models.CharField(max_length=20, blank=True, null=True, help_text="e.g., U, UA, A")
    banner = models.ImageField(upload_to="movies/banners/", blank=True, null=True)
    director = models.CharField(max_length=255, blank=True, null=True)
    producer = models.CharField(max_length=255, blank=True, null=True)
    trailer_url = models.URLField(max_length=500, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='now_showing')
    imdb_rating = models.DecimalField(max_digits=3, decimal_places=1, blank=True, null=True)
    genres = models.ManyToManyField('admin_panel.Genre', blank=True, related_name='movies')
    languages = models.ManyToManyField('admin_panel.Language', blank=True, related_name='movies')

    def __str__(self):
        return self.name

class Theater(models.Model):
    name = models.CharField(max_length=255)
    movie = models.ForeignKey(Movie,on_delete=models.CASCADE,related_name='theaters')
    time= models.DateTimeField()

    def __str__(self):
        return f'{self.name} - {self.movie.name} at {self.time}'

class Seat(models.Model):
    theater = models.ForeignKey(Theater,on_delete=models.CASCADE,related_name='seats')
    seat_number = models.CharField(max_length=10)
    is_booked=models.BooleanField(default=False)

    def __str__(self):
        return f'{self.seat_number} in {self.theater.name}'

class Booking(models.Model):
    user=models.ForeignKey(User,on_delete=models.CASCADE)
    seat=models.OneToOneField(Seat,on_delete=models.CASCADE)
    movie=models.ForeignKey(Movie,on_delete=models.CASCADE)
    theater=models.ForeignKey(Theater,on_delete=models.CASCADE)
    booked_at=models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return f'Booking by{self.user.username} for {self.seat.seat_number} at {self.theater.name}'