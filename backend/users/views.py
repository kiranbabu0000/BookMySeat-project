from datetime import timedelta

from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from .forms import UserRegisterForm, UserUpdateForm
from .models import NameChange
from django.shortcuts import render,redirect,get_object_or_404
from django.contrib.auth import login, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from django.urls import reverse
from movies.models import Movie, Booking, Reservation, Wishlist, TicketDownload
from movies.discovery import trending_movies, recently_released, recommended_for_user, _min_price_subquery
from admin_panel.models import AdminProfile, PaymentTransaction, Notification, Genre
from bookmyseat.ratelimit import is_locked_out, login_failed, login_succeeded
from .otp import (
    can_resend,
    generate_and_store,
    mark_resend,
    mask_email,
    remaining_attempts,
    send_otp_email,
    verify as verify_otp,
)

def home(request):
    movies = Movie.objects.filter(
        status='now_showing',
        show_on_homepage=True,
        is_deleted=False,
        category='movie',
    ).annotate(min_price=_min_price_subquery())
    laughing_therapy = Movie.objects.filter(
        status='now_showing',
        show_on_homepage=True,
        is_deleted=False,
        category='laughing_therapy',
    ).annotate(min_price=_min_price_subquery())
    live_concerts = Movie.objects.filter(
        status='now_showing',
        show_on_homepage=True,
        is_deleted=False,
        category='live_concert',
    ).annotate(min_price=_min_price_subquery())
    recent_ids = request.session.get('recently_viewed', [])
    recent_movies = []
    if recent_ids:
        recent_movies = list(
            Movie.objects.filter(id__in=recent_ids, is_deleted=False)
            .exclude(status__in=['archived', 'hidden'])
            .annotate(min_price=_min_price_subquery())
        )
        ordered = {mid: i for i, mid in enumerate(recent_ids)}
        recent_movies.sort(key=lambda m: ordered.get(m.id, len(ordered)))
    top_rated = list(
        Movie.objects.filter(is_deleted=False, category='movie')
        .exclude(status__in=['archived', 'hidden'])
        .annotate(min_price=_min_price_subquery())
        .order_by('-rating', '-release_date')[:8]
    )
    coming_soon = list(
        Movie.objects.filter(status='coming_soon', is_deleted=False, category='movie')
        .order_by('release_date')[:8]
    )
    visible_movie_ids = Movie.objects.filter(is_deleted=False).exclude(
        status__in=['archived', 'hidden']
    )
    genres = list(
        Genre.objects.filter(movies__in=visible_movie_ids)
        .distinct().order_by('name')
    )
    # Event pseudo-genres (Laughing Therapy / Live Concert) must link to their
    # category tabs, not to a genre filter on the Movies tab (which is empty).
    genre_links = []
    for genre in genres:
        category = genre.slug.replace('-', '_')
        if category in ('laughing_therapy', 'live_concert'):
            href = '{}?category={}'.format(reverse('movie_list'), category)
        else:
            href = '{}?genre={}'.format(reverse('movie_list'), genre.slug)
        genre_links.append({'name': genre.name, 'slug': genre.slug, 'href': href})
    return render(request, 'home.html', {
        'movies': movies,
        'laughing_therapy': laughing_therapy,
        'live_concerts': live_concerts,
        'recently_viewed': recent_movies,
        'trending': trending_movies(8),
        'recently_released': recently_released(8),
        'recommended': recommended_for_user(request, 8) if request.user.is_authenticated else [],
        'top_rated': top_rated,
        'coming_soon': coming_soon,
        'genres': genre_links,
    })

def register(request):
    if request.method == 'POST':
        form=UserRegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False
            user.save()
            otp = generate_and_store(user)
            if send_otp_email(user, otp):
                request.session['otp_user_id'] = user.id
                request.session['otp_purpose'] = 'register'
                messages.info(request, 'Verify your email: a one-time code was sent to {}.'.format(mask_email(user.email)))
                return redirect('register_otp')
            user.delete()
            messages.error(request, 'We could not send the verification code to your email. Please try again.')
    else:
        form=UserRegisterForm()
    return render(request,'users/register.html',{'form':form})

def login_view(request):
    next_url = request.POST.get('next') or request.GET.get('next') or '/'
    if not next_url.startswith('/') or next_url.startswith('//'):
        next_url = '/'
    if request.method == 'POST':
        form=AuthenticationForm(request,data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username', '')
            if is_locked_out('user', request, username):
                messages.error(request, 'Too many failed attempts. Please wait a few minutes and try again.')
                return render(request,'users/login.html',{'form':form, 'next': next_url})
            user=form.get_user()
            if user.is_staff or user.is_superuser or AdminProfile.objects.filter(user=user, is_active=True).exists():
                messages.error(request, 'This is an admin account. Please sign in through the admin portal.')
                return render(request,'users/login.html',{'form':form, 'next': next_url})
            login_succeeded('user', request, username)
            login(request,user)
            request.session.pop('just_logged_out', None)
            return redirect(next_url)
        else:
            username = request.POST.get('username', '')
            login_failed('user', request, username)
            if is_locked_out('user', request, username):
                messages.error(request, 'Too many failed attempts. Please wait a few minutes and try again.')
    else:
        form=AuthenticationForm()
    return render(request,'users/login.html',{'form':form, 'next': next_url})


def _register_pending_user(request):
    """Return the inactive user awaiting email-OTP verification, or None."""
    user_id = request.session.get('otp_user_id')
    if not user_id or request.session.get('otp_purpose') != 'register':
        return None
    try:
        user = User.objects.get(pk=user_id, is_active=False)
    except User.DoesNotExist:
        return None
    return user


def register_otp(request):
    user = _register_pending_user(request)
    if user is None:
        request.session.pop('otp_user_id', None)
        request.session.pop('otp_purpose', None)
        return redirect('register')

    if request.method == 'POST':
        ok, msg = verify_otp(user.id, request.POST.get('otp', ''))
        if not ok:
            messages.error(request, msg)
            if 'expired' in msg or 'Too many' in msg:
                request.session.pop('otp_user_id', None)
                request.session.pop('otp_purpose', None)
                return redirect('register')
            return render(request, 'users/register_otp.html', {
                'email': mask_email(user.email),
                'remaining': remaining_attempts(user.id),
            })
        user.is_active = True
        user.save(update_fields=['is_active'])
        request.session.pop('otp_user_id', None)
        request.session.pop('otp_purpose', None)
        login(request, user)
        return redirect('profile')

    return render(request, 'users/register_otp.html', {
        'email': mask_email(user.email),
        'remaining': remaining_attempts(user.id),
    })


def register_otp_resend(request):
    if request.method != 'POST':
        return redirect('register_otp')
    user = _register_pending_user(request)
    if user is None:
        return redirect('register')
    if not can_resend(user.id):
        messages.error(request, 'Please wait a moment before requesting another code.')
        return redirect('register_otp')
    otp = generate_and_store(user)
    mark_resend(user.id)
    if send_otp_email(user, otp):
        messages.info(request, 'A new verification code was sent to {}.'.format(mask_email(user.email)))
    else:
        messages.error(request, 'We could not send a new code. Please try again shortly.')
    return redirect('register_otp')

@login_required
def profile(request):
    status_filter = request.GET.get('filter', '')
    now = timezone.now()
    # History retention: bookings older than 6 months roll off the list.
    history_cutoff = now - timedelta(days=182)
    bookings_qs = Booking.objects.filter(user=request.user).select_related(
        'movie', 'theater', 'seat', 'reservation', 'payment'
    ).prefetch_related('movie__languages').filter(theater__time__gte=history_cutoff)
    if status_filter == 'current':
        bookings_qs = bookings_qs.filter(theater__time__gte=now)
    elif status_filter in ('history', 'past'):
        bookings_qs = bookings_qs.filter(theater__time__lt=now)
    bookings = list(bookings_qs.order_by('-booked_at'))
    for booking in bookings:
        booking.cancel_allowed = booking.theater.time > now
        duration_min = booking.movie.duration or 180
        booking.show_end = booking.theater.time + timedelta(minutes=duration_min)
        booking.is_on_now = booking.theater.time <= now < booking.show_end
        booking.is_current = booking.show_end > now
        booking.is_history = booking.show_end <= now
        if booking.is_on_now:
            booking.status_display = 'On Now'
        elif booking.is_current:
            booking.status_display = 'Upcoming'
        else:
            booking.status_display = 'History'

    # Group tickets purchased together (same reservation) into a single booking;
    # separate purchases stay as their own entries stacked below.
    booking_groups = []
    group_index = {}
    for booking in bookings:
        key = booking.reservation_id or booking.id
        if key in group_index:
            group_index[key]['seats'].append(booking)
        else:
            group = {'primary': booking, 'seats': [booking]}
            group_index[key] = group
            booking_groups.append(group)

    for group in booking_groups:
        group['seats'].sort(key=lambda b: b.seat.seat_number)
        group['seat_label'] = ', '.join(b.seat.seat_number for b in group['seats'])
        group['total'] = sum((b.total or 0) for b in group['seats'])
        group['any_confirmed'] = any(b.status == 'confirmed' for b in group['seats'])
        group['any_cancelled'] = any(b.status == 'cancelled' for b in group['seats'])
        reservation = group['primary'].reservation
        group['reservation'] = reservation
        if reservation and reservation.booking_ref:
            group['booking_ref'] = reservation.booking_ref
            group['ticket_count'] = reservation.ticket_count or len(group['seats'])
            group['cancelled'] = reservation.status == 'cancelled'
            group['scanned_at'] = reservation.scanned_at
            group['scan_count'] = reservation.scan_count
        else:
            group['booking_ref'] = group['primary'].booking_ref
            group['ticket_count'] = len(group['seats'])
            group['cancelled'] = group['any_cancelled'] and not group['any_confirmed']
            group['scanned_at'] = group['primary'].scanned_at
            group['scan_count'] = group['primary'].scan_count
        group['scanned'] = bool(group.get('scanned_at'))
        group['cancel_allowed'] = all(
            b.cancel_allowed for b in group['seats'] if b.status == 'confirmed'
        ) and not group['cancelled']
        movie = group['primary'].movie
        group['poster_url'] = movie.image.url if movie.image else None
        group['language_label'] = ', '.join(
            m.name for m in movie.languages.all()[:3]
        )
        ticket_url = request.build_absolute_uri(
            reverse('download_ticket', args=[group['booking_ref']])
        )
        group['wa_text'] = (
            '🎬 {movie}\n'
            '📍 {theatre}\n'
            '🕒 {time}\n'
            '🎟 {count} Tickets · Seats: {seats}\n\n'
            'View Ticket: {url}'
        ).format(
            movie=movie.name,
            theatre=group['primary'].theater.name,
            time=group['primary'].theater.time.strftime('%d %b %Y, %I:%M %p'),
            count=group['ticket_count'],
            seats=group['seat_label'],
            url=ticket_url,
        )

    for group in booking_groups:
        if any(b.is_on_now for b in group['seats']):
            group['status_display'] = 'On Now'
        elif any(b.is_current for b in group['seats']):
            group['status_display'] = 'Upcoming'
        else:
            group['status_display'] = 'History'
        group['is_current'] = group['status_display'] in ('Upcoming', 'On Now')

    if status_filter == 'current':
        booking_groups = [g for g in booking_groups if g.get('is_current')]
    elif status_filter in ('history', 'past'):
        booking_groups = [g for g in booking_groups if not g.get('is_current')]

    transactions = PaymentTransaction.objects.filter(user=request.user).select_related(
        'reservation', 'reservation__show', 'reservation__show__movie'
    )[:50]
    active_reservations = Reservation.objects.filter(
        user=request.user,
        status='active',
        expires_at__gt=now,
    ).select_related('show', 'show__movie').prefetch_related('reserved_seats__seat').order_by('expires_at')
    for reservation in active_reservations:
        reservation.remaining_seconds = max(
            0, int((reservation.expires_at - now).total_seconds())
        )
    if request.method == 'POST':
        u_form = UserUpdateForm(request.POST, instance=request.user)
        if u_form.is_valid():
            old_first = request.user.first_name
            old_last = request.user.last_name
            name_changed = (old_first, old_last) != (
                u_form.cleaned_data.get('first_name') or '',
                u_form.cleaned_data.get('last_name') or '',
            )
            if name_changed:
                day_ago = now - timedelta(hours=24)
                if NameChange.objects.filter(
                    user=request.user, changed_at__gte=day_ago
                ).count() >= 3:
                    messages.error(
                        request,
                        'You can only change your name 3 times per day. Please try again later.',
                    )
                    return redirect('profile')
            u_form.save()
            if name_changed:
                NameChange.objects.create(
                    user=request.user,
                    old_first=old_first,
                    old_last=old_last,
                    new_first=request.user.first_name,
                    new_last=request.user.last_name,
                )
            messages.success(request, 'Your profile has been updated.')
            return redirect('profile')
    else:
        u_form = UserUpdateForm(instance=request.user)

    wishlist_movies = Movie.objects.filter(wishlisted_by__user=request.user).select_related()
    unread_notifications = Notification.objects.filter(user=request.user, is_read=False).count()
    ticket_downloads = TicketDownload.objects.filter(user=request.user)[:5]

    return render(request, 'users/profile.html', {
        'u_form': u_form,
        'bookings': bookings,
        'booking_groups': booking_groups,
        'transactions': transactions,
        'active_reservations': active_reservations,
        'status_filter': status_filter,
        'wishlist_movies': wishlist_movies,
        'unread_notifications': unread_notifications,
        'ticket_downloads': ticket_downloads,
    })

@login_required
def toggle_wishlist(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    entry = Wishlist.objects.filter(user=request.user, movie=movie)
    if entry.exists():
        entry.delete()
        in_wishlist = False
    else:
        Wishlist.objects.get_or_create(user=request.user, movie=movie)
        in_wishlist = True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'in_wishlist': in_wishlist})
    messages.success(request, 'Added to your wishlist.' if in_wishlist else 'Removed from your wishlist.')
    return redirect('movie_detail', movie_id=movie.id)

@login_required
def wishlist(request):
    movies = Movie.objects.filter(wishlisted_by__user=request.user).select_related()
    return render(request, 'users/wishlist.html', {'wishlist_movies': movies})

@login_required
def my_notifications(request):
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
    unread_ids = list(notifications.filter(is_read=False).values_list('id', flat=True))
    if unread_ids and request.method == 'POST':
        Notification.objects.filter(id__in=unread_ids, user=request.user).update(is_read=True)
        return redirect('my_notifications')
    return render(request, 'users/notifications.html', {'notifications': notifications})

@login_required
def mark_notification_read(request, pk):
    notification = get_object_or_404(Notification, id=pk, user=request.user)
    if request.method == 'POST':
        notification.is_read = True
        notification.save()
        return redirect('my_notifications')
    return redirect('my_notifications')

@login_required
def reset_password(request):
    if request.method == 'POST':
        form=PasswordChangeForm(user=request.user,data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Your password has been changed successfully.')
            return redirect('profile')
    else:
        form=PasswordChangeForm(user=request.user)
    return render(request,'users/reset_password.html',{'form':form})

def user_logout_view(request):
    if request.method != 'POST':
        return render(request, 'users/logout.html')
    request.session.cycle_key()
    for key in ('_auth_user_id', '_auth_user_backend', '_auth_user_hash'):
        request.session.pop(key, None)
    request.session['just_logged_out'] = timezone.now().isoformat()
    response = redirect('home')
    response.delete_cookie('csrftoken')
    return response
