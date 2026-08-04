from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Count, Q, Avg
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.contrib import messages
from django.utils import timezone
from django.core.paginator import Paginator
from django.http import JsonResponse, HttpResponse, HttpResponseNotModified
from django.urls import reverse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import Movie, Theater, Seat, Booking, Reservation, ReservedSeat, Wishlist, RESERVATION_HOLD_SECONDS
from . import gateway, payments
from .payments import PaymentError
from .services import (
    ReservationError,
    cancel_booking,
    confirm_booking,
    create_reservation,
    expire_stale_for_show,
    get_pricing_config,
    gst_slabs,
    modify_reservation,
    release_reservation,
    release_expired_reservations,
    reservation_pricing,
    seat_data_for_show,
    seat_states_for_show,
)
from .notifications import send_booking_confirmation
from admin_panel.models import CastMember, Trailer, MovieImage, Review, Genre, Language, Show, Theatre, Screen, PaymentTransaction
import json
import hashlib
import secrets


def movie_list(request):
    search_query = request.GET.get('search')
    genre_slug = request.GET.get('genre')
    lang_code = request.GET.get('language')
    movies_qs = Movie.objects.filter(is_deleted=False).exclude(status__in=['archived', 'hidden'])
    if search_query:
        movies_qs = movies_qs.filter(name__icontains=search_query)
    if genre_slug:
        movies_qs = movies_qs.filter(genres__slug=genre_slug)
    if lang_code:
        movies_qs = movies_qs.filter(languages__code=lang_code)
    paginator = Paginator(movies_qs, 20)
    page_number = request.GET.get('page', 1)
    movies = paginator.get_page(page_number)
    active_movies = Movie.objects.filter(is_deleted=False).exclude(status__in=['archived', 'hidden'])
    genres = Genre.objects.filter(movies__in=active_movies).distinct()
    languages = Language.objects.filter(movies__in=active_movies).distinct()
    return render(request, 'movies/movie_list.html', {
        'movies': movies,
        'genres': genres,
        'languages': languages,
    })


def movie_detail(request, movie_id):
    movie = get_object_or_404(
        Movie.objects.prefetch_related('genres', 'languages', 'cast_members', 'gallery_images', 'trailers'),
        id=movie_id, is_deleted=False
    )
    if movie.status in ['archived', 'hidden']:
        from django.http import Http404
        raise Http404("Movie not available")
    cast_members = CastMember.objects.filter(movie=movie)
    gallery = MovieImage.objects.filter(movie=movie)
    trailers = Trailer.objects.filter(movie=movie)
    review_base = Review.objects.filter(movie=movie, is_approved=True, is_hidden=False).select_related('user')
    review_pages = Paginator(review_base, 10)
    page_num = request.GET.get('rpage', 1)
    reviews = review_pages.get_page(page_num)
    user_review = None
    has_booked_and_completed = False
    if request.user.is_authenticated:
        user_review = Review.objects.filter(movie=movie, user=request.user).first()
        user_bookings = Booking.objects.filter(movie=movie, user=request.user).select_related('theater')
        for b in user_bookings:
            duration_hours = (movie.duration or 180) / 60
            show_end = b.theater.time + timezone.timedelta(hours=duration_hours)
            if show_end < timezone.now():
                has_booked_and_completed = True
                break
    avg_rating = review_base.aggregate(Avg('rating'))['rating__avg']
    total_reviews = review_base.count()
    all_reviews = list(review_base)
    rating_dist = {i: 0 for i in range(1, 6)}
    for r in all_reviews:
        if r.rating in rating_dist:
            rating_dist[r.rating] += 1
    visible_filter = {'is_deleted': False}
    similar_movies = Movie.objects.filter(genres__in=movie.genres.all(), **visible_filter).exclude(id=movie.id).exclude(status__in=['archived', 'hidden']).distinct()[:6]
    trending_movies = Movie.objects.filter(**visible_filter).exclude(status__in=['archived', 'hidden']).annotate(booking_count=Count('booking')).exclude(id=movie.id).order_by('-booking_count')[:6]
    recently_released = Movie.objects.filter(status='now_showing', **visible_filter).exclude(id=movie.id).order_by('-release_date')[:6]
    theaters = Theater.objects.filter(movie=movie, status='active').order_by('time')
    shows = Show.objects.filter(movie=movie, status='active').select_related('theatre', 'screen').order_by('date', 'time')
    recent_ids = request.session.get('recently_viewed', [])
    recent_ids = [mid for mid in recent_ids if str(mid) != str(movie.id)]
    recent_ids.insert(0, movie.id)
    request.session['recently_viewed'] = recent_ids[:8]
    in_wishlist = request.user.is_authenticated and Wishlist.objects.filter(
        user=request.user, movie=movie
    ).exists()
    return render(request, 'movies/movie_detail.html', {
        'movie': movie,
        'cast_members': cast_members,
        'gallery': gallery,
        'trailers': trailers,
        'reviews': reviews,
        'user_review': user_review,
        'has_booked_and_completed': has_booked_and_completed,
        'avg_rating': round(avg_rating, 1) if avg_rating else None,
        'total_reviews': total_reviews,
        'rating_dist': rating_dist,
        'similar_movies': similar_movies,
        'trending_movies': trending_movies,
        'recently_released': recently_released,
        'theaters': theaters,
        'shows': shows,
        'in_wishlist': in_wishlist,
    })


def theater_list(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id, is_deleted=False)
    if movie.status in ['archived', 'hidden']:
        from django.http import Http404
        raise Http404("Movie not available")
    theater = Theater.objects.filter(movie=movie, status='active')
    return render(request, 'movies/theater_list.html', {'movie': movie, 'theaters': theater})


def _reservation_payload(reservation):
    seats = list(
        Reservation.objects.filter(pk=reservation.pk)
        .values_list('reserved_seats__seat_id', flat=True)
    )
    pricing = reservation_pricing(reservation)
    remaining = max(0, int((reservation.expires_at - timezone.now()).total_seconds()))
    return {
        'token': reservation.token,
        'status': reservation.status,
        'expires_at': reservation.expires_at.isoformat(),
        'remaining': remaining,
        'seats': seats,
        'prices': [{'seat_id': s['seat_id'], 'price': str(s['price'])} for s in pricing['seats']],
        'tiers': [{'seat_id': s['seat_id'], 'tier': s['tier']} for s in pricing['seats']],
        'subtotal': str(pricing['subtotal']),
        'platform_fee': str(pricing['platform_fee']),
        'misc_fee': str(pricing['misc_fee']),
        'gst_rate': str(pricing['gst_rate']),
        'gst': str(pricing['gst']),
        'discount': str(pricing['discount']),
        'total': str(pricing['total']),
        'hold_seconds': RESERVATION_HOLD_SECONDS,
    }


def _parse_request_params(request):
    body = {}
    try:
        body = json.loads(request.body.decode('utf-8') or b'{}')
    except (ValueError, UnicodeDecodeError):
        body = {}

    def param_list(key):
        if key in request.POST:
            return request.POST.getlist(key)
        value = body.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]

    def param(key, default=None):
        if key in request.POST:
            return request.POST.get(key)
        return body.get(key, default)

    return param, param_list


@login_required(login_url='/login/')
def book_seats(request, theater_id):
    show = get_object_or_404(Theater.objects.select_related('movie'), id=theater_id, status='active')
    seat_data = seat_data_for_show(show)
    config = get_pricing_config()
    reservation = None
    if request.user.is_authenticated:
        reservation = Reservation.objects.filter(
            user=request.user, show=show, status='active'
        ).first()
    active_reservation = None
    if reservation and reservation.expires_at > timezone.now():
        active_reservation = _reservation_payload(reservation)
    tier_prices = {}
    for item in seat_data:
        tier_prices.setdefault(item['tier'], str(item['price']))
    return render(request, 'movies/seat_selection.html', {
        'theaters': show,
        'seat_data': seat_data,
        'tier_prices': tier_prices,
        'show_data': {
            'id': show.id,
            'name': show.name,
            'movie': show.movie.name,
            'time': show.time.strftime('%I:%M %p, %A, %b %d'),
            'ticket_price': str(show.ticket_price),
            'prices': {str(item['id']): str(item['price']) for item in seat_data},
            'tiers': tier_prices,
            'platform_fee': str(config['platform_fee_per_ticket']),
            'misc_fee': str(config['misc_fee_per_booking']),
            'gst_slabs': gst_slabs(),
        },
        'reservation_data': active_reservation,
    })


@login_required(login_url='/login/')
def seat_status(request, theater_id):
    show = get_object_or_404(Theater, id=theater_id, status='active')
    expire_stale_for_show(show)
    revision = Theater.objects.get(pk=show.pk).seat_revision
    etag = f'"rev-{revision}"'
    if request.META.get('HTTP_IF_NONE_MATCH') == etag:
        return HttpResponseNotModified()
    states = seat_states_for_show(show)
    seat_rows = seat_data_for_show(show)
    prices = {
        str(item['id']): str(item['price'])
        for item in seat_rows
    }
    reservation = Reservation.objects.filter(
        user=request.user, show=show, status='active'
    ).first()
    payload = {
        'revision': revision,
        'seats': {str(k): v for k, v in states.items()},
        'prices': prices,
    }
    if reservation and reservation.expires_at > timezone.now():
        payload['reservation'] = _reservation_payload(reservation)
    else:
        payload['reservation'] = None
    response = JsonResponse(payload)
    response['ETag'] = etag
    return response


@login_required(login_url='/login/')
def reserve_seats_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    param, param_list = _parse_request_params(request)
    try:
        show_id = int(param('show_id'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid show.'}, status=400)
    try:
        reservation = create_reservation(request.user, show_id, param_list('seats'))
        return JsonResponse({'ok': True, 'reservation': _reservation_payload(reservation)})
    except ReservationError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=409)


@login_required(login_url='/login/')
def modify_reservation_view(request, token):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    param, param_list = _parse_request_params(request)
    try:
        reservation = modify_reservation(
            request.user,
            token,
            param_list('add'),
            param_list('remove'),
        )
        return JsonResponse({'ok': True, 'reservation': _reservation_payload(reservation)})
    except ReservationError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=409)


@login_required(login_url='/login/')
def release_reservation_view(request, token):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    try:
        release_reservation(request.user, token)
        return JsonResponse({'ok': True})
    except ReservationError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=409)


@login_required(login_url='/login/')
def payment_page(request, token):
    reservation = get_object_or_404(
        Reservation.objects.select_related('show', 'show__movie', 'user')
        .prefetch_related('reserved_seats__seat'),
        token=token,
    )
    if reservation.user_id != request.user.id:
        messages.error(request, 'This reservation does not belong to you.')
        return redirect('profile')
    if reservation.status == 'booked':
        messages.success(request, 'This reservation is already confirmed.')
        return redirect('profile')
    if reservation.status != 'active' or reservation.expires_at <= timezone.now():
        messages.error(request, 'Your reservation has expired. Please select your seats again.')
        return redirect('book_seats', theater_id=reservation.show_id)
    if reservation.show.time <= timezone.now():
        messages.error(request, 'This show has already started and cannot be booked.')
        return redirect('book_seats', theater_id=reservation.show_id)
    pricing = reservation_pricing(reservation)
    return render(request, 'movies/payment.html', {
        'reservation': reservation,
        'seats': reservation.reserved_seats.select_related('seat'),
        'pricing': pricing,
        'transaction_id': 'TXN-{}{}'.format(
            reservation.token[:10].upper(), secrets.token_hex(4).upper()
        ),
        'book_seats_url': reverse('book_seats', args=[reservation.show_id]),
        'razorpay_key': settings.RAZORPAY_KEY_ID,
        'demo_checkout': gateway.demo_mode(),
        'remaining': max(0, int((reservation.expires_at - timezone.now()).total_seconds())),
        'start_payment_url': reverse('payment_start', args=[token]),
        'verify_payment_url': reverse('payment_verify', args=[token]),
        'failed_payment_url': reverse('payment_failed', args=[token]),
    })


def _payment_failure_redirect(token):
    """Best-effort target for an unbookable reservation: reselect or profile."""
    show = Reservation.objects.filter(token=token).only('show_id').first()
    if show is not None:
        return reverse('book_seats', args=[show.show_id])
    return reverse('profile')


@login_required(login_url='/login/')
def payment_start_api(request, token):
    """Create (or reuse) the payment order for an active reservation."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    param, _ = _parse_request_params(request)
    try:
        _tx, checkout = payments.start_checkout(
            request.user, token, coupon_code=param('coupon_code', '')
        )
        return JsonResponse({'ok': True, 'checkout': checkout})
    except ReservationError as exc:
        return JsonResponse({
            'ok': False,
            'error': str(exc),
            'redirect': _payment_failure_redirect(token),
        }, status=409)
    except PaymentError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=502)


@login_required(login_url='/login/')
def payment_verify_api(request, token):
    """Verify a checkout callback server-side and confirm the booking."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    param, _ = _parse_request_params(request)
    try:
        _reservation, _bookings = payments.verify_and_confirm(
            request.user,
            token,
            gateway_order_id=param('razorpay_order_id', ''),
            gateway_payment_id=param('razorpay_payment_id', ''),
            gateway_signature=param('razorpay_signature', ''),
            method=param('payment_method', 'upi'),
            demo=str(param('demo', 'false')).lower() == 'true',
        )
        return JsonResponse({
            'ok': True,
            'confirmation_url': reverse('booking_confirmation', args=[token]),
        })
    except ReservationError as exc:
        return JsonResponse({
            'ok': False,
            'error': str(exc),
            'redirect': _payment_failure_redirect(token),
        }, status=409)
    except PaymentError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


@login_required(login_url='/login/')
def payment_failed_api(request, token):
    """Record a failed checkout attempt (seats stay held for a retry)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    param, _ = _parse_request_params(request)
    try:
        payments.record_failure(
            request.user,
            token,
            gateway_order_id=param('razorpay_order_id', ''),
            gateway_payment_id=param('razorpay_payment_id', ''),
            failure_reason=param('error', ''),
            method=param('payment_method', ''),
        )
        return JsonResponse({'ok': True})
    except (ReservationError, PaymentError) as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=409)


@csrf_exempt
def payment_webhook(request):
    """Razorpay webhook endpoint (signature verified, no CSRF)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    signature = request.headers.get('X-Razorpay-Signature', '')
    try:
        payments.handle_webhook(request.body, signature)
        return HttpResponse('ok', status=200)
    except PaymentError:
        return HttpResponse('invalid signature', status=400)
    except Exception:
        return HttpResponse('error', status=500)


@login_required(login_url='/login/')
def simulate_payment_view(request, token):
    """Demo checkout path, active only while the gateway is not configured.

    Runs the exact same signature-verified confirmation flow the Razorpay
    checkout uses, so the demo behaves like production. Never enabled when
    real gateway keys are present.
    """
    if not gateway.demo_mode():
        messages.error(request, 'Online payments are enabled — use the checkout.')
        return redirect('payment_page', token=token)
    if request.method != 'POST':
        return redirect('payment_page', token=token)
    reservation = get_object_or_404(
        Reservation.objects.select_related('show', 'show__movie'), token=token
    )
    if reservation.user_id != request.user.id:
        messages.error(request, 'This reservation does not belong to you.')
        return redirect('profile')
    if reservation.status == 'booked':
        return redirect('booking_confirmation', token=reservation.token)
    try:
        tx, _checkout = payments.start_checkout(
            request.user, token, coupon_code=request.POST.get('coupon_code')
        )
    except ReservationError as exc:
        messages.error(request, str(exc))
        return redirect(_payment_failure_redirect(token))
    except PaymentError as exc:
        messages.error(request, str(exc))
        return redirect('payment_page', token=token)

    action = request.POST.get('action', 'success')
    if action == 'fail':
        payments.record_failure(
            request.user,
            token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id='pay_DEMO_failed',
            failure_reason='Simulated payment failure',
            method=request.POST.get('payment_method') or 'upi',
        )
        messages.error(
            request,
            'Payment failed. Your seats are still held — you can retry below.',
        )
        return redirect('payment_page', token=token)

    gateway_payment_id = 'pay_DEMO{}'.format(secrets.token_hex(8).upper())
    gateway_signature = gateway.demo_signature(
        tx.gateway_order_id, gateway_payment_id
    )
    try:
        reservation, bookings = payments.verify_and_confirm(
            request.user,
            token,
            gateway_order_id=tx.gateway_order_id,
            gateway_payment_id=gateway_payment_id,
            gateway_signature=gateway_signature,
            method=request.POST.get('payment_method') or 'upi',
            demo=True,
        )
        send_booking_confirmation(request.user, reservation, bookings)
        messages.success(
            request, 'Payment successful! Your tickets are confirmed.'
        )
        return redirect('booking_confirmation', token=reservation.token)
    except ReservationError as exc:
        messages.error(request, str(exc))
        return redirect(_payment_failure_redirect(token))
    except PaymentError as exc:
        messages.error(request, str(exc))
        return redirect('payment_page', token=token)


@login_required(login_url='/login/')
def booking_confirmation(request, token):
    reservation = get_object_or_404(
        Reservation.objects.select_related('show', 'show__movie', 'user', 'coupon'),
        token=token,
    )
    if reservation.user_id != request.user.id:
        messages.error(request, 'This booking does not belong to you.')
        return redirect('profile')
    if reservation.status != 'booked':
        messages.error(request, 'This booking is not confirmed yet.')
        return redirect('profile')
    bookings = list(
        reservation.bookings.select_related('seat', 'payment').order_by('seat__seat_number')
    )
    payment_tx = (
        PaymentTransaction.objects.filter(reservation=reservation, status='captured')
        .order_by('-captured_at')
        .first()
    )
    return render(request, 'movies/booking_confirmation.html', {
        'reservation': reservation,
        'bookings': bookings,
        'payment_tx': payment_tx,
    })


@login_required(login_url='/login/')
def download_ticket(request, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related('user', 'movie', 'theater', 'seat', 'reservation', 'payment'),
        id=booking_id,
    )
    if booking.user_id != request.user.id:
        messages.error(request, 'This ticket does not belong to you.')
        return redirect('profile')
    seed = (booking.booking_ref + booking.seat.seat_number).encode()
    digest = hashlib.sha256(seed).digest()
    width = 16
    rows = []
    for r in range(width):
        line = ''.join('\u2588' if digest[(r * width + c) % 16] & (1 << (c % 8)) else ' ' for c in range(width))
        rows.append(line + '  ' + line)
    return render(request, 'movies/ticket.html', {'booking': booking, 'qr_pattern': '\n'.join(rows)})


@login_required(login_url='/login/')
def cancel_booking_view(request, booking_id):
    if request.method != 'POST':
        return redirect('profile')
    try:
        cancel_booking(request.user, booking_id)
        messages.success(request, 'Booking cancelled and your payment has been refunded.')
    except ReservationError as exc:
        messages.error(request, str(exc))
    return redirect('profile')


@login_required(login_url='/login/')
def coupon_validate(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'GET required.'}, status=405)
    code = request.GET.get('code', '').strip()
    token = request.GET.get('token', '')
    reservation = Reservation.objects.filter(
        token=token, user=request.user, status='active'
    ).select_related('show').first()
    if not reservation:
        return JsonResponse({'ok': False, 'error': 'Active reservation not found.'}, status=404)
    from .services import discount_for, reservation_pricing, validate_coupon

    try:
        pricing = reservation_pricing(reservation, coupon_code=code)
        coupon = pricing.get('coupon')
        if not coupon:
            raise ReservationError('This coupon code is invalid or inactive.')
        return JsonResponse({
            'ok': True,
            'coupon_code': coupon.code,
            'discount': str(pricing['discount']),
            'total': str(pricing['total']),
        })
    except ReservationError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


@login_required(login_url='/login/')
def cleanup_expired_reservations_view(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse({'error': 'Forbidden.'}, status=403)
    count = release_expired_reservations()
    return JsonResponse({'ok': True, 'released': count})


@login_required(login_url='/login/')
@require_POST
def report_review(request, review_id):
    review = get_object_or_404(Review, id=review_id)
    if review.user_id == request.user.id:
        messages.error(request, 'You cannot report your own review.')
        return redirect('movie_detail', movie_id=review.movie.id)
    if not review.is_reported:
        review.is_reported = True
        review.save(update_fields=['is_reported'])
    messages.success(request, 'Review has been reported for moderation.')
    return redirect('movie_detail', movie_id=review.movie.id)


def custom_404(request, exception):
    return render(request, '404.html', status=404)


def custom_403(request, exception):
    return render(request, '403.html', status=403)


def custom_500(request):
    return render(request, '500.html', status=500)


@login_required(login_url='/login/')
def submit_review(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id, is_deleted=False)
    if request.method == 'POST':
        rating = request.POST.get('rating')
        comment = request.POST.get('comment', '').strip()
        if not rating or not rating.isdigit() or int(rating) < 1 or int(rating) > 5:
            messages.error(request, 'Please select a valid rating (1-5).')
            return redirect('movie_detail', movie_id=movie.id)
        if not comment:
            messages.error(request, 'Please write a review comment.')
            return redirect('movie_detail', movie_id=movie.id)
        eligible_booking = None
        user_bookings = Booking.objects.filter(movie=movie, user=request.user).select_related('theater')
        for b in user_bookings:
            duration_hours = (movie.duration or 180) / 60
            show_end = b.theater.time + timezone.timedelta(hours=duration_hours)
            if show_end < timezone.now():
                eligible_booking = b
                break
        existing = Review.objects.filter(movie=movie, user=request.user).first()
        if existing:
            existing.rating = int(rating)
            existing.comment = comment
            if eligible_booking and not existing.booking:
                existing.booking = eligible_booking
            existing.edited_at = timezone.now()
            existing.save()
            messages.success(request, 'Your review has been updated.')
        else:
            if not eligible_booking:
                messages.error(request, 'You can only review movies you have watched. Book a ticket and watch the show first.')
                return redirect('movie_detail', movie_id=movie.id)
            Review.objects.create(
                movie=movie,
                user=request.user,
                booking=eligible_booking,
                rating=int(rating),
                comment=comment,
            )
            messages.success(request, 'Your review has been submitted for approval.')
    return redirect('movie_detail', movie_id=movie.id)




