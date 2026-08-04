from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from .forms import UserRegisterForm, UserUpdateForm
from django.shortcuts import render,redirect,get_object_or_404
from django.contrib.auth import login, authenticate, update_session_auth_hash
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import JsonResponse
from movies.models import Movie, Booking, Wishlist
from admin_panel.models import AdminProfile, PaymentTransaction, Notification
from bookmyseat.ratelimit import is_locked_out, login_failed, login_succeeded

def home(request):
    movies = Movie.objects.filter(
        status='now_showing',
        show_on_homepage=True,
        is_deleted=False
    )
    recent_ids = request.session.get('recently_viewed', [])
    recent_movies = []
    if recent_ids:
        recent_movies = list(
            Movie.objects.filter(id__in=recent_ids, is_deleted=False)
            .exclude(status__in=['archived', 'hidden'])
        )
        ordered = {mid: i for i, mid in enumerate(recent_ids)}
        recent_movies.sort(key=lambda m: ordered.get(m.id, len(ordered)))
    return render(request,'home.html',{'movies':movies, 'recently_viewed':recent_movies})

def register(request):
    if request.method == 'POST':
        form=UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            username=form.cleaned_data.get('username')
            password=form.cleaned_data.get('password1')
            user=authenticate(username=username,password=password)
            login(request,user)
            return redirect('profile')
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
            return redirect(next_url)
        else:
            username = request.POST.get('username', '')
            login_failed('user', request, username)
            if is_locked_out('user', request, username):
                messages.error(request, 'Too many failed attempts. Please wait a few minutes and try again.')
    else:
        form=AuthenticationForm()
    return render(request,'users/login.html',{'form':form, 'next': next_url})

@login_required
def profile(request):
    status_filter = request.GET.get('filter', '')
    bookings_qs = Booking.objects.filter(user=request.user).select_related(
        'movie', 'theater', 'seat', 'reservation', 'payment'
    )
    now = timezone.now()
    if status_filter == 'upcoming':
        bookings_qs = bookings_qs.filter(theater__time__gte=now)
    elif status_filter == 'past':
        bookings_qs = bookings_qs.filter(theater__time__lt=now)
    bookings = bookings_qs.order_by('-booked_at')
    for booking in bookings:
        booking.cancel_allowed = booking.theater.time > now
    transactions = PaymentTransaction.objects.filter(user=request.user).select_related(
        'reservation', 'reservation__show', 'reservation__show__movie'
    )[:50]
    if request.method == 'POST':
        u_form = UserUpdateForm(request.POST, instance=request.user)
        if u_form.is_valid():
            u_form.save()
            return redirect('profile')
    else:
        u_form = UserUpdateForm(instance=request.user)

    wishlist_movies = Movie.objects.filter(wishlisted_by__user=request.user).select_related()
    unread_notifications = Notification.objects.filter(user=request.user, is_read=False).count()

    return render(request, 'users/profile.html', {
        'u_form': u_form,
        'bookings': bookings,
        'transactions': transactions,
        'status_filter': status_filter,
        'wishlist_movies': wishlist_movies,
        'unread_notifications': unread_notifications,
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
    response = redirect('home')
    response.delete_cookie('csrftoken')
    return response
