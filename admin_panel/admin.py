from django.contrib import admin
from .models import (
    Genre, Language, CastMember, Theatre, Screen, Show, Trailer, MovieImage,
    AdminProfile, AdminPermission, AuditLog, Coupon, Notification, Review, Payment
)


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ['name', 'code']


@admin.register(CastMember)
class CastMemberAdmin(admin.ModelAdmin):
    list_display = ['name', 'movie', 'character_name', 'role']
    list_filter = ['role', 'movie']


@admin.register(Theatre)
class TheatreAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'contact', 'is_active']
    list_filter = ['is_active', 'city']
    search_fields = ['name', 'city']


@admin.register(Screen)
class ScreenAdmin(admin.ModelAdmin):
    list_display = ['name', 'theatre', 'capacity']
    list_filter = ['theatre']


@admin.register(Show)
class ShowAdmin(admin.ModelAdmin):
    list_display = ['movie', 'theatre', 'screen', 'date', 'time', 'ticket_price', 'status']
    list_filter = ['status', 'movie', 'theatre']


@admin.register(Trailer)
class TrailerAdmin(admin.ModelAdmin):
    list_display = ['movie', 'title', 'is_featured']
    list_filter = ['is_featured']


@admin.register(MovieImage)
class MovieImageAdmin(admin.ModelAdmin):
    list_display = ['movie', 'caption', 'uploaded_at']


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'department', 'is_active']
    list_filter = ['role', 'is_active']


@admin.register(AdminPermission)
class AdminPermissionAdmin(admin.ModelAdmin):
    list_display = ['admin_profile', 'module', 'can_view', 'can_create', 'can_edit', 'can_delete']
    list_filter = ['module']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'action', 'module', 'created_at']
    list_filter = ['module', 'created_at']
    search_fields = ['action', 'user__username']


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ['code', 'discount_percent', 'max_uses', 'used_count', 'is_active', 'valid_to']
    list_filter = ['is_active']


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'notification_type', 'user', 'is_read', 'created_at']
    list_filter = ['notification_type', 'is_read']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['movie', 'user', 'rating', 'is_approved', 'created_at']
    list_filter = ['is_approved', 'rating']


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['booking', 'amount', 'payment_method', 'status', 'paid_at']
    list_filter = ['status', 'payment_method']
