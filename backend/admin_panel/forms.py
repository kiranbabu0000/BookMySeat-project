from datetime import date
from urllib.parse import urlparse

from django import forms
from django.contrib.auth.models import User
from django.utils import timezone
from movies.models import Movie, Theater, Seat, Booking
from .models import Genre, Language, CastMember, Theatre, Screen, Show, Trailer, MovieImage, AdminProfile, AdminPermission, Coupon, Notification, Review, PaymentTransaction


class AdminLoginForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Password'})
    )


class MovieForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['status'].initial = 'now_showing'
            self.fields['show_on_homepage'].initial = True

    genres = forms.ModelMultipleChoiceField(
        queryset=Genre.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={
            'class': 'form-control',
            'placeholder': 'Select genres...'
        }),
    )
    languages = forms.ModelMultipleChoiceField(
        queryset=Language.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={
            'class': 'form-control',
            'placeholder': 'Select languages...'
        }),
    )

    class Meta:
        model = Movie
        fields = [
            'name', 'category', 'description', 'story', 'duration', 'release_date', 'certificate',
            'rating', 'imdb_rating', 'image', 'thumbnail', 'banner', 'cast',
            'director', 'producer', 'writer', 'music_director', 'cinematographer',
            'production_company', 'trailer_url', 'status',
            'show_on_homepage',
            'genres', 'languages',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'story': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'duration': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Duration in minutes'}),
            'release_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'certificate': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'U, UA, A'}),
            'rating': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'imdb_rating': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
            'thumbnail': forms.FileInput(attrs={'class': 'form-control'}),
            'banner': forms.FileInput(attrs={'class': 'form-control'}),
            'cast': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'director': forms.TextInput(attrs={'class': 'form-control'}),
            'producer': forms.TextInput(attrs={'class': 'form-control'}),
            'writer': forms.TextInput(attrs={'class': 'form-control'}),
            'music_director': forms.TextInput(attrs={'class': 'form-control'}),
            'cinematographer': forms.TextInput(attrs={'class': 'form-control'}),
            'production_company': forms.TextInput(attrs={'class': 'form-control'}),
            'trailer_url': forms.URLInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'show_on_homepage': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if not name or not name.strip():
            raise forms.ValidationError('This field is required.')
        return name.strip()

    def clean_duration(self):
        duration = self.cleaned_data.get('duration')
        if duration is not None and duration <= 0:
            raise forms.ValidationError('Ensure this value is greater than 0.')
        return duration

    def clean_rating(self):
        rating = self.cleaned_data.get('rating')
        if rating is None:
            raise forms.ValidationError('This field is required.')
        if rating < 0 or rating > 10:
            raise forms.ValidationError('Ensure this value is between 0 and 10.')
        return rating

    def clean_imdb_rating(self):
        imdb_rating = self.cleaned_data.get('imdb_rating')
        if imdb_rating is not None:
            if imdb_rating < 0 or imdb_rating > 10:
                raise forms.ValidationError('Ensure this value is between 0 and 10.')
        return imdb_rating

    def clean_trailer_url(self):
        url = self.cleaned_data.get('trailer_url')
        if url and 'youtube.com' not in url and 'youtu.be' not in url:
            raise forms.ValidationError('Enter a valid YouTube URL.')
        return url

    def clean_certificate(self):
        certificate = self.cleaned_data.get('certificate')
        allowed = ['U', 'UA', 'A', 'PG', 'PG-13', 'R']
        if certificate:
            if certificate not in allowed:
                raise forms.ValidationError(
                    f"Select a valid choice. '{certificate}' is not one of the available choices."
                )
        return certificate

    def clean_languages(self):
        languages = self.cleaned_data.get('languages')
        if Language.objects.exists() and not languages:
            raise forms.ValidationError('Select at least one language.')
        return languages

    def clean_genres(self):
        genres = self.cleaned_data.get('genres')
        if Genre.objects.exists() and not genres:
            raise forms.ValidationError('Select at least one genre.')
        return genres

    def clean(self):
        cleaned_data = super().clean()

        release_date = cleaned_data.get('release_date')
        if release_date and not self.instance.pk:
            if release_date < date.today():
                self.add_error('release_date', 'Release date cannot be in the past.')

        duration = cleaned_data.get('duration')
        if duration is not None and duration <= 0:
            self.add_error('duration', 'Ensure this value is greater than 0.')

        return cleaned_data

    def save(self, commit=True):
        if self.instance.pk:
            preserved = {}
            for field_name in ('image', 'thumbnail', 'banner'):
                if field_name not in self.files:
                    preserved[field_name] = (
                        type(self.instance).objects.filter(
                            pk=self.instance.pk,
                        ).values_list(field_name, flat=True).first() or ''
                    )
            instance = super().save(commit=False)
            for field_name, value in preserved.items():
                if value and not getattr(instance, field_name):
                    setattr(instance, field_name, value)
            if commit:
                instance.save()
                self.save_m2m()
            return instance
        return super().save(commit=commit)


class GenreForm(forms.ModelForm):
    class Meta:
        model = Genre
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter genre name'}),
        }


class LanguageForm(forms.ModelForm):
    class Meta:
        model = Language
        fields = ['name', 'code']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter language name'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., EN, HI'}),
        }


class CastMemberForm(forms.ModelForm):
    class Meta:
        model = CastMember
        fields = ['movie', 'name', 'character_name', 'image', 'role']
        widgets = {
            'movie': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Actor name'}),
            'character_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Character name'}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
        }


class TheatreForm(forms.ModelForm):
    class Meta:
        model = Theatre
        fields = ['name', 'location', 'address', 'city', 'contact', 'description', 'facilities', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Theatre name'}),
            'location': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Location'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'contact': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'facilities': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ScreenForm(forms.ModelForm):
    class Meta:
        model = Screen
        fields = ['theatre', 'name', 'size', 'capacity', 'seat_layout']
        widgets = {
            'theatre': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Screen name (e.g., Screen 1)'}),
            'size': forms.Select(attrs={'class': 'form-select'}),
            'capacity': forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
            'seat_layout': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def clean(self):
        cleaned = super().clean()
        from admin_panel.layouts import build_layout_spec, capacity_of
        size = cleaned.get('size') or getattr(self.instance, 'size', 'small') or 'small'
        spec = build_layout_spec(size)
        cleaned['capacity'] = capacity_of(spec)
        cleaned['rows'] = spec['rows']
        cleaned['cols_per_section'] = spec['cols_per_section']
        cleaned['layout_spec'] = spec
        return cleaned

    def save(self, commit=True):
        screen = super().save(commit=False)
        screen.capacity = self.cleaned_data.get('capacity') or screen.capacity
        screen.rows = self.cleaned_data.get('rows') or 0
        screen.cols_per_section = self.cleaned_data.get('cols_per_section') or 0
        screen.layout_spec = self.cleaned_data.get('layout_spec') or screen.layout_spec
        if commit:
            screen.save()
        return screen


class ShowForm(forms.ModelForm):
    class Meta:
        model = Show
        fields = ['movie', 'theatre', 'screen', 'date', 'time', 'ticket_price', 'status']
        widgets = {
            'movie': forms.Select(attrs={'class': 'form-select'}),
            'theatre': forms.Select(attrs={'class': 'form-select'}),
            'screen': forms.Select(attrs={'class': 'form-select'}),
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'ticket_price': forms.NumberInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }


class TrailerForm(forms.ModelForm):
    class Meta:
        model = Trailer
        fields = ['movie', 'title', 'url', 'is_featured']
        widgets = {
            'movie': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Trailer title'}),
            'url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'YouTube URL'}),
            'is_featured': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_url(self):
        url = self.cleaned_data.get('url', '')
        if url:
            host = urlparse(url).netloc.lower()
            if host not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
                raise forms.ValidationError('Only YouTube URLs are allowed.')
        return url


class MovieImageForm(forms.ModelForm):
    class Meta:
        model = MovieImage
        fields = ['movie', 'image', 'caption']
        widgets = {
            'movie': forms.Select(attrs={'class': 'form-select'}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
            'caption': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional caption'}),
        }


class SeatStatusForm(forms.Form):
    seat_id = forms.IntegerField(widget=forms.HiddenInput())
    status = forms.ChoiceField(
        choices=[
            ('available', 'Available'),
            ('booked', 'Booked'),
            ('blocked', 'Blocked'),
            ('maintenance', 'Maintenance'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )


class BookingSearchForm(forms.Form):
    movie = forms.ModelChoiceField(
        queryset=Movie.objects.all(), required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    user = forms.CharField(
        max_length=150, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'})
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    theatre = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        theatres = Theater.objects.values_list('name', flat=True).distinct().order_by('name')
        self.fields['theatre'].choices = [('', 'All Theatres')] + [(t, t) for t in theatres]


class PaymentSearchForm(forms.Form):
    user = forms.CharField(
        max_length=150, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'})
    )
    status = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    gateway_order_id = forms.CharField(
        max_length=255, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Order / Payment ID'})
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = [('', 'All Statuses')] + list(PaymentTransaction.STATUS_CHOICES)


class StaffCreateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'first_name', 'last_name']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'password': forms.PasswordInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def clean_password(self):
        return self.cleaned_data.get('password')


class StaffUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'is_active']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class AdminProfileForm(forms.ModelForm):
    class Meta:
        model = AdminProfile
        fields = ['role', 'department', 'phone', 'is_active']
        widgets = {
            'role': forms.Select(attrs={'class': 'form-select'}),
            'department': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class AdminProfileSelfEditForm(forms.ModelForm):
    """Profile fields a user may edit about themselves.

    Role and activation status are deliberately excluded so a user can never
    promote or deactivate their own account.
    """

    class Meta:
        model = AdminProfile
        fields = ['department', 'phone']
        widgets = {
            'department': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
        }


class AdminUserSelfEditForm(forms.ModelForm):
    """Basic identity fields a logged-in admin may update for themselves."""

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }


class AdminPermissionForm(forms.ModelForm):
    class Meta:
        model = AdminPermission
        fields = ['module', 'can_view', 'can_create', 'can_edit', 'can_delete']
        widgets = {
            'module': forms.Select(attrs={'class': 'form-select'}),
            'can_view': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_create': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_edit': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_delete': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class CouponForm(forms.ModelForm):
    class Meta:
        model = Coupon
        fields = ['code', 'description', 'discount_percent', 'discount_amount', 'max_uses', 'min_order_amount', 'is_active', 'valid_from', 'valid_to']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'discount_percent': forms.NumberInput(attrs={'class': 'form-control'}),
            'discount_amount': forms.NumberInput(attrs={'class': 'form-control'}),
            'max_uses': forms.NumberInput(attrs={'class': 'form-control'}),
            'min_order_amount': forms.NumberInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'valid_from': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'valid_to': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
        }


class NotificationForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = ['title', 'message', 'notification_type', 'link']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'message': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'notification_type': forms.Select(attrs={'class': 'form-control'}),
            'link': forms.URLInput(attrs={'class': 'form-control'}),
        }


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['movie', 'user', 'booking', 'rating', 'comment', 'is_approved', 'is_hidden', 'is_reported']
        widgets = {
            'movie': forms.Select(attrs={'class': 'form-select'}),
            'user': forms.Select(attrs={'class': 'form-select'}),
            'booking': forms.Select(attrs={'class': 'form-select'}),
            'rating': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '5'}),
            'comment': forms.Textarea(attrs={'class': 'form-control'}),
            'is_approved': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_hidden': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_reported': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_rating(self):
        rating = self.cleaned_data.get('rating')
        if rating is not None and not (1 <= rating <= 5):
            raise forms.ValidationError('Rating must be between 1 and 5.')
        return rating


class ReserveBookingForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=User.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    movie = forms.ModelChoiceField(
        queryset=Movie.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    show = forms.ModelChoiceField(
        queryset=Theater.objects.filter(time__gte=timezone.now()).select_related('movie'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    seat_count = forms.IntegerField(
        min_value=1, max_value=10, initial=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )


class RefundForm(forms.Form):
    booking_id = forms.IntegerField(widget=forms.HiddenInput())
    refund_amount = forms.DecimalField(
        max_digits=10, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3})
    )


class ShowFilterForm(forms.Form):
    movie = forms.ModelChoiceField(
        queryset=Movie.objects.all(), required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    theatre = forms.ModelChoiceField(
        queryset=Theatre.objects.all(), required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    status = forms.ChoiceField(
        choices=[('', 'All'), ('active', 'Active'), ('cancelled', 'Cancelled'), ('completed', 'Completed'), ('sold_out', 'Sold Out'), ('paused', 'Paused')],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
