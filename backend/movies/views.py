from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.db.models import Q, Avg, Count
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
from .models import Movie, Theater, Seat, Booking, Reservation, ReservedSeat, Wishlist, TicketDownload, RESERVATION_HOLD_SECONDS
from . import gateway, payments
from .payments import PaymentError
from .services import (
    ReservationError,
    cancel_booking,
    cancel_reservation_booking,
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
    seat_layout_for_show,
    seat_states_for_show,
)
from .notifications import send_booking_confirmation
from admin_panel.models import Review, ReviewHelpful, Show, PaymentTransaction, AuditLog
from admin_panel.services import ensure_movie_schedule, SCHEDULE_HORIZON_DAYS
from .qr import build_qr_payload, ticket_qr_data_uri
from .ticket_scan import scan_ticket, resolve_ticket_target as _resolve_ticket_target
from .showtime import (
    day_range_utc,
    show_bookable,
    show_status_info,
    to_local,
)
import json
import secrets
from datetime import date as date_type, timedelta
from urllib.parse import quote


def movie_list(request):
    from . import discovery

    params = discovery.DiscoveryParams.from_request(request)
    if not params.city:
        cookie_city = (request.COOKIES.get('bms_city') or '').strip()
        if cookie_city in discovery.available_cities():
            params.city = cookie_city
    qs = discovery.discover_movies(params)
    paginator = Paginator(qs, params.per_page)
    page = paginator.get_page(params.page)
    page_range = paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1)
    qs_base = discovery.querystring(params)
    prev_page = page.number - 1 if page.has_previous() else 1
    next_page = page.number + 1 if page.has_next() else page.number

    context = {
        'movies': page,
        'paginator': paginator,
        'page_obj': page,
        'page_range': page_range,
        'prev_page': prev_page,
        'next_page': next_page,
        'total': paginator.count,
        'qs_base': qs_base,
        'params': params,
        'category_options': [
            (value, label)
            for value, label in discovery.CATEGORY_LABELS.items()
        ],
        'category_links': discovery.category_links(params),
        'results_label': discovery.CATEGORY_SINGULAR.get(params.category, 'Movie'),
        'results_label_plural': discovery.CATEGORY_LABELS.get(params.category, 'Movies'),
    }

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        html = render_to_string('movies/_movie_results.html', context, request=request)
        return JsonResponse({
            'ok': True,
            'html': html,
            'count': paginator.count,
            'page': page.number,
            'pages': paginator.num_pages,
            'has_prev': page.has_previous(),
            'has_next': page.has_next(),
        })

    facets = discovery.facet_data(params.category)
    context.update({
        'genres': facets['genres'],
        'languages': facets['languages'],
        'cities': facets['cities'],
        'theatres': facets['theatres'],
        'chips': discovery.chip_data(params, facets),
        'recommended': discovery.recommended_for_user(request, 8, params.category) if request.user.is_authenticated else [],
    })
    return render(request, 'movies/movie_list.html', context)


def movie_detail(request, movie_id):
    movie = get_object_or_404(
        Movie.objects.prefetch_related('genres', 'languages', 'cast_members', 'gallery_images', 'trailers'),
        id=movie_id, is_deleted=False
    )
    if movie.status in ['archived', 'hidden']:
        from django.http import Http404
        raise Http404("Movie not available")
    cast_members = movie.cast_members.all()
    gallery = movie.gallery_images.all()
    trailers = movie.trailers.all()
    review_base = Review.objects.filter(movie=movie, is_approved=True, is_hidden=False).select_related('user')
    review_pages = Paginator(review_base.annotate(helpful_count=Count('helpful_votes')).order_by('-created_at'), 10)
    page_num = request.GET.get('rpage', 1)
    reviews = review_pages.get_page(page_num)
    verified_reviews = Review.objects.filter(
        movie=movie, is_approved=True, is_hidden=False, booking__isnull=False
    ).select_related('user').annotate(
        helpful_count=Count('helpful_votes')
    ).order_by('-rating', '-created_at')[:5]
    user_review = None
    user_helpful_ids = set()
    has_booked_and_completed = False
    if request.user.is_authenticated:
        user_review = Review.objects.filter(movie=movie, user=request.user).first()
        user_helpful_ids = set(
            ReviewHelpful.objects.filter(user=request.user, review__movie=movie)
            .values_list('review_id', flat=True)
        )
        user_bookings = Booking.objects.filter(movie=movie, user=request.user).select_related('theater')
        for b in user_bookings:
            duration_hours = (movie.duration or 180) / 60
            show_end = b.theater.time + timezone.timedelta(hours=duration_hours)
            if show_end < timezone.now():
                has_booked_and_completed = True
                break
    avg_rating = review_base.aggregate(Avg('rating'))['rating__avg']
    total_reviews = review_base.count()
    rating_dist = {i: 0 for i in range(1, 6)}
    for rating, count in review_base.values('rating').annotate(count=Count('rating')):
        if rating in rating_dist:
            rating_dist[rating] = count
    from . import discovery
    similar_movies = discovery.similar_movies(movie, 6)
    trending_movies = discovery.trending_movies(6, movie.category)
    recently_released = discovery.recently_released(6, movie.category)
    theaters = Theater.objects.filter(movie=movie, status='active').order_by('time')
    shows = Show.objects.filter(movie=movie, status='active').select_related('theatre', 'screen').order_by('date', 'time')
    recent_ids = request.session.get('recently_viewed', [])
    recent_ids = [mid for mid in recent_ids if str(mid) != str(movie.id)]
    recent_ids.insert(0, movie.id)
    request.session['recently_viewed'] = recent_ids[:8]
    in_wishlist = request.user.is_authenticated and Wishlist.objects.filter(
        user=request.user, movie=movie
    ).exists()
    crew = []
    if movie.director:
        crew.append({'role': 'Director', 'name': movie.director})
    if movie.producer:
        crew.append({'role': 'Producer', 'name': movie.producer})
    if movie.writer:
        crew.append({'role': 'Writer', 'name': movie.writer})
    if movie.music_director:
        crew.append({'role': 'Music Director', 'name': movie.music_director})
    if movie.cinematographer:
        crew.append({'role': 'Cinematographer', 'name': movie.cinematographer})
    if movie.production_company:
        crew.append({'role': 'Production', 'name': movie.production_company})
    return render(request, 'movies/movie_detail.html', {
        'movie': movie,
        'cast_members': cast_members,
        'gallery': gallery,
        'trailers': trailers,
        'crew': crew,
        'reviews': reviews,
        'verified_reviews': verified_reviews,
        'user_helpful_ids': user_helpful_ids,
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


def _attach_occupancy(theaters, now=None):
    """Annotate each Theater with occupancy_pct / availability_level / availability_label.

    Levels: low (<70% occupied), medium (70-89%), high (>=90% - almost full).
    Occupied = permanently booked seats + seats held by active reservations.
    """
    now = now or timezone.now()
    theaters = list(theaters)
    ids = [t.id for t in theaters]
    if not ids:
        return theaters
    totals = dict(
        Seat.objects.filter(theater_id__in=ids)
        .values('theater_id')
        .annotate(n=Count('id'))
        .values_list('theater_id', 'n')
    )
    booked = dict(
        Seat.objects.filter(theater_id__in=ids, is_booked=True)
        .values('theater_id')
        .annotate(n=Count('id'))
        .values_list('theater_id', 'n')
    )
    held = dict(
        ReservedSeat.objects.filter(
            reservation__show_id__in=ids,
            reservation__status='active',
            reservation__expires_at__gt=now,
        )
        .values('reservation__show_id')
        .annotate(n=Count('id'))
        .values_list('reservation__show_id', 'n')
    )
    for t in theaters:
        total = totals.get(t.id) or 0
        used = (booked.get(t.id) or 0) + (held.get(t.id) or 0)
        available = max(0, total - used)
        pct = round(used * 100.0 / total, 1) if total else 0
        t.occupancy_pct = pct
        t.available_count = available
        t.availability_level = 'high' if pct >= 90 else ('medium' if pct >= 70 else 'low')
        if pct >= 90:
            t.availability_label = 'Almost full'
        elif pct >= 70:
            t.availability_label = f'{available} seats left'
        else:
            t.availability_label = ''
        t.show_status = show_status_info(t, now)
    return theaters


def theater_list(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id, is_deleted=False)
    if movie.status in ['archived', 'hidden']:
        from django.http import Http404
        raise Http404("Movie not available")

    today = timezone.localdate()
    show_dates = [today + timedelta(days=i) for i in range(SCHEDULE_HORIZON_DAYS)]
    # Lazily roll the schedule forward so the rolling date tabs always have
    # shows: if the last tab has no theaters yet, re-apply the movie's daily
    # slate to the freshly appearing days. Skipped under the test runner.
    if not getattr(settings, 'TESTING', False) and not Theater.objects.filter(
        movie=movie, status='active', time__range=day_range_utc(show_dates[-1])
    ).exists():
        ensure_movie_schedule(movie, SCHEDULE_HORIZON_DAYS)
    selected_date = today
    raw_date = request.GET.get('date')
    if raw_date:
        try:
            parsed = date_type.fromisoformat(raw_date)
            if parsed in show_dates:
                selected_date = parsed
        except ValueError:
            pass

    theaters = (
        Theater.objects.filter(
            movie=movie, status='active', time__range=day_range_utc(selected_date)
        )
        .select_related('admin_show__theatre')
    )
    city = (request.GET.get('city') or request.COOKIES.get('bms_city') or '').strip()
    if city:
        theaters = theaters.filter(admin_show__theatre__city__iexact=city)
    theaters = theaters.order_by('name', 'screen_name', 'time')
    theaters = _attach_occupancy(theaters)
    if selected_date == today:
        # Keep late-entry shows visible (with a warning) but never EXPIRED ones.
        theaters = [t for t in theaters if show_bookable(t)]

    date_tabs = [
        {
            'iso': d.isoformat(),
            'label': 'Today' if d == today else d.strftime('%a'),
            'day': d.day,
            'month': d.strftime('%b'),
            'is_today': d == today,
            'is_selected': d == selected_date,
        }
        for d in show_dates
    ]

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        html = render_to_string(
            'movies/_theater_list_results.html',
            {'movie': movie, 'theaters': theaters, 'selected_date': selected_date, 'city': city},
            request=request,
        )
        return JsonResponse({'html': html, 'count': len(theaters)})

    return render(request, 'movies/theater_list.html', {
        'movie': movie,
        'theaters': theaters,
        'show_dates': date_tabs,
        'selected_date': selected_date,
        'city': city,
    })


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
        'ticket_count': reservation.ticket_count or len(seats),
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
    show = get_object_or_404(
        Theater.objects.select_related('movie', 'admin_show__theatre'),
        id=theater_id, status='active',
    )
    if not show_bookable(show):
        messages.error(
            request,
            'This show is no longer available for booking — its late-entry '
            'window has closed.',
        )
        return redirect('theater_list', movie_id=show.movie_id)
    from .services import MAX_TICKET_COUNT

    raw_tickets = request.GET.get('tickets') or request.GET.get('ticket_count')
    ticket_count = None
    if raw_tickets:
        try:
            ticket_count = int(raw_tickets)
        except (TypeError, ValueError):
            ticket_count = None
        if ticket_count is not None and not (1 <= ticket_count <= MAX_TICKET_COUNT):
            ticket_count = None
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
        ticket_count = ticket_count or reservation.ticket_count or len(
            active_reservation['seats']
        )
    tier_prices = {}
    for item in seat_data:
        tier_prices.setdefault(item['tier'], str(item['price']))
    layout = seat_layout_for_show(show)
    section_starts = {
        section['start_row']: section
        for section in layout.get('sections', [])
    }
    for item in seat_data:
        if item['row_idx'] in section_starts:
            item['section_start'] = section_starts[item['row_idx']]
    num_to_id = {item['number']: item['id'] for item in seat_data}
    couple_pairs = [
        [num_to_id[a], num_to_id[b]]
        for a, b in layout.get('couple_pairs', [])
        if a in num_to_id and b in num_to_id
    ]
    return render(request, 'movies/seat_selection.html', {
        'theaters': show,
        'seat_data': seat_data,
        'tier_prices': tier_prices,
        'layout': layout,
        'max_tickets': MAX_TICKET_COUNT,
        'ticket_count': ticket_count or 1,
        'show_status': show_status_info(show),
        'show_data': {
            'id': show.id,
            'name': show.name,
            'movie': show.movie.name,
            'time': to_local(show.time).strftime('%I:%M %p, %A, %b %d'),
            'status': show_status_info(show),
            'ticket_price': str(show.ticket_price),
            'prices': {str(item['id']): str(item['price']) for item in seat_data},
            'tiers': tier_prices,
            'platform_fee': str(config['platform_fee_per_ticket']),
            'misc_fee': str(config['misc_fee_per_booking']),
            'gst_slabs': gst_slabs(),
            'layout': layout,
            'couple_pairs': couple_pairs,
            'max_tickets': MAX_TICKET_COUNT,
            'ticket_count': ticket_count or 1,
        },
        'reservation_data': active_reservation,
    })


@login_required(login_url='/login/')
def seat_status(request, theater_id):
    show = get_object_or_404(Theater, id=theater_id, status='active')
    expire_stale_for_show(show)
    status_info = show_status_info(show)
    revision = Theater.objects.get(pk=show.pk).seat_revision
    # Include the live showtime status so a status change (upcoming -> late
    # entry -> expired) busts the ETag cache even when no seat changed.
    etag = f'"rev-{revision}-st-{status_info["status"]}-{status_info["deadline"].timestamp()}"'
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
        'show_status': status_info,
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
        reservation = create_reservation(
            request.user,
            show_id,
            param_list('seats'),
            ticket_count=param('ticket_count', None),
        )
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
            ticket_count=param('ticket_count', None),
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
    if not show_bookable(reservation.show):
        messages.error(
            request,
            'This show is no longer available for booking — its late-entry '
            'window has closed.',
        )
        return redirect('book_seats', theater_id=reservation.show_id)
    pricing = reservation_pricing(reservation)
    return render(request, 'movies/payment.html', {
        'reservation': reservation,
        'seats': reservation.reserved_seats.select_related('seat'),
        'pricing': pricing,
        'show_status': show_status_info(reservation.show),
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


def _ticket_context(request, booking_ref, reservation, booking):
    """Build the shared context used by the redesigned ticket page."""
    if reservation is not None:
        bookings = list(
            reservation.bookings.select_related('seat', 'payment').order_by('seat__seat_number')
        )
        movie = reservation.show.movie
        theater = reservation.show
        seats = [b.seat.seat_number for b in bookings]
        ticket_count = reservation.ticket_count or len(seats)
        booked_for = reservation.user.get_full_name() or reservation.user.username
        total = reservation.total_amount
        payment_tx = PaymentTransaction.objects.filter(
            reservation=reservation, status='captured'
        ).order_by('-captured_at').first()
        payment_method = payment_tx.method if payment_tx else 'Online'
        transaction_id = payment_tx.gateway_payment_id if payment_tx else ''
        if not transaction_id and bookings and bookings[0].payment:
            transaction_id = bookings[0].payment.transaction_id or ''
            payment_method = bookings[0].payment.payment_method or payment_method
        ref = reservation.booking_ref
    else:
        bookings = [booking]
        movie = booking.movie
        theater = booking.theater
        seats = [booking.seat.seat_number]
        ticket_count = 1
        booked_for = booking.user.get_full_name() or booking.user.username
        total = booking.total
        payment = booking.payment if hasattr(booking, 'payment') else None
        payment_method = payment.payment_method if payment else 'Online'
        transaction_id = payment.transaction_id if payment else ''
        ref = booking.booking_ref

    poster_url = movie.image.url if movie.image else None
    language_label = ', '.join(m.name for m in movie.languages.all()[:3])
    qr_payload = build_qr_payload(ref, movie.name, theater.name, seats)
    ticket_url = request.build_absolute_uri(reverse('download_ticket', args=[ref]))
    wa_message = (
        '🎬 {movie}\n'
        '📍 {theatre}\n'
        '🕒 {time}\n'
        '🎟 Seats: {seats}\n\n'
        'View Ticket: {url}'
    ).format(
        movie=movie.name,
        theatre=theater.name,
        time=theater.time.strftime('%d %b %Y, %I:%M %p'),
        seats=', '.join(seats),
        url=ticket_url,
    )
    return {
        'booking_ref': ref,
        'movie_name': movie.name,
        'theatre_name': theater.name,
        'screen_name': theater.screen_name or 'Main',
        'show_time': theater.time,
        'seats': seats,
        'ticket_count': ticket_count,
        'booked_for': booked_for,
        'total': total,
        'payment_method': payment_method or 'Online',
        'transaction_id': transaction_id,
        'poster_url': poster_url,
        'language_label': language_label,
        'format_label': '2D / 4K',
        'qr_payload': qr_payload,
        'qr_data_uri': ticket_qr_data_uri(qr_payload),
        'wa_link': 'https://wa.me/?text=' + quote(wa_message),
        'wa_web_link': 'https://web.whatsapp.com/send?text=' + quote(wa_message),
    }


@login_required(login_url='/login/')
def download_ticket(request, booking_ref):
    reservation, booking = _resolve_ticket_target(booking_ref)
    if reservation is None and booking is None:
        messages.error(request, 'This ticket could not be found.')
        return redirect('profile')
    owner = reservation.user if reservation else booking.user
    if owner.id != request.user.id:
        return render(request, 'movies/ticket.html', {
            'not_your_ticket': True,
            'booking_ref': booking_ref,
        }, status=200)
    if (reservation and reservation.status != 'booked') or (booking and booking.status != 'confirmed'):
        messages.error(request, 'This ticket is no longer valid.')
        return redirect('profile')
    context = _ticket_context(request, booking_ref, reservation, booking)
    context['not_your_ticket'] = False
    _record_ticket_download(request, booking_ref, context.get('movie_name', ''))
    return render(request, 'movies/ticket.html', context)


def _record_ticket_download(request, booking_ref, movie_name=''):
    """Light-weight audit trail for ticket views/downloads."""
    try:
        TicketDownload.objects.create(
            user=request.user,
            booking_ref=booking_ref,
            movie=movie_name,
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )
    except Exception:
        pass


@login_required(login_url='/login/')
def ticket_pdf(request, booking_ref):
    """Stream a server-side generated PDF M-ticket (reportlab, guarded)."""
    reservation, booking = _resolve_ticket_target(booking_ref)
    if reservation is None and booking is None:
        messages.error(request, 'This ticket could not be found.')
        return redirect('profile')
    owner = reservation.user if reservation else booking.user
    if owner.id != request.user.id:
        messages.error(request, 'This ticket does not belong to you.')
        return redirect('profile')
    if (reservation and reservation.status != 'booked') or (booking and booking.status != 'confirmed'):
        messages.error(request, 'This ticket is no longer valid.')
        return redirect('profile')
    context = _ticket_context(request, booking_ref, reservation, booking)
    from .pdf import build_ticket_pdf
    pdf_bytes = build_ticket_pdf(context)
    if not pdf_bytes:
        messages.error(request, 'PDF generation is not available right now. Please use Print Ticket instead.')
        return redirect('download_ticket', booking_ref=booking_ref)
    _record_ticket_download(request, booking_ref, context.get('movie_name', ''))
    filename = 'ticket_{}.pdf'.format(booking_ref)
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="{}"'.format(filename)
    return response


@csrf_exempt
@require_POST
def verify_ticket_qr(request):
    """Gate-scanner validation for a signed ticket QR payload.

    The QR is one-time usable: the first successful scan marks the ticket as
    scanned and later scans are reported as ``already_scanned`` so the venue
    can deny re-entry. Delegates to the shared ``ticket_scan`` core which also
    records the scan-history audit trail.
    """
    from bookmyseat.ratelimit import scan_is_locked, scan_rate_limit
    if scan_is_locked(request):
        return JsonResponse(
            {'valid': False, 'reason': 'rate_limited',
             'message': 'Too many requests. Please try again later.'},
            status=429,
        )
    scan_rate_limit(request)
    raw = (request.body or b'').decode('utf-8', 'ignore')
    try:
        payload = json.loads(raw)
    except Exception:
        payload = None
    return scan_ticket(payload)


@login_required(login_url='/login/')
def booking_invoice(request, booking_ref):
    """Printable GST invoice for a confirmed transaction-level booking."""
    reservation, booking = _resolve_ticket_target(booking_ref)
    if reservation is None and booking is None:
        messages.error(request, 'This invoice could not be found.')
        return redirect('profile')
    owner = reservation.user if reservation else booking.user
    if owner.id != request.user.id:
        messages.error(request, 'This invoice does not belong to you.')
        return redirect('profile')
    if (reservation and reservation.status != 'booked') or (booking and booking.status != 'confirmed'):
        messages.error(request, 'An invoice is only available for confirmed bookings.')
        return redirect('profile')

    if reservation is not None:
        bookings = list(
            reservation.bookings.select_related('seat', 'payment').order_by('seat__seat_number')
        )
        return render(request, 'movies/invoice.html', {
            'invoice_number': 'INV-{}'.format(reservation.booking_ref),
            'booking_ref': reservation.booking_ref,
            'movie_name': reservation.show.movie.name,
            'theatre_name': reservation.show.name,
            'screen_name': reservation.show.screen_name or 'Main',
            'show_time': reservation.show.time,
            'seat_labels': [b.seat.seat_number for b in bookings],
            'ticket_count': reservation.ticket_count or len(bookings),
            'customer': reservation.user,
            'booked_at': reservation.updated_at,
            'subtotal': reservation.subtotal_amount,
            'platform_fee': reservation.platform_fee,
            'misc_fee': reservation.misc_fee,
            'gst_rate': reservation.gst_rate,
            'gst_amount': reservation.gst_amount,
            'discount': reservation.discount_amount,
            'coupon_code': reservation.coupon_code,
            'total': reservation.total_amount,
            'payment': bookings[0].payment if bookings else None,
        })
    payment = booking.payment if hasattr(booking, 'payment') else None
    return render(request, 'movies/invoice.html', {
        'invoice_number': 'INV-{}'.format(booking.booking_ref),
        'booking_ref': booking.booking_ref,
        'movie_name': booking.movie.name,
        'theatre_name': booking.theater.name,
        'screen_name': booking.theater.screen_name or 'Main',
        'show_time': booking.theater.time,
        'seat_labels': [booking.seat.seat_number],
        'ticket_count': 1,
        'customer': booking.user,
        'booked_at': booking.booked_at,
        'subtotal': booking.ticket_price,
        'platform_fee': booking.platform_fee,
        'misc_fee': booking.misc_fee,
        'gst_rate': booking.gst_rate,
        'gst_amount': booking.gst_amount,
        'discount': booking.discount,
        'coupon_code': '',
        'total': booking.total,
        'payment': payment,
    })


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
def cancel_booking_ref_view(request, booking_ref):
    """Cancel an entire transaction-level booking (all seats) by its booking_ref."""
    if request.method != 'POST':
        return redirect('profile')
    try:
        cancel_reservation_booking(request.user, booking_ref)
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
        AuditLog.objects.create(
            user=request.user,
            action='Review Reported',
            module='Review',
            object_id=review.id,
            details=f'User reported review for {review.movie.name} by {review.user.username}',
            ip_address=request.META.get('REMOTE_ADDR')
        )
    messages.success(request, 'Review has been reported for moderation.')
    return redirect('movie_detail', movie_id=review.movie.id)


@login_required(login_url='/login/')
@require_POST
def helpful_review(request, review_id):
    review = get_object_or_404(Review, id=review_id)
    if review.user_id == request.user.id:
        messages.error(request, 'You cannot vote on your own review.')
        return redirect('movie_detail', movie_id=review.movie.id)
    vote, created = ReviewHelpful.objects.get_or_create(review=review, user=request.user)
    if not created:
        vote.delete()
        messages.success(request, 'You removed your helpful vote.')
    else:
        messages.success(request, 'Thanks! Your vote helps other viewers.')
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




