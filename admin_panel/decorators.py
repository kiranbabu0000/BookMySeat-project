from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from .models import AdminProfile, AdminPermission


def admin_session_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/admin-login/')
        if not (request.user.is_staff or request.user.is_superuser):
            messages.error(request, 'Unauthorized Access.')
            return redirect('/')
        if not request.session.get('is_admin_authenticated'):
            return redirect('/admin-login/')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


class AdminSessionMixin:
    login_url = '/admin-login/'
    redirect_field_name = None

    def test_func(self):
        return (
            self.request.user.is_authenticated and
            (self.request.user.is_staff or self.request.user.is_superuser) and
            self.request.session.get('is_admin_authenticated', False)
        )

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            if self.request.user.is_staff or self.request.user.is_superuser:
                if not self.request.session.get('is_admin_authenticated'):
                    return redirect('/admin-login/')
            messages.error(self.request, 'Unauthorized Access.')
            return redirect('/')
        return redirect(self.login_url)


def permission_required(module, action='can_view'):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
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
