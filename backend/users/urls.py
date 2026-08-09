from django.urls import path
from .views import register, login_view, register_otp, register_otp_resend, profile, reset_password, home, user_logout_view, toggle_wishlist, wishlist, my_notifications, mark_notification_read
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('',home,name='home'),
    path('register/', register, name='register'),
    path('login/', login_view, name='login'),
    path('register/otp/', register_otp, name='register_otp'),
    path('register/otp/resend/', register_otp_resend, name='register_otp_resend'),
    path('profile/', profile, name='profile'),
    path('wishlist/', wishlist, name='wishlist'),
    path('wishlist/toggle/<int:movie_id>/', toggle_wishlist, name='toggle_wishlist'),
    path('notifications/', my_notifications, name='my_notifications'),
    path('notifications/<int:pk>/read/', mark_notification_read, name='mark_notification_read'),
    path('reset-password/', reset_password, name='reset-password'),
    path('logout/', user_logout_view, name='logout'),
    path('password-reset/',
         auth_views.PasswordResetView.as_view(template_name='users/reset_password.html'),
         name='password_reset'),
    path('password-reset/done/',
         auth_views.PasswordResetDoneView.as_view(template_name='users/password_reset_done.html'),
         name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(template_name='users/password_reset_confirm.html'),
         name='password_reset_confirm'),
    path('password-reset-complete/',
         auth_views.PasswordResetCompleteView.as_view(template_name='users/password_reset_complete.html'),
         name='password_reset_complete'),
]
