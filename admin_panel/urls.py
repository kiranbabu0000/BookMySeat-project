from django.urls import path
from . import views

urlpatterns = [
    path('admin-login/', views.admin_login_view, name='admin_login'),
    path('admin-logout/', views.admin_logout_view, name='admin_logout'),
    path('dashboard/', views.DashboardView.as_view(), name='admin_dashboard'),
    path('profile/', views.profile_view, name='admin_profile'),

    path('movies/', views.MovieListView.as_view(), name='admin_movie_list'),
    path('movies/add/', views.MovieCreateView.as_view(), name='admin_movie_add'),
    path('movies/<int:pk>/edit/', views.MovieUpdateView.as_view(), name='admin_movie_edit'),
    path('movies/<int:pk>/delete/', views.MovieDeleteView.as_view(), name='admin_movie_delete'),
    path('movies/<int:pk>/', views.MovieDetailView.as_view(), name='admin_movie_detail'),
    path('movies/<int:pk>/toggle-status/', views.movie_toggle_status, name='admin_movie_toggle_status'),

    path('genres/', views.GenreListView.as_view(), name='admin_genre_list'),
    path('genres/add/', views.GenreCreateView.as_view(), name='admin_genre_add'),
    path('genres/<int:pk>/edit/', views.GenreUpdateView.as_view(), name='admin_genre_edit'),
    path('genres/<int:pk>/delete/', views.GenreDeleteView.as_view(), name='admin_genre_delete'),

    path('languages/', views.LanguageListView.as_view(), name='admin_language_list'),
    path('languages/add/', views.LanguageCreateView.as_view(), name='admin_language_add'),
    path('languages/<int:pk>/edit/', views.LanguageUpdateView.as_view(), name='admin_language_edit'),
    path('languages/<int:pk>/delete/', views.LanguageDeleteView.as_view(), name='admin_language_delete'),

    path('cast/', views.CastListView.as_view(), name='admin_cast_list'),
    path('cast/add/', views.CastCreateView.as_view(), name='admin_cast_add'),
    path('cast/<int:pk>/edit/', views.CastUpdateView.as_view(), name='admin_cast_edit'),
    path('cast/<int:pk>/delete/', views.CastDeleteView.as_view(), name='admin_cast_delete'),

    path('theatres/', views.TheatreListView.as_view(), name='admin_theatre_list'),
    path('theatres/add/', views.TheatreCreateView.as_view(), name='admin_theatre_add'),
    path('theatres/<int:pk>/edit/', views.TheatreUpdateView.as_view(), name='admin_theatre_edit'),
    path('theatres/<int:pk>/delete/', views.TheatreDeleteView.as_view(), name='admin_theatre_delete'),

    path('screens/', views.ScreenListView.as_view(), name='admin_screen_list'),
    path('screens/add/', views.ScreenCreateView.as_view(), name='admin_screen_add'),
    path('screens/<int:pk>/edit/', views.ScreenUpdateView.as_view(), name='admin_screen_edit'),
    path('screens/<int:pk>/delete/', views.ScreenDeleteView.as_view(), name='admin_screen_delete'),

    path('shows/', views.ShowListView.as_view(), name='admin_show_list'),
    path('shows/add/', views.ShowCreateView.as_view(), name='admin_show_add'),
    path('shows/<int:pk>/edit/', views.ShowUpdateView.as_view(), name='admin_show_edit'),
    path('shows/<int:pk>/delete/', views.ShowDeleteView.as_view(), name='admin_show_delete'),
    path('shows/<int:pk>/toggle-status/', views.show_toggle_status, name='admin_show_toggle_status'),
    path('shows/bulk-action/', views.show_bulk_action, name='admin_show_bulk_action'),

    path('trailers/', views.TrailerListView.as_view(), name='admin_trailer_list'),
    path('trailers/add/', views.TrailerCreateView.as_view(), name='admin_trailer_add'),
    path('trailers/<int:pk>/edit/', views.TrailerUpdateView.as_view(), name='admin_trailer_edit'),
    path('trailers/<int:pk>/delete/', views.TrailerDeleteView.as_view(), name='admin_trailer_delete'),

    path('images/', views.MovieImageListView.as_view(), name='admin_image_list'),
    path('images/add/', views.MovieImageCreateView.as_view(), name='admin_image_add'),
    path('images/<int:pk>/delete/', views.MovieImageDeleteView.as_view(), name='admin_image_delete'),

    path('seats/', views.seat_management, name='admin_seat_management'),

    path('bookings/', views.BookingListView.as_view(), name='admin_booking_list'),
    path('bookings/<int:pk>/', views.booking_detail, name='admin_booking_detail'),
    path('bookings/<int:pk>/cancel/', views.booking_cancel, name='admin_booking_cancel'),
    path('bookings/reserve/', views.booking_reserve, name='admin_booking_reserve'),
    path('bookings/<int:pk>/modify/', views.booking_modify, name='admin_booking_modify'),
    path('bookings/<int:pk>/resend/', views.booking_resend_confirmation, name='admin_booking_resend'),

    path('users/', views.UserListView.as_view(), name='admin_user_list'),
    path('users/<int:pk>/toggle-active/', views.user_toggle_active, name='admin_user_toggle_active'),
    path('users/<int:pk>/bookings/', views.user_booking_history, name='admin_user_bookings'),
    path('users/<int:pk>/reset-password/', views.user_reset_password, name='admin_user_reset_password'),

    path('staff/', views.StaffListView.as_view(), name='admin_staff_list'),
    path('staff/add/', views.staff_create, name='admin_staff_add'),
    path('staff/<int:pk>/edit/', views.staff_edit, name='admin_staff_edit'),
    path('staff/<int:pk>/delete/', views.staff_delete, name='admin_staff_delete'),
    path('staff/<int:pk>/permissions/', views.staff_permissions, name='admin_staff_permissions'),

    path('coupons/', views.CouponListView.as_view(), name='admin_coupon_list'),
    path('coupons/add/', views.CouponCreateView.as_view(), name='admin_coupon_add'),
    path('coupons/<int:pk>/edit/', views.CouponUpdateView.as_view(), name='admin_coupon_edit'),
    path('coupons/<int:pk>/delete/', views.CouponDeleteView.as_view(), name='admin_coupon_delete'),

    path('notifications/', views.NotificationListView.as_view(), name='admin_notification_list'),
    path('notifications/add/', views.notification_create, name='admin_notification_add'),
    path('notifications/<int:pk>/mark-read/', views.notification_mark_read, name='admin_notification_mark_read'),
    path('notifications/<int:pk>/delete/', views.notification_delete, name='admin_notification_delete'),
    path('notifications/unread-count/', views.get_notifications, name='admin_notification_count'),

    path('reviews/', views.ReviewListView.as_view(), name='admin_review_list'),
    path('reviews/<int:pk>/approve/', views.review_approve, name='admin_review_approve'),
    path('reviews/<int:pk>/hide/', views.review_hide, name='admin_review_hide'),
    path('reviews/<int:pk>/restore/', views.review_restore, name='admin_review_restore'),
    path('reviews/<int:pk>/delete/', views.review_delete, name='admin_review_delete'),

    path('logs/', views.AuditLogListView.as_view(), name='admin_audit_logs'),

    path('settings/', views.SettingsView.as_view(), name='admin_settings'),
]
