from django.core.cache import cache

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300
LOCKOUT_SECONDS = 900
LOCKOUT_KEY = 'bms_rl_locked'


def _ip(request):
    return (request.META.get('HTTP_X_FORWARDED_FOR', '')
            .split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', ''))


def _keys(scope, ip, username):
    base = 'bms_rl_{}_{}_{}'.format(scope, ip or '-', (username or '').lower())
    return base, '{}_global'.format(base)


def is_locked_out(scope, request, username):
    ip = _ip(request)
    if not ip:
        return False
    for key in _keys(scope, ip, username):
        if cache.get(key) == LOCKOUT_KEY:
            return True
    return False


def remaining_attempts(scope, request, username):
    ip = _ip(request)
    if not ip:
        return MAX_ATTEMPTS
    per_ip_username, _ = _keys(scope, ip, username)
    return max(0, MAX_ATTEMPTS - cache.get(per_ip_username, 0))


def login_failed(scope, request, username):
    ip = _ip(request)
    if not ip:
        return 1
    per_ip_username, per_ip = _keys(scope, ip, username)
    ip_username_attempts = cache.get(per_ip_username, 0) + 1
    ip_attempts = cache.get(per_ip, 0) + 1
    if ip_username_attempts >= MAX_ATTEMPTS or ip_attempts >= MAX_ATTEMPTS * 3:
        cache.set(per_ip_username, ip_username_attempts, LOCKOUT_SECONDS)
        cache.set(per_ip, ip_attempts, LOCKOUT_SECONDS)
    else:
        cache.set(per_ip_username, ip_username_attempts, WINDOW_SECONDS)
        cache.set(per_ip, ip_attempts, WINDOW_SECONDS)
    return ip_username_attempts


def login_succeeded(scope, request, username):
    ip = _ip(request)
    if not ip:
        return
    for key in _keys(scope, ip, username):
        cache.delete(key)
