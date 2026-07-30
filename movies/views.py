from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Count, Q, Avg
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.contrib import messages
from django.utils import timezone
from .models import Movie, Theater, Seat, Booking
from admin_panel.models import CastMember, Trailer, MovieImage, Review, Genre, Language, Show, Theatre, Screen


def movie_list(request):
    search_query = request.GET.get('search')
    genre_slug = request.GET.get('genre')
    lang_code = request.GET.get('language')
    movies = Movie.objects.filter(is_deleted=False).exclude(status__in=['archived', 'hidden'])
    if search_query:
        movies = movies.filter(name__icontains=search_query)
    if genre_slug:
        movies = movies.filter(genres__slug=genre_slug)
    if lang_code:
        movies = movies.filter(languages__code=lang_code)
    genres = Genre.objects.all()
    languages = Language.objects.all()
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
    reviews = Review.objects.filter(movie=movie, is_approved=True, is_hidden=False).select_related('user')
    user_review = None
    has_booked_and_completed = False
    if request.user.is_authenticated:
        user_review = Review.objects.filter(movie=movie, user=request.user).first()
        user_bookings = Booking.objects.filter(movie=movie, user=request.user).select_related('theater')
        for b in user_bookings:
            show_end = b.theater.time + timezone.timedelta(hours=3)
            if show_end < timezone.now():
                has_booked_and_completed = True
                break
    avg_rating = reviews.aggregate(Avg('rating'))['rating__avg']
    total_reviews = reviews.count()
    rating_dist = {i: 0 for i in range(1, 6)}
    for r in reviews:
        if r.rating in rating_dist:
            rating_dist[r.rating] += 1
    visible_filter = {'is_deleted': False}
    similar_movies = Movie.objects.filter(genres__in=movie.genres.all(), **visible_filter).exclude(id=movie.id).exclude(status__in=['archived', 'hidden']).distinct()[:6]
    trending_movies = Movie.objects.filter(**visible_filter).exclude(status__in=['archived', 'hidden']).annotate(booking_count=Count('booking')).exclude(id=movie.id).order_by('-booking_count')[:6]
    recently_released = Movie.objects.filter(status='now_showing', **visible_filter).exclude(id=movie.id).order_by('-release_date')[:6]
    theaters = Theater.objects.filter(movie=movie).order_by('time')
    shows = Show.objects.filter(movie=movie, status='active').select_related('theatre', 'screen').order_by('date', 'time')
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
    })


def theater_list(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    theater = Theater.objects.filter(movie=movie)
    return render(request, 'movies/theater_list.html', {'movie': movie, 'theaters': theater})


@login_required(login_url='/login/')
def book_seats(request, theater_id):
    theaters = get_object_or_404(Theater, id=theater_id)
    seats = Seat.objects.filter(theater=theaters)
    if request.method == 'POST':
        selected_Seats = request.POST.getlist('seats')
        error_seats = []
        if not selected_Seats:
            return render(request, "movies/seat_selection.html", {'theaters': theaters, "seats": seats, 'error': "No seat selected"})
        for seat_id in selected_Seats:
            seat = get_object_or_404(Seat, id=seat_id, theater=theaters)
            if seat.is_booked:
                error_seats.append(seat.seat_number)
                continue
            try:
                Booking.objects.create(
                    user=request.user,
                    seat=seat,
                    movie=theaters.movie,
                    theater=theaters
                )
                seat.is_booked = True
                seat.save()
            except IntegrityError:
                error_seats.append(seat.seat_number)
        if error_seats:
            error_message = f"The following seats are already booked: {', '.join(error_seats)}"
            return render(request, 'movies/seat_selection.html', {'theaters': theaters, "seats": seats, 'error': error_message})
        return redirect('profile')
    return render(request, 'movies/seat_selection.html', {'theaters': theaters, "seats": seats})


@login_required(login_url='/login/')
def report_review(request, review_id):
    review = get_object_or_404(Review, id=review_id)
    review.is_reported = True
    review.save()
    messages.success(request, 'Review has been reported for moderation.')
    return redirect('movie_detail', movie_id=review.movie.id)


@login_required(login_url='/login/')
def submit_review(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
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
            show_end = b.theater.time + timezone.timedelta(hours=3)
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




