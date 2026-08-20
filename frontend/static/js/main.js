/**
 * BookMySeat — Global interactions
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    initCinematicIntro();
    initNavbarScroll();
    initPasswordToggles();
    initFormLoading();
    setActiveNavLink();
    initFitText();
    initImageFallback();
    initWishlistButtons();
  });

  function bmsToast(options) {
    var opts = options || {};
    var type = opts.type === 'error' ? 'error' : (opts.type === 'info' ? 'info' : 'success');
    var root = document.querySelector('.bms-toast-root');
    if (!root) {
      root = document.createElement('div');
      root.className = 'bms-toast-root';
      root.setAttribute('aria-live', 'polite');
      root.setAttribute('aria-atomic', 'false');
      document.body.appendChild(root);
    }
    var icons = { success: 'bi-check-circle-fill', error: 'bi-x-circle-fill', info: 'bi-info-circle-fill' };
    var toast = document.createElement('div');
    toast.className = 'bms-toast bms-toast--' + type;
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
    toast.innerHTML =
      '<span class="bms-toast__icon"><i class="bi ' + (icons[type] || icons.success) + '" aria-hidden="true"></i></span>' +
      '<div class="bms-toast__body">' +
        (opts.title ? '<p class="bms-toast__title"></p>' : '') +
        (opts.message ? '<p class="bms-toast__message"></p>' : '') +
      '</div>' +
      '<button type="button" class="bms-toast__close" aria-label="Dismiss notification"><i class="bi bi-x-lg" aria-hidden="true"></i></button>';
    if (opts.title) toast.querySelector('.bms-toast__title').textContent = opts.title;
    if (opts.message) toast.querySelector('.bms-toast__message').textContent = opts.message;
    root.appendChild(toast);

    var duration = typeof opts.duration === 'number' ? opts.duration : 4000;
    var timer = null;
    function dismiss() {
      if (toast.classList.contains('is-leaving')) return;
      toast.classList.add('is-leaving');
      if (timer) clearTimeout(timer);
      setTimeout(function () { toast.remove(); }, 260);
    }
    toast.querySelector('.bms-toast__close').addEventListener('click', dismiss);
    timer = setTimeout(dismiss, duration);
  }
  window.bmsToast = bmsToast;

  /* ============================================================
     Cinematic Intro — Premium opening animation
     Plays once per browser session on first visit.
     ============================================================ */
  function initCinematicIntro() {
    var INTRO_KEY = 'bms-intro-seen';
    var overlay = document.getElementById('bmsIntroOverlay');
    var logo = document.getElementById('bmsIntroLogo');

    // Guard: skip if intro already seen, elements missing, or reduced motion
    if (!overlay || !logo) return;
    try { if (sessionStorage.getItem(INTRO_KEY)) { overlay.remove(); return; } } catch (e) {}
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      overlay.remove();
      return;
    }

    // Immediately hide page elements behind the overlay
    document.body.classList.add('bms-intro-active');

    // Max failsafe — force-dismiss after 4 seconds no matter what
    var failsafe = setTimeout(function () { dismissIntro(); }, 4000);

    // Collect page elements that will be revealed
    var revealEls = [
      document.querySelector('.navbar-bms'),
      document.querySelector('main'),
      document.querySelector('.footer-bms'),
      document.querySelector('.bottom-nav')
    ].filter(Boolean);

    // Wait for next frame so overlay is painted, then start sequence
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        startSequence();
      });
    });

    function startSequence() {
      // Phase 1: Activate overlay (dark cinema background fades in)
      overlay.classList.add('is-active');

      // Phase 2: Logo reveal + light sweep (after 0.3s)
      setTimeout(function () {
        var sweep = overlay.querySelector('.bms-intro-sweep');
        if (sweep) sweep.classList.add('is-sweeping');
      }, 300);

      // Phase 3: Hold logo briefly, then animate to navbar (at ~1.2s)
      setTimeout(function () {
        animateToNavbar();
      }, 1200);
    }

    function animateToNavbar() {
      // Find the real navbar brand element
      var navbarBrand = document.querySelector('.navbar-bms .navbar-brand');
      if (!navbarBrand) { dismissIntro(); return; }

      // Get the target position from the actual navbar brand
      var target = navbarBrand.getBoundingClientRect();

      // Get current intro logo position
      var source = logo.getBoundingClientRect();

      // Calculate FLIP delta — move the intro logo to overlap the navbar brand
      var deltaX = target.left - source.left;
      var deltaY = target.top - source.top;

      // Scale to match the navbar brand's overall size
      var scaleX = target.width / source.width;
      var scaleY = target.height / source.height;
      var finalScale = Math.min(scaleX, scaleY);

      // Add animating class for smooth CSS transition
      logo.classList.add('is-animating');

      // Apply FLIP transform on the next frame
      requestAnimationFrame(function () {
        logo.style.transform = 'translate(' + deltaX + 'px, ' + deltaY + 'px) scale(' + finalScale + ')';
        logo.style.opacity = '0.4';
      });

      // Phase 4: Fade overlay and reveal page (starts during movement)
      setTimeout(function () {
        // Fade the overlay background
        overlay.classList.add('is-fading');

        // Remove the CSS hiding class first
        document.body.classList.remove('bms-intro-active');

        // Set elements to hidden state via inline styles (matching pre-remove state)
        revealEls.forEach(function (el) {
          el.style.opacity = '0';
          el.style.pointerEvents = 'none';
        });

        // Transition elements to visible on the next frame
        requestAnimationFrame(function () {
          requestAnimationFrame(function () {
            revealEls.forEach(function (el, i) {
              // Stagger: navbar first, then content, then footer
              var delay = i === 0 ? '0s' : (i === 1 ? '0.12s' : '0.2s');
              el.style.transition = 'opacity 0.55s ease ' + delay;
              el.style.opacity = '1';
              el.style.pointerEvents = '';
            });
          });
        });
      }, 650);

      // Phase 5: Clean up overlay and inline styles
      setTimeout(function () {
        dismissIntro();
      }, 1250);
    }

    function dismissIntro() {
      clearTimeout(failsafe);
      if (!overlay || overlay.dataset.dismissed) return;
      overlay.dataset.dismissed = '1';

      // Ensure body class is removed
      document.body.classList.remove('bms-intro-active');

      // Clean up all inline styles from revealed elements
      revealEls.forEach(function (el) {
        el.style.opacity = '';
        el.style.pointerEvents = '';
        el.style.transition = '';
      });

      // Mark intro as seen in this session
      try { sessionStorage.setItem(INTRO_KEY, '1'); } catch (e) {}

      // Remove overlay from DOM
      overlay.remove();
    }
  }

  function initWishlistButtons() {
    document.querySelectorAll('form[data-wishlist-form]').forEach(function (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var btn = document.getElementById('wishlist-toggle-btn');
        var icon = document.getElementById('wishlist-icon');
        var label = document.getElementById('wishlist-label');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        var csrf = form.querySelector('[name="csrfmiddlewaretoken"]');
        var body = csrf ? new URLSearchParams({ csrfmiddlewaretoken: csrf.value }) : new URLSearchParams();
        fetch(form.action, {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/x-www-form-urlencoded' },
          credentials: 'same-origin',
          body: body.toString()
        }).then(function (res) {
          if (!res.ok) throw new Error('Request failed');
          return res.json();
        }).then(function (data) {
          var inWishlist = !!data.in_wishlist;
          btn.classList.toggle('btn-bms-wishlist', inWishlist);
          btn.setAttribute('aria-pressed', inWishlist ? 'true' : 'false');
          if (icon) {
            icon.classList.toggle('bi-heart-fill', inWishlist);
            icon.classList.toggle('bi-heart', !inWishlist);
            icon.classList.remove('heart-pulse');
            void icon.offsetWidth;
            icon.classList.add('heart-pulse');
          }
          if (label) label.textContent = inWishlist ? 'In Wishlist' : 'Add to Wishlist';
          bmsToast({
            type: 'success',
            title: inWishlist ? 'Added to Wishlist' : 'Removed from Wishlist',
            message: inWishlist ? 'Found something you love? Book it before it sells out.' : 'Saved for later — browse some more picks.',
            duration: 3200
          });
        }).catch(function () {
          bmsToast({
            type: 'error',
            title: 'Something went wrong',
            message: 'Could not update your wishlist. Please try again.',
            duration: 3200
          });
        }).finally(function () {
          btn.disabled = false;
        });
      });
    });
  }

  function initNavbarScroll() {
    var navbar = document.querySelector('.navbar-bms');
    if (!navbar) return;

    window.addEventListener('scroll', function () {
      navbar.classList.toggle('scrolled', window.scrollY > 20);
    }, { passive: true });
  }

  function initPasswordToggles() {
    document.querySelectorAll('[data-toggle-password]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var targetId = btn.getAttribute('data-toggle-password');
        var input = document.getElementById(targetId);
        var icon = btn.querySelector('i');
        if (!input) return;

        var isPassword = input.type === 'password';
        input.type = isPassword ? 'text' : 'password';
        if (icon) {
          icon.classList.toggle('bi-eye', !isPassword);
          icon.classList.toggle('bi-eye-slash', isPassword);
        }
        btn.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
      });
    });
  }

  function initFormLoading() {
    document.querySelectorAll('form[data-loading]').forEach(function (form) {
      var btn = form.querySelector('[type="submit"]');
      if (!btn) return;
      form.addEventListener('submit', function () {
        if (btn.disabled) return;
        btn.disabled = true;
        btn.dataset.originalHtml = btn.innerHTML;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Loading...';
      });
      window.addEventListener('pageshow', function () {
        if (btn.dataset.originalHtml) {
          btn.innerHTML = btn.dataset.originalHtml;
          btn.disabled = false;
        }
      });
    });
  }

  function setActiveNavLink() {
    var path = window.location.pathname;
    document.querySelectorAll('.navbar-bms .nav-link').forEach(function (link) {
      var href = link.getAttribute('href');
      if (!href || href === '#') return;
      if (path === href || (href !== '/' && path.startsWith(href))) {
        link.classList.add('active');
      }
    });
  }

  function initFitText() {
    document.querySelectorAll('[data-fit-text]').forEach(function (el) {
      fitText(el);
    });
    window.addEventListener('resize', function () {
      document.querySelectorAll('[data-fit-text]').forEach(function (el) {
        fitText(el);
      });
    }, { passive: true });
  }

  function initImageFallback() {
    var PLACEHOLDER = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600">' +
      '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="#1f2838"/><stop offset="1" stop-color="#0a0e14"/>' +
      '</linearGradient></defs>' +
      '<rect width="400" height="600" fill="url(#g)"/>' +
      '<g fill="none" stroke="#3b4a63" stroke-width="14" opacity="0.7">' +
      '<rect x="96" y="150" width="208" height="52" rx="10"/>' +
      '<rect x="96" y="234" width="208" height="52" rx="10"/>' +
      '<rect x="96" y="318" width="208" height="52" rx="10"/>' +
      '</g>' +
      '<circle cx="200" cy="300" r="0"/>' +
      '</svg>'
    );
    document.addEventListener('error', function (e) {
      var img = e.target;
      if (!img || img.tagName !== 'IMG' || img.dataset.fallbackApplied) return;
      img.dataset.fallbackApplied = '1';
      img.src = PLACEHOLDER;
    }, true);
  }

  function fitText(el) {
    var base = parseFloat(el.dataset.baseSize);
    if (!base) {
      base = parseFloat(window.getComputedStyle(el).fontSize);
      if (!base) return;
      el.dataset.baseSize = base;
    }
    var min = 11;
    var elWidth = el.clientWidth;
    if (!elWidth) return;
    el.style.fontSize = base + 'px';
    while (el.scrollWidth > elWidth && base > min) {
      base -= 0.5;
      el.style.fontSize = base + 'px';
    }
  }
})();
