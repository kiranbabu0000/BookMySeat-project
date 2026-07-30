from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.db.models import Count, Sum, Q, Avg, Max
from django.db.models.functions import TruncMonth, TruncDate
from django.utils import timezone
from django.core.paginator import Paginator
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View, TemplateView, DetailView
from django.urls import reverse_lazy, reverse
from django.http import JsonResponse, HttpResponseRedirect
import json
from datetime import datetime, date, timedelta

from movies.models import Movie, Theater, Seat, Booking
from .models import Genre, Language, CastMember, Theatre, Screen, Show, Trailer, MovieImage, AdminProfile, AdminPermission, AuditLog, Coupon, Notification, Review, Payment
from .forms import (
    AdminLoginForm, MovieForm, GenreForm, LanguageForm, CastMemberForm,
    TheatreForm, ScreenForm, ShowForm, TrailerForm, MovieImageForm,
    BookingSearchForm, StaffCreateForm, StaffUpdateForm, AdminProfileForm,
    AdminPermissionForm, CouponForm, NotificationForm, ReviewForm,
    ReserveBookingForm, RefundForm
)
from .decorators import admin_session_required, AdminSessionMixin, permission_required


def admin_login_view(request):
    if request.user.is_authenticated:
        if request.session.get('is_admin_authenticated') and (request.user.is_staff or request.user.is_superuser):
            return redirect('admin_dashboard')
        if request.user.is_staff or request.user.is_superuser:
            pass
        elif not request.user.is_staff:
            return redirect('/')

    if request.method == 'POST':
        form = AdminLoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(request, username=username, password=password)
            if user is not None:
                if user.is_staff or user.is_superuser:
                    login(request, user)
                    request.session['is_admin_authenticated'] = True
                    request.session['admin_login_time'] = str(timezone.now())
                    AuditLog.objects.create(
                        user=user,
                        action='Admin Login',
                        module='Auth',
                        ip_address=request.META.get('REMOTE_ADDR')
                    )
                    return redirect('admin_dashboard')
                else:
                    messages.error(request, 'Unauthorized Access')
            else:
                messages.error(request, 'Invalid username or password')
    else:
        form = AdminLoginForm()
    return render(request, 'admin/login.html', {'form': form})


def admin_logout_view(request):
    user = request.user
    if 'is_admin_authenticated' in request.session:
        del request.session['is_admin_authenticated']
    logout(request)
    if user.is_authenticated:
        AuditLog.objects.create(
            user=user,
            action='Admin Logout',
            module='Auth',
            ip_address=request.META.get('REMOTE_ADDR')
        )
    return redirect('admin_login')


class DashboardView(AdminSessionMixin, TemplateView):
    template_name = 'admin/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        week_start = today - timedelta(days=7)
        month_start = today.replace(day=1)

        today_bookings = Booking.objects.filter(booked_at__date=today)
        yesterday_bookings = Booking.objects.filter(booked_at__date=yesterday)
        weekly_bookings = Booking.objects.filter(booked_at__date__gte=week_start)
        monthly_bookings = Booking.objects.filter(booked_at__date__gte=month_start)

        avg_price = 10
        context['today_bookings'] = today_bookings.count()
        context['today_revenue'] = today_bookings.count() * avg_price
        context['yesterday_revenue'] = yesterday_bookings.count() * avg_price
        context['weekly_revenue'] = weekly_bookings.count() * avg_price
        context['monthly_revenue'] = monthly_bookings.count() * avg_price
        context['total_movies'] = Movie.objects.count()
        context['total_bookings'] = Booking.objects.count()
        context['total_users'] = User.objects.count()
        context['total_staff'] = AdminProfile.objects.count()
        context['total_theatres'] = Theater.objects.values('name').distinct().count()
        context['total_screens'] = Theater.objects.count()
        context['total_shows'] = Theater.objects.count()
        context['active_movies'] = Movie.objects.filter(status='now_showing').count()
        context['upcoming_movies'] = Movie.objects.filter(status='coming_soon').count()
        context['pending_refunds'] = Booking.objects.filter(booked_at__date__gte=today - timedelta(days=7)).count()
        total_seats = Seat.objects.count()
        booked_seats = Seat.objects.filter(is_booked=True).count()
        context['total_available_seats'] = Seat.objects.filter(is_booked=False).count()
        context['total_booked_seats'] = booked_seats
        context['occupancy_rate'] = round((booked_seats / total_seats * 100) if total_seats > 0 else 0)

        trending = Movie.objects.annotate(booking_count=Count('booking')).order_by('-booking_count').first()
        context['trending_movie'] = trending

        context['cancelled_today'] = 0
        context['recent_bookings'] = Booking.objects.select_related('user', 'movie', 'theater').order_by('-booked_at')[:10]
        context['shows_running_today'] = Theater.objects.filter(time__date=today).count()
        context['todays_shows'] = Theater.objects.filter(time__date=today).count()
        context['upcoming_shows'] = Theater.objects.filter(time__date__gte=today).count()
        context['notifications'] = Notification.objects.filter(is_read=False).count()

        context['recent_movies'] = Movie.objects.all().order_by('-id')[:5]
        context['recent_theatres'] = Theater.objects.select_related('movie').all().order_by('-id')[:5]
        context['recent_shows'] = Theater.objects.select_related('movie').all().order_by('-time')[:5]
        context['upcoming_movies_list'] = Movie.objects.filter(status='coming_soon').order_by('release_date')[:5]

        context['total_reviews'] = Review.objects.count()
        context['total_coupons'] = Coupon.objects.count()
        context['total_payments'] = Payment.objects.count()

        context['quick_stats'] = json.dumps([
            {'label': 'Today Bookings', 'value': context['today_bookings']},
            {'label': 'Today Revenue', 'value': context['today_revenue']},
            {'label': 'Active Movies', 'value': context['active_movies']},
            {'label': 'Occupancy', 'value': f"{context['occupancy_rate']}%"},
        ])

        labels = []
        data = []
        for k in range(5, -1, -1):
            yr = today.year
            mth = today.month - k
            while mth <= 0:
                mth += 12
                yr -= 1
            month_start = date(yr, mth, 1)
            if mth == 12:
                month_end = date(yr + 1, 1, 1)
            else:
                month_end = date(yr, mth + 1, 1)
            count = Booking.objects.filter(booked_at__date__gte=month_start, booked_at__date__lt=month_end).count()
            labels.append(month_start.strftime('%b %Y'))
            data.append(count)
        context['monthly_labels'] = json.dumps(labels)
        context['monthly_data'] = json.dumps(data)

        return context


class MovieListView(AdminSessionMixin, ListView):
    model = Movie
    template_name = 'admin/movies/movie_list.html'
    context_object_name = 'movies'
    paginate_by = 20

    def get_queryset(self):
        qs = Movie.objects.all().order_by('-id')
        search = self.request.GET.get('search')
        status = self.request.GET.get('status')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(director__icontains=search))
        if status:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Movie.STATUS_CHOICES
        return context


class MovieCreateView(AdminSessionMixin, CreateView):
    model = Movie
    form_class = MovieForm
    template_name = 'admin/movies/movie_form.html'
    success_url = reverse_lazy('admin_movie_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Movie added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Movie Added',
            module='Movie',
            object_id=self.object.id,
            details=f'Added movie: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class MovieUpdateView(AdminSessionMixin, UpdateView):
    model = Movie
    form_class = MovieForm
    template_name = 'admin/movies/movie_form.html'
    success_url = reverse_lazy('admin_movie_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Movie updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Movie Updated',
            module='Movie',
            object_id=self.object.id,
            details=f'Updated movie: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class MovieDeleteView(AdminSessionMixin, DeleteView):
    model = Movie
    template_name = 'admin/movies/movie_confirm_delete.html'
    success_url = reverse_lazy('admin_movie_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        movie = self.get_object()
        today = timezone.now().date()
        context['active_bookings'] = Booking.objects.filter(movie=movie).exclude(seat__isnull=True).count()
        context['future_shows'] = Show.objects.filter(movie=movie, date__gte=today, status='active').count()
        context['past_shows'] = Show.objects.filter(movie=movie, date__lt=today).count()
        context['related_trailers'] = Trailer.objects.filter(movie=movie).count()
        context['related_gallery'] = MovieImage.objects.filter(movie=movie).count()
        context['related_cast'] = CastMember.objects.filter(movie=movie).count()
        context['has_dependencies'] = any([context['active_bookings'], context['future_shows'], context['past_shows'], context['related_trailers'], context['related_gallery'], context['related_cast']])
        context['can_hard_delete'] = not any([context['active_bookings'], context['future_shows']])
        return context

    def delete(self, request, *args, **kwargs):
        movie = self.get_object()
        action = request.POST.get('action', 'archive')
        today = timezone.now().date()
        active_bookings = Booking.objects.filter(movie=movie).exclude(seat__isnull=True).count()
        future_shows = Show.objects.filter(movie=movie, date__gte=today, status='active').count()

        if action == 'hard_delete' and not active_bookings and not future_shows:
            name = movie.name
            movie.delete()
            AuditLog.objects.create(
                user=request.user,
                action='Movie Permanently Deleted',
                module='Movie',
                object_id=movie.id,
                details=f'Permanently deleted movie: {name}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Movie "{name}" has been permanently deleted.')
            return redirect(self.success_url)
        else:
            movie.is_deleted = True
            movie.show_on_homepage = False
            movie.status = 'archived'
            movie.save()
            AuditLog.objects.create(
                user=request.user,
                action='Movie Deleted',
                module='Movie',
                object_id=movie.id,
                details=f'Soft-deleted movie: {movie.name}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Movie "{movie.name}" has been archived and removed from all public listings.')
            return redirect(self.success_url)


class MovieDetailView(AdminSessionMixin, TemplateView):
    template_name = 'admin/movies/movie_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        movie = get_object_or_404(Movie, id=self.kwargs['pk'])
        context['movie'] = movie
        context['cast_members'] = CastMember.objects.filter(movie=movie)
        context['trailers'] = Trailer.objects.filter(movie=movie)
        context['gallery'] = MovieImage.objects.filter(movie=movie)
        context['shows'] = Show.objects.filter(movie=movie).select_related('theatre', 'screen')
        context['bookings'] = Booking.objects.filter(movie=movie).select_related('user', 'theater').order_by('-booked_at')[:20]
        return context


@admin_session_required
def movie_toggle_status(request, pk):
    movie = get_object_or_404(Movie, id=pk)
    status_cycle = {'draft': 'coming_soon', 'coming_soon': 'now_showing', 'now_showing': 'archived', 'archived': 'hidden', 'hidden': 'draft'}
    old_status = movie.status
    movie.status = status_cycle.get(movie.status, 'now_showing')
    movie.save()
    AuditLog.objects.create(
        user=request.user,
        action='Movie Status Toggled',
        module='Movie',
        object_id=movie.id,
        details=f'Changed {movie.name} status from {old_status} to {movie.status}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'Movie "{movie.name}" status changed to {movie.get_status_display()}.')
    return redirect('admin_movie_list')


@admin_session_required
def movie_toggle_homepage(request, pk):
    movie = get_object_or_404(Movie, id=pk)
    movie.show_on_homepage = not movie.show_on_homepage
    movie.save()
    status = 'shown' if movie.show_on_homepage else 'hidden'
    AuditLog.objects.create(
        user=request.user,
        action='Movie Homepage Toggled',
        module='Movie',
        object_id=movie.id,
        details=f'{movie.name} homepage visibility: {status}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'Movie "{movie.name}" homepage visibility updated.')
    return redirect('admin_movie_list')


@admin_session_required
def movie_restore(request, pk):
    movie = get_object_or_404(Movie, id=pk)
    movie.is_deleted = False
    movie.show_on_homepage = True
    if movie.status in ['archived', 'hidden']:
        movie.status = 'draft'
    movie.save()
    AuditLog.objects.create(
        user=request.user,
        action='Movie Restored',
        module='Movie',
        object_id=movie.id,
        details=f'Restored movie: {movie.name}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'Movie "{movie.name}" restored successfully and is visible again in public listings.')
    return redirect('admin_movie_list')


@admin_session_required
def search_suggestions(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 1:
        return JsonResponse([], safe=False)
    movies = Movie.objects.filter(
        name__icontains=q,
        is_deleted=False
    ).exclude(
        status__in=['archived', 'hidden']
    )[:8]
    results = []
    for m in movies:
        results.append({
            'id': m.id,
            'name': m.name,
            'image': m.image.url if m.image and hasattr(m.image, 'url') else '',
            'url': f'/movies/{m.id}/'
        })
    return JsonResponse(results, safe=False)


@admin_session_required
def genre_ajax_add(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'Name is required'}, status=400)
        genre, created = Genre.objects.get_or_create(name=name)
        return JsonResponse({
            'id': genre.id,
            'name': genre.name,
            'slug': genre.slug,
            'created': created,
        })
    return JsonResponse({'error': 'POST required'}, status=405)


@admin_session_required
def language_ajax_add(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'Name is required'}, status=400)
        lang, created = Language.objects.get_or_create(name=name)
        return JsonResponse({
            'id': lang.id,
            'name': lang.name,
            'code': lang.code,
            'created': created,
        })
    return JsonResponse({'error': 'POST required'}, status=405)


class GenreListView(AdminSessionMixin, ListView):
    model = Genre
    template_name = 'admin/genres/genre_list.html'
    context_object_name = 'genres'
    paginate_by = 20


class GenreCreateView(AdminSessionMixin, CreateView):
    model = Genre
    form_class = GenreForm
    template_name = 'admin/genres/genre_form.html'
    success_url = reverse_lazy('admin_genre_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Genre added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Genre Added',
            module='Genre',
            object_id=self.object.id,
            details=f'Added genre: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class GenreUpdateView(AdminSessionMixin, UpdateView):
    model = Genre
    form_class = GenreForm
    template_name = 'admin/genres/genre_form.html'
    success_url = reverse_lazy('admin_genre_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Genre updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Genre Updated',
            module='Genre',
            object_id=self.object.id,
            details=f'Updated genre: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class GenreDeleteView(AdminSessionMixin, DeleteView):
    model = Genre
    template_name = 'admin/genres/genre_confirm_delete.html'
    success_url = reverse_lazy('admin_genre_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Genre Deleted',
            module='Genre',
            object_id=obj.id,
            details=f'Deleted genre: {obj.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Genre deleted successfully.')
        return super().delete(request, *args, **kwargs)


class LanguageListView(AdminSessionMixin, ListView):
    model = Language
    template_name = 'admin/languages/language_list.html'
    context_object_name = 'languages'
    paginate_by = 20


class LanguageCreateView(AdminSessionMixin, CreateView):
    model = Language
    form_class = LanguageForm
    template_name = 'admin/languages/language_form.html'
    success_url = reverse_lazy('admin_language_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Language added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Language Added',
            module='Language',
            object_id=self.object.id,
            details=f'Added language: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class LanguageUpdateView(AdminSessionMixin, UpdateView):
    model = Language
    form_class = LanguageForm
    template_name = 'admin/languages/language_form.html'
    success_url = reverse_lazy('admin_language_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Language updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Language Updated',
            module='Language',
            object_id=self.object.id,
            details=f'Updated language: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class LanguageDeleteView(AdminSessionMixin, DeleteView):
    model = Language
    template_name = 'admin/languages/language_confirm_delete.html'
    success_url = reverse_lazy('admin_language_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Language Deleted',
            module='Language',
            object_id=obj.id,
            details=f'Deleted language: {obj.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Language deleted successfully.')
        return super().delete(request, *args, **kwargs)


class CastListView(AdminSessionMixin, ListView):
    model = CastMember
    template_name = 'admin/cast/cast_list.html'
    context_object_name = 'cast_members'
    paginate_by = 20

    def get_queryset(self):
        qs = CastMember.objects.select_related('movie').all().order_by('-id')
        search = self.request.GET.get('search')
        movie_id = self.request.GET.get('movie')
        if search:
            qs = qs.filter(name__icontains=search)
        if movie_id:
            qs = qs.filter(movie_id=movie_id)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['movies'] = Movie.objects.all()
        return context


class CastCreateView(AdminSessionMixin, CreateView):
    model = CastMember
    form_class = CastMemberForm
    template_name = 'admin/cast/cast_form.html'
    success_url = reverse_lazy('admin_cast_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Cast member added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Cast Added',
            module='Cast',
            object_id=self.object.id,
            details=f'Added cast: {self.object.name} for {self.object.movie.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class CastUpdateView(AdminSessionMixin, UpdateView):
    model = CastMember
    form_class = CastMemberForm
    template_name = 'admin/cast/cast_form.html'
    success_url = reverse_lazy('admin_cast_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Cast member updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Cast Updated',
            module='Cast',
            object_id=self.object.id,
            details=f'Updated cast: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class CastDeleteView(AdminSessionMixin, DeleteView):
    model = CastMember
    template_name = 'admin/cast/cast_confirm_delete.html'
    success_url = reverse_lazy('admin_cast_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Cast Deleted',
            module='Cast',
            object_id=obj.id,
            details=f'Deleted cast: {obj.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Cast member deleted successfully.')
        return super().delete(request, *args, **kwargs)


class TheatreListView(AdminSessionMixin, ListView):
    model = Theater
    template_name = 'admin/theatres/theatre_list.html'
    context_object_name = 'theatres'
    paginate_by = 20

    def get_queryset(self):
        qs = Theater.objects.values('name').annotate(
            show_count=Count('id'),
            movie_count=Count('movie', distinct=True),
            last_show=Max('time')
        ).order_by('-last_show')
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(name__icontains=search)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_theatres'] = Theater.objects.values('name').distinct().count()
        context['theatre_profiles'] = Theatre.objects.all().order_by('name')
        return context


class TheatreCreateView(AdminSessionMixin, CreateView):
    model = Theatre
    form_class = TheatreForm
    template_name = 'admin/theatres/theatre_form.html'
    success_url = reverse_lazy('admin_theatre_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Theatre added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Theatre Added',
            module='Theatre',
            object_id=self.object.id,
            details=f'Added theatre: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class TheatreUpdateView(AdminSessionMixin, UpdateView):
    model = Theatre
    form_class = TheatreForm
    template_name = 'admin/theatres/theatre_form.html'
    success_url = reverse_lazy('admin_theatre_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Theatre updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Theatre Updated',
            module='Theatre',
            object_id=self.object.id,
            details=f'Updated theatre: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class TheatreDeleteView(AdminSessionMixin, DeleteView):
    model = Theatre
    template_name = 'admin/theatres/theatre_confirm_delete.html'
    success_url = reverse_lazy('admin_theatre_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Theatre Deleted',
            module='Theatre',
            object_id=obj.id,
            details=f'Deleted theatre: {obj.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Theatre deleted successfully.')
        return super().delete(request, *args, **kwargs)


@admin_session_required
def theatre_movie_management(request, pk):
    theatre = get_object_or_404(Theatre, id=pk)
    assigned_movies = Movie.objects.filter(theaters__name=theatre.name, is_deleted=False).distinct()
    running_shows = Show.objects.filter(theatre=theatre, status='active', date__gte=timezone.now().date()).select_related('movie', 'screen').order_by('date', 'time')
    all_movies = Movie.objects.filter(is_deleted=False).exclude(status__in=['archived', 'hidden'])
    return render(request, 'admin/theatres/theatre_movies.html', {
        'theatre': theatre,
        'assigned_movies': assigned_movies,
        'running_shows': running_shows,
        'all_movies': all_movies,
    })


@admin_session_required
def theatre_remove_movie(request):
    if request.method == 'POST':
        theatre_id = request.POST.get('theatre_id')
        movie_id = request.POST.get('movie_id')
        theatre = get_object_or_404(Theatre, id=theatre_id)
        movie = get_object_or_404(Movie, id=movie_id)
        future_shows = Show.objects.filter(
            theatre=theatre, movie=movie,
            date__gte=timezone.now().date(),
            status='active'
        )
        count = future_shows.count()
        future_shows.update(status='cancelled')
        old_theaters = Theater.objects.filter(name=theatre.name, movie=movie, time__gte=timezone.now())
        old_count = old_theaters.count()
        old_theaters.delete()
        AuditLog.objects.create(
            user=request.user,
            action='Movie Removed from Theatre',
            module='Theatre',
            details=f'Removed {movie.name} from {theatre.name}: cancelled {count} show(s), removed {old_count} old listing(s)',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, f'"{movie.name}" removed from "{theatre.name}". {count} future show(s) cancelled.')
    return redirect('admin_theatre_list')


class ScreenListView(AdminSessionMixin, ListView):
    model = Theater
    template_name = 'admin/screens/screen_list.html'
    context_object_name = 'screens'
    paginate_by = 20

    def get_queryset(self):
        qs = Theater.objects.select_related('movie').annotate(
            total_seats=Count('seats'),
            available_seats=Count('seats', filter=Q(seats__is_booked=False)),
            booked_seats=Count('seats', filter=Q(seats__is_booked=True))
        ).order_by('-time')
        theatre_name = self.request.GET.get('theatre')
        if theatre_name:
            qs = qs.filter(name__icontains=theatre_name)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['theatre_names'] = Theater.objects.values_list('name', flat=True).distinct().order_by('name')
        return context


class ScreenCreateView(AdminSessionMixin, CreateView):
    model = Screen
    form_class = ScreenForm
    template_name = 'admin/screens/screen_form.html'
    success_url = reverse_lazy('admin_screen_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Screen added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Screen Added',
            module='Screen',
            object_id=self.object.id,
            details=f'Added screen: {self.object.name} at {self.object.theatre.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class ScreenUpdateView(AdminSessionMixin, UpdateView):
    model = Screen
    form_class = ScreenForm
    template_name = 'admin/screens/screen_form.html'
    success_url = reverse_lazy('admin_screen_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Screen updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Screen Updated',
            module='Screen',
            object_id=self.object.id,
            details=f'Updated screen: {self.object.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class ScreenDeleteView(AdminSessionMixin, DeleteView):
    model = Screen
    template_name = 'admin/screens/screen_confirm_delete.html'
    success_url = reverse_lazy('admin_screen_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Screen Deleted',
            module='Screen',
            object_id=obj.id,
            details=f'Deleted screen: {obj.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Screen deleted successfully.')
        return super().delete(request, *args, **kwargs)


class ShowListView(AdminSessionMixin, ListView):
    model = Theater
    template_name = 'admin/shows/show_list.html'
    context_object_name = 'shows'
    paginate_by = 20

    def get_queryset(self):
        today = timezone.now().date()
        qs = Theater.objects.select_related('movie').annotate(
            total_seats=Count('seats'),
            available_seats=Count('seats', filter=Q(seats__is_booked=False)),
            booked_seats=Count('seats', filter=Q(seats__is_booked=True))
        ).order_by('-time')
        movie_id = self.request.GET.get('movie')
        theatre_name = self.request.GET.get('theatre')
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if movie_id:
            qs = qs.filter(movie_id=movie_id)
        if theatre_name:
            qs = qs.filter(name__icontains=theatre_name)
        if date_from:
            qs = qs.filter(time__date__gte=date_from)
        if date_to:
            qs = qs.filter(time__date__lte=date_to)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['movies'] = Movie.objects.all()
        context['theatre_names'] = Theater.objects.values_list('name', flat=True).distinct().order_by('name')
        return context


class ShowCreateView(AdminSessionMixin, CreateView):
    model = Show
    form_class = ShowForm
    template_name = 'admin/shows/show_form.html'
    success_url = reverse_lazy('admin_show_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Show added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Show Added',
            module='Show',
            object_id=self.object.id,
            details=f'Added show: {self.object.movie.name} at {self.object.theatre.name} on {self.object.date}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class ShowUpdateView(AdminSessionMixin, UpdateView):
    model = Show
    form_class = ShowForm
    template_name = 'admin/shows/show_form.html'
    success_url = reverse_lazy('admin_show_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Show updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Show Updated',
            module='Show',
            object_id=self.object.id,
            details=f'Updated show: {self.object.movie.name} at {self.object.theatre.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class ShowDeleteView(AdminSessionMixin, DeleteView):
    model = Show
    template_name = 'admin/shows/show_confirm_delete.html'
    success_url = reverse_lazy('admin_show_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Show Deleted',
            module='Show',
            object_id=obj.id,
            details=f'Deleted show: {obj.movie.name} at {obj.theatre.name} on {obj.date}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Show deleted successfully.')
        return super().delete(request, *args, **kwargs)


@admin_session_required
def show_toggle_status(request, pk):
    show = get_object_or_404(Show, id=pk)
    status_cycle = {'active': 'sold_out', 'sold_out': 'paused', 'paused': 'cancelled', 'cancelled': 'active'}
    old_status = show.status
    show.status = status_cycle.get(show.status, 'active')
    show.save()
    AuditLog.objects.create(
        user=request.user,
        action='Show Status Toggled',
        module='Show',
        object_id=show.id,
        details=f'Changed show status from {old_status} to {show.status}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'Show status changed to {show.status}.')
    return redirect('admin_show_list')


@admin_session_required
def show_bulk_action(request):
    if request.method == 'POST':
        movie_id = request.POST.get('movie')
        theatre_id = request.POST.get('theatre')
        date_val = request.POST.get('date')
        shows = Show.objects.all()
        if movie_id:
            shows = shows.filter(movie_id=movie_id)
        if theatre_id:
            shows = shows.filter(theatre_id=theatre_id)
        if date_val:
            shows = shows.filter(date=date_val)
        count = shows.update(status='cancelled')
        AuditLog.objects.create(
            user=request.user,
            action='Bulk Cancel Shows',
            module='Show',
            details=f'Cancelled {count} shows. Movie:{movie_id}, Theatre:{theatre_id}, Date:{date_val}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, f'{count} show(s) cancelled successfully.')
    return redirect('admin_show_list')


class TrailerListView(AdminSessionMixin, ListView):
    model = Trailer
    template_name = 'admin/trailers/trailer_list.html'
    context_object_name = 'trailers'
    paginate_by = 20

    def get_queryset(self):
        qs = Trailer.objects.select_related('movie').all().order_by('-id')
        movie_id = self.request.GET.get('movie')
        if movie_id:
            qs = qs.filter(movie_id=movie_id)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['movies'] = Movie.objects.all()
        return context


class TrailerCreateView(AdminSessionMixin, CreateView):
    model = Trailer
    form_class = TrailerForm
    template_name = 'admin/trailers/trailer_form.html'
    success_url = reverse_lazy('admin_trailer_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Trailer added successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Trailer Added',
            module='Trailer',
            object_id=self.object.id,
            details=f'Added trailer for {self.object.movie.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class TrailerUpdateView(AdminSessionMixin, UpdateView):
    model = Trailer
    form_class = TrailerForm
    template_name = 'admin/trailers/trailer_form.html'
    success_url = reverse_lazy('admin_trailer_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Trailer updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Trailer Updated',
            module='Trailer',
            object_id=self.object.id,
            details=f'Updated trailer for {self.object.movie.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class TrailerDeleteView(AdminSessionMixin, DeleteView):
    model = Trailer
    template_name = 'admin/trailers/trailer_confirm_delete.html'
    success_url = reverse_lazy('admin_trailer_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Trailer Deleted',
            module='Trailer',
            object_id=obj.id,
            details=f'Deleted trailer for {obj.movie.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Trailer deleted successfully.')
        return super().delete(request, *args, **kwargs)


class MovieImageListView(AdminSessionMixin, ListView):
    model = MovieImage
    template_name = 'admin/images/image_list.html'
    context_object_name = 'images'
    paginate_by = 20

    def get_queryset(self):
        qs = MovieImage.objects.select_related('movie').all().order_by('-uploaded_at')
        movie_id = self.request.GET.get('movie')
        if movie_id:
            qs = qs.filter(movie_id=movie_id)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['movies'] = Movie.objects.all()
        return context


class MovieImageCreateView(AdminSessionMixin, CreateView):
    model = MovieImage
    form_class = MovieImageForm
    template_name = 'admin/images/image_form.html'
    success_url = reverse_lazy('admin_image_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Image uploaded successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Image Added',
            module='MovieImage',
            object_id=self.object.id,
            details=f'Added image for {self.object.movie.name}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class MovieImageDeleteView(AdminSessionMixin, DeleteView):
    model = MovieImage
    template_name = 'admin/images/image_confirm_delete.html'
    success_url = reverse_lazy('admin_image_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Image Deleted',
            module='MovieImage',
            object_id=obj.id,
            details=f'Deleted image for {obj.movie.name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Image deleted successfully.')
        return super().delete(request, *args, **kwargs)


@admin_session_required
def seat_management(request):
    theatre_names = Theater.objects.values_list('name', flat=True).distinct().order_by('name')
    selected_theater = None
    seats = []
    theater_id = request.GET.get('theater')

    if theater_id:
        selected_theater = get_object_or_404(Theater, id=theater_id)
        seats = Seat.objects.filter(theater=selected_theater).order_by('seat_number')
    else:
        seats = Seat.objects.none()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'generate_seats':
            theater_id = request.POST.get('theater_id')
            theater = get_object_or_404(Theater, id=theater_id)
            rows = int(request.POST.get('rows', 8))
            seats_per_row = int(request.POST.get('seats_per_row', 12))
            created_count = 0
            for r in range(1, rows + 1):
                row_label = chr(64 + r) if r <= 26 else f'R{r}'
                for s in range(1, seats_per_row + 1):
                    seat_number = f'{row_label}{s}'
                    _, created = Seat.objects.get_or_create(
                        theater=theater,
                        seat_number=seat_number,
                        defaults={'is_booked': False}
                    )
                    if created:
                        created_count += 1
            AuditLog.objects.create(
                user=request.user,
                action='Seats Generated',
                module='Seat',
                details=f'Generated {created_count} seats for {theater.name} ({rows}x{seats_per_row})',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'{created_count} seats generated successfully.')
            return redirect(f'{reverse("admin_seat_management")}?theater={theater.id}')

        elif action == 'block_seat':
            seat_id = request.POST.get('seat_id')
            seat = get_object_or_404(Seat, id=seat_id)
            seat.is_booked = True
            seat.save()
            AuditLog.objects.create(
                user=request.user,
                action='Seat Blocked',
                module='Seat',
                object_id=seat.id,
                details=f'Blocked seat {seat.seat_number}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Seat {seat.seat_number} blocked.')

        elif action == 'unblock_seat':
            seat_id = request.POST.get('seat_id')
            seat = get_object_or_404(Seat, id=seat_id)
            seat.is_booked = False
            seat.save()
            AuditLog.objects.create(
                user=request.user,
                action='Seat Unblocked',
                module='Seat',
                object_id=seat.id,
                details=f'Unblocked seat {seat.seat_number}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Seat {seat.seat_number} unblocked.')

        elif action == 'maintenance_seat':
            seat_id = request.POST.get('seat_id')
            seat = get_object_or_404(Seat, id=seat_id)
            seat.is_booked = not seat.is_booked
            seat.save()
            AuditLog.objects.create(
                user=request.user,
                action='Seat Maintenance Toggle',
                module='Seat',
                object_id=seat.id,
                details=f'Toggled maintenance for seat {seat.seat_number}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Seat {seat.seat_number} toggled.')

        return redirect(f'{reverse("admin_seat_management")}?theater={theater_id or ""}')

    context = {
        'theatre_names': theatre_names,
        'theatres_list': Theater.objects.select_related('movie').all().order_by('-time'),
        'selected_theater': selected_theater,
        'seats': seats,
    }
    return render(request, 'admin/seat_management.html', context)


class BookingListView(AdminSessionMixin, ListView):
    model = Booking
    template_name = 'admin/bookings/booking_list.html'
    context_object_name = 'bookings'
    paginate_by = 20

    def get_queryset(self):
        qs = Booking.objects.select_related('user', 'movie', 'theater').all().order_by('-booked_at')
        form = BookingSearchForm(self.request.GET)
        if form.is_valid():
            movie = form.cleaned_data.get('movie')
            username = form.cleaned_data.get('user')
            date_from = form.cleaned_data.get('date_from')
            date_to = form.cleaned_data.get('date_to')
            theatre = form.cleaned_data.get('theatre')
            if movie:
                qs = qs.filter(movie=movie)
            if username:
                qs = qs.filter(user__username__icontains=username)
            if date_from:
                qs = qs.filter(booked_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(booked_at__date__lte=date_to)
            if theatre:
                qs = qs.filter(theater__name__icontains=theatre)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = BookingSearchForm(self.request.GET)
        return context


@admin_session_required
def booking_detail(request, pk):
    booking = get_object_or_404(Booking.objects.select_related('user', 'movie', 'theater', 'seat'), id=pk)
    return render(request, 'admin/bookings/booking_detail.html', {'booking': booking})


@admin_session_required
def booking_cancel(request, pk):
    booking = get_object_or_404(Booking, id=pk)
    seat = booking.seat
    if seat:
        seat.is_booked = False
        seat.save()
    AuditLog.objects.create(
        user=request.user,
        action='Booking Cancelled',
        module='Booking',
        object_id=booking.id,
        details=f'Cancelled booking {booking.id} for {booking.movie.name}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    booking.delete()
    messages.success(request, 'Booking cancelled successfully.')
    return redirect('admin_booking_list')


@admin_session_required
def booking_reserve(request):
    if request.method == 'POST':
        form = ReserveBookingForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data['user']
            movie = form.cleaned_data['movie']
            show = form.cleaned_data['show']
            seat_count = form.cleaned_data['seat_count']
            movie_theatres = Theater.objects.filter(movie=movie)
            available_seats = Seat.objects.filter(
                theater__in=movie_theatres,
                is_booked=False
            )[:seat_count]
            if available_seats.count() < seat_count:
                messages.error(request, f'Only {available_seats.count()} seats available, need {seat_count}.')
                return redirect('admin_booking_list')
            created_count = 0
            for seat in available_seats:
                Booking.objects.create(
                    user=user,
                    seat=seat,
                    movie=movie,
                    theater=seat.theater
                )
                seat.is_booked = True
                seat.save()
                created_count += 1
            AuditLog.objects.create(
                user=request.user,
                action='Booking Reserved',
                module='Booking',
                details=f'Reserved {created_count} seat(s) for {user.username} - {movie.name}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'{created_count} seat(s) reserved successfully for {user.username}.')
    return redirect('admin_booking_list')


@admin_session_required
def booking_modify(request, pk):
    booking = get_object_or_404(Booking, id=pk)
    if request.method == 'POST':
        new_seat_id = request.POST.get('new_seat')
        new_seat = get_object_or_404(Seat, id=new_seat_id)
        if new_seat.is_booked:
            messages.error(request, 'Selected seat is already booked.')
            return redirect('booking_detail', pk=pk)
        old_seat = booking.seat
        if old_seat:
            old_seat.is_booked = False
            old_seat.save()
        booking.seat = new_seat
        booking.save()
        new_seat.is_booked = True
        new_seat.save()
        AuditLog.objects.create(
            user=request.user,
            action='Booking Modified',
            module='Booking',
            object_id=booking.id,
            details=f'Changed seat from {old_seat.seat_number if old_seat else "None"} to {new_seat.seat_number}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Booking modified successfully.')
    return redirect('booking_detail', pk=pk)


@admin_session_required
def booking_resend_confirmation(request, pk):
    booking = get_object_or_404(Booking, id=pk)
    messages.success(request, f'Confirmation resent for booking #{booking.id}.')
    return redirect('booking_detail', pk=pk)


class UserListView(AdminSessionMixin, ListView):
    model = User
    template_name = 'admin/users/user_list.html'
    context_object_name = 'users'
    paginate_by = 20

    def get_queryset(self):
        qs = User.objects.all().order_by('-date_joined')
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(
                Q(username__icontains=search) |
                Q(email__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search)
            )
        return qs


@admin_session_required
def user_toggle_active(request, pk):
    user_obj = get_object_or_404(User, id=pk)
    user_obj.is_active = not user_obj.is_active
    user_obj.save()
    status = 'activated' if user_obj.is_active else 'deactivated'
    AuditLog.objects.create(
        user=request.user,
        action=f'User {status}',
        module='User',
        object_id=user_obj.id,
        details=f'User {user_obj.username} {status}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'User {user_obj.username} {status} successfully.')
    return redirect('admin_user_list')


@admin_session_required
def user_booking_history(request, pk):
    user_obj = get_object_or_404(User, id=pk)
    bookings = Booking.objects.filter(user=user_obj).select_related('movie', 'theater', 'seat').order_by('-booked_at')
    return render(request, 'admin/users/user_bookings.html', {
        'user_obj': user_obj,
        'bookings': bookings,
    })


@admin_session_required
def user_reset_password(request, pk):
    if request.method == 'POST':
        user_obj = get_object_or_404(User, id=pk)
        default_password = 'password123'
        user_obj.set_password(default_password)
        user_obj.save()
        AuditLog.objects.create(
            user=request.user,
            action='Password Reset',
            module='User',
            object_id=user_obj.id,
            details=f'Password reset for {user_obj.username}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, f'Password for {user_obj.username} reset to: {default_password}')
    return redirect('admin_user_list')


class StaffListView(AdminSessionMixin, ListView):
    model = AdminProfile
    template_name = 'admin/staff/staff_list.html'
    context_object_name = 'staff_members'
    paginate_by = 20

    def get_queryset(self):
        qs = AdminProfile.objects.select_related('user').all()
        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(Q(user__username__icontains=search) | Q(user__email__icontains=search))
        return qs


@admin_session_required
def staff_create(request):
    if request.method == 'POST':
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            password = form.cleaned_data.get('password')
            user.set_password(password)
            user.is_staff = True
            user.save()
            AdminProfile.objects.create(
                user=user,
                role='staff',
                department=request.POST.get('department', ''),
                phone=request.POST.get('phone', ''),
            )
            AuditLog.objects.create(
                user=request.user,
                action='Staff Created',
                module='Staff',
                object_id=user.id,
                details=f'Created staff: {user.username}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Staff member {user.username} created successfully.')
            return redirect('admin_staff_list')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = StaffCreateForm()
    return render(request, 'admin/staff/staff_form.html', {'form': form, 'is_create': True})


@admin_session_required
def staff_edit(request, pk):
    profile = get_object_or_404(AdminProfile, id=pk)
    user = profile.user
    if request.method == 'POST':
        user_form = StaffUpdateForm(request.POST, instance=user)
        profile_form = AdminProfileForm(request.POST, instance=profile)
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            AuditLog.objects.create(
                user=request.user,
                action='Staff Updated',
                module='Staff',
                object_id=user.id,
                details=f'Updated staff: {user.username}',
                ip_address=request.META.get('REMOTE_ADDR')
            )
            messages.success(request, f'Staff {user.username} updated successfully.')
            return redirect('admin_staff_list')
    else:
        user_form = StaffUpdateForm(instance=user)
        profile_form = AdminProfileForm(instance=profile)
    return render(request, 'admin/staff/staff_edit.html', {
        'user_form': user_form,
        'profile_form': profile_form,
        'staff_profile': profile,
    })


@admin_session_required
def staff_delete(request, pk):
    profile = get_object_or_404(AdminProfile, id=pk)
    user = profile.user
    AuditLog.objects.create(
        user=request.user,
        action='Staff Deleted',
        module='Staff',
        object_id=user.id,
        details=f'Deleted staff: {user.username}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    profile.delete()
    user.is_staff = False
    user.save()
    messages.success(request, f'Staff {user.username} removed successfully.')
    return redirect('admin_staff_list')


@admin_session_required
def staff_permissions(request, pk):
    profile = get_object_or_404(AdminProfile, id=pk)
    modules = ['Movie', 'Theatre', 'Screen', 'Show', 'Booking', 'User', 'Staff', 'Coupon', 'Notification', 'Review', 'Genre', 'Language', 'Cast']

    if request.method == 'POST':
        AdminPermission.objects.filter(admin_profile=profile).delete()
        for module in modules:
            can_view = request.POST.get(f'{module}_can_view') == 'on'
            can_create = request.POST.get(f'{module}_can_create') == 'on'
            can_edit = request.POST.get(f'{module}_can_edit') == 'on'
            can_delete = request.POST.get(f'{module}_can_delete') == 'on'
            AdminPermission.objects.create(
                admin_profile=profile,
                module=module.lower(),
                can_view=can_view,
                can_create=can_create,
                can_edit=can_edit,
                can_delete=can_delete,
            )
        AuditLog.objects.create(
            user=request.user,
            action='Permissions Updated',
            module='Staff',
            object_id=profile.user.id,
            details=f'Updated permissions for {profile.user.username}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, f'Permissions updated for {profile.user.username}.')
        return redirect('admin_staff_list')

    existing_perms = {p.module: p for p in AdminPermission.objects.filter(admin_profile=profile)}
    return render(request, 'admin/staff/staff_permissions.html', {
        'profile': profile,
        'modules': modules,
        'existing_perms': existing_perms,
    })


class CouponListView(AdminSessionMixin, ListView):
    model = Coupon
    template_name = 'admin/coupons/coupon_list.html'
    context_object_name = 'coupons'
    paginate_by = 20


class CouponCreateView(AdminSessionMixin, CreateView):
    model = Coupon
    form_class = CouponForm
    template_name = 'admin/coupons/coupon_form.html'
    success_url = reverse_lazy('admin_coupon_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Coupon created successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Coupon Created',
            module='Coupon',
            object_id=self.object.id,
            details=f'Created coupon: {self.object.code}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class CouponUpdateView(AdminSessionMixin, UpdateView):
    model = Coupon
    form_class = CouponForm
    template_name = 'admin/coupons/coupon_form.html'
    success_url = reverse_lazy('admin_coupon_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Coupon updated successfully.')
        AuditLog.objects.create(
            user=self.request.user,
            action='Coupon Updated',
            module='Coupon',
            object_id=self.object.id,
            details=f'Updated coupon: {self.object.code}',
            ip_address=self.request.META.get('REMOTE_ADDR')
        )
        return response


class CouponDeleteView(AdminSessionMixin, DeleteView):
    model = Coupon
    template_name = 'admin/coupons/coupon_confirm_delete.html'
    success_url = reverse_lazy('admin_coupon_list')

    def delete(self, request, *args, **kwargs):
        obj = self.get_object()
        AuditLog.objects.create(
            user=request.user,
            action='Coupon Deleted',
            module='Coupon',
            object_id=obj.id,
            details=f'Deleted coupon: {obj.code}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
        messages.success(request, 'Coupon deleted successfully.')
        return super().delete(request, *args, **kwargs)


class NotificationListView(AdminSessionMixin, ListView):
    model = Notification
    template_name = 'admin/notifications/notification_list.html'
    context_object_name = 'notifications'
    paginate_by = 20


@admin_session_required
def notification_create(request):
    if request.method == 'POST':
        form = NotificationForm(request.POST)
        if form.is_valid():
            notification = form.save(commit=False)
            send_to_all = request.POST.get('send_to_all')
            if send_to_all:
                for user in User.objects.filter(is_active=True):
                    Notification.objects.create(
                        user=user,
                        title=notification.title,
                        message=notification.message,
                        notification_type=notification.notification_type,
                        link=notification.link,
                    )
                AuditLog.objects.create(
                    user=request.user,
                    action='Notification Sent (All)',
                    module='Notification',
                    details=f'Sent notification to all users: {notification.title}',
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                messages.success(request, 'Notification sent to all users.')
            else:
                notification.user = request.POST.get('user_id') if request.POST.get('user_id') else None
                if notification.user:
                    notification.user = get_object_or_404(User, id=int(request.POST.get('user_id')))
                notification.save()
                AuditLog.objects.create(
                    user=request.user,
                    action='Notification Created',
                    module='Notification',
                    object_id=notification.id,
                    details=f'Created notification: {notification.title}',
                    ip_address=request.META.get('REMOTE_ADDR')
                )
                messages.success(request, 'Notification created successfully.')
            return redirect('admin_notification_list')
    else:
        form = NotificationForm()
    return render(request, 'admin/notifications/notification_form.html', {
        'form': form,
        'users': User.objects.filter(is_active=True),
    })


@admin_session_required
def notification_mark_read(request, pk):
    notification = get_object_or_404(Notification, id=pk)
    notification.is_read = True
    notification.save()
    return redirect('admin_notification_list')


@admin_session_required
def notification_delete(request, pk):
    notification = get_object_or_404(Notification, id=pk)
    AuditLog.objects.create(
        user=request.user,
        action='Notification Deleted',
        module='Notification',
        object_id=notification.id,
        details=f'Deleted notification: {notification.title}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    notification.delete()
    messages.success(request, 'Notification deleted.')
    return redirect('admin_notification_list')


class ReviewListView(AdminSessionMixin, ListView):
    model = Review
    template_name = 'admin/reviews/review_list.html'
    context_object_name = 'reviews'
    paginate_by = 20

    def get_queryset(self):
        qs = Review.objects.select_related('movie', 'user').all().order_by('-created_at')
        status = self.request.GET.get('status')
        movie_id = self.request.GET.get('movie')
        reported = self.request.GET.get('reported')
        hidden = self.request.GET.get('hidden')
        if status == 'approved':
            qs = qs.filter(is_approved=True)
        elif status == 'pending':
            qs = qs.filter(is_approved=False)
        if movie_id:
            qs = qs.filter(movie_id=movie_id)
        if reported == '1':
            qs = qs.filter(is_reported=True)
        if hidden == '1':
            qs = qs.filter(is_hidden=True)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from movies.models import Movie
        context['movies'] = Movie.objects.all()
        return context


@admin_session_required
def review_approve(request, pk):
    review = get_object_or_404(Review, id=pk)
    review.is_approved = not review.is_approved
    review.save()
    status = 'approved' if review.is_approved else 'unapproved'
    AuditLog.objects.create(
        user=request.user,
        action=f'Review {status}',
        module='Review',
        object_id=review.id,
        details=f'Review {status} for {review.movie.name} by {review.user.username}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, f'Review {status} successfully.')
    return redirect('admin_review_list')


@admin_session_required
def review_hide(request, pk):
    review = get_object_or_404(Review, id=pk)
    review.is_hidden = True
    review.save()
    AuditLog.objects.create(
        user=request.user,
        action='Review Hidden',
        module='Review',
        object_id=review.id,
        details=f'Hidden review for {review.movie.name} by {review.user.username}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, 'Review hidden from public view.')
    return redirect('admin_review_list')


@admin_session_required
def review_restore(request, pk):
    review = get_object_or_404(Review, id=pk)
    review.is_hidden = False
    review.save()
    AuditLog.objects.create(
        user=request.user,
        action='Review Restored',
        module='Review',
        object_id=review.id,
        details=f'Restored review for {review.movie.name} by {review.user.username}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    messages.success(request, 'Review restored to public view.')
    return redirect('admin_review_list')


@admin_session_required
def review_delete(request, pk):
    review = get_object_or_404(Review, id=pk)
    movie_name = review.movie.name
    username = review.user.username
    AuditLog.objects.create(
        user=request.user,
        action='Review Deleted',
        module='Review',
        object_id=review.id,
        details=f'Deleted review for {movie_name} by {username}',
        ip_address=request.META.get('REMOTE_ADDR')
    )
    review.delete()
    messages.success(request, 'Review deleted permanently.')
    return redirect('admin_review_list')




class AuditLogListView(AdminSessionMixin, ListView):
    model = AuditLog
    template_name = 'admin/audit_logs.html'
    context_object_name = 'logs'
    paginate_by = 30
    ordering = ['-created_at']

    def get_queryset(self):
        qs = AuditLog.objects.select_related('user').all().order_by('-created_at')
        search = self.request.GET.get('search')
        action = self.request.GET.get('action')
        module = self.request.GET.get('module')
        if search:
            qs = qs.filter(
                Q(action__icontains=search) |
                Q(user__username__icontains=search) |
                Q(module__icontains=search) |
                Q(details__icontains=search)
            )
        if action:
            qs = qs.filter(action__icontains=action)
        if module:
            qs = qs.filter(module__icontains=module)
        return qs


@admin_session_required
def get_notifications(request):
    count = Notification.objects.filter(is_read=False).count()
    return JsonResponse({'unread_count': count})


class SettingsView(AdminSessionMixin, TemplateView):
    template_name = 'admin/settings.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_movies'] = Movie.objects.count()
        context['total_theatres'] = Theater.objects.values('name').distinct().count()
        context['total_screens'] = Theater.objects.count()
        context['total_staff'] = AdminProfile.objects.count()
        return context


@admin_session_required
def profile_view(request):
    profile, _ = AdminProfile.objects.get_or_create(
        user=request.user,
        defaults={'role': 'admin'}
    )
    if request.method == 'POST':
        form = AdminProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('admin_profile')
    else:
        form = AdminProfileForm(instance=profile)
    return render(request, 'admin/profile.html', {
        'form': form,
        'profile': profile,
    })
