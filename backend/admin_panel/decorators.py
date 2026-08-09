from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from .models import AdminProfile, AdminPermission

ADMIN_SESSION_KEYS = (
    'admin_user_id', 'is_admin_authenticated', 'admin_session_id',
    'admin_login_time', 'admin_ip_address', 'admin_user_agent',
)


def clear_admin_session(request):
    for key in ADMIN_SESSION_KEYS:
        request.session.pop(key, None)


def _verify_admin_session(request):
    if not request.session.get('is_admin_authenticated'):
        return False
    if not request.session.get('admin_user_id'):
        return False
    if not request.user.is_authenticated:
        return False
    if not (request.user.is_superuser or AdminProfile.objects.filter(user=request.user, is_active=True).exists()):
        return False
    stored_session_id = request.session.get('admin_session_id')
    if stored_session_id and stored_session_id != request.session.session_key:
        return False
    if not request.user.is_active:
        return False
    return True


def admin_session_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not _verify_admin_session(request):
            clear_admin_session(request)
            return redirect('/admin-login/')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


class AdminSessionMixin(UserPassesTestMixin):
    login_url = '/admin-login/'
    redirect_field_name = None

    def test_func(self):
        return _verify_admin_session(self.request)

    def handle_no_permission(self):
        clear_admin_session(self.request)
        return redirect(self.login_url)


def permission_required(module, action='can_view'):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not _verify_admin_session(request):
                clear_admin_session(request)
                return redirect('/admin-login/')
            user = request.user
            if user.is_superuser:
                return view_func(request, *args, **kwargs)
            try:
                profile = AdminProfile.objects.get(user=user, is_active=True)
                if profile.role == 'super_admin':
                    return view_func(request, *args, **kwargs)
                if profile.role == 'admin' and module not in ['settings', 'staff']:
                    return view_func(request, *args, **kwargs)
                perm = AdminPermission.objects.filter(
                    admin_profile=profile, module=module
                ).first()
                if perm and getattr(perm, action, False):
                    return view_func(request, *args, **kwargs)
                messages.error(request, 'You do not have permission for this action.')
                return redirect('admin_dashboard')
            except AdminProfile.DoesNotExist:
                messages.error(request, 'Admin profile not found.')
                return redirect('admin_logout')
        return _wrapped_view
    return decorator
