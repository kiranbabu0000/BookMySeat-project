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
     Cinematic Intro — Premium brand opening animation
     Event-driven: brand reveal completion triggers FLIP immediately.
     Total duration: ~2.5 seconds. No artificial pauses.
     Uses sessionStorage so it plays once per new session only
     (not on refresh or internal navigation).
     ============================================================ */
  function initCinematicIntro() {
    var INTRO_KEY = 'bms-intro-seen';
    var overlay  = document.getElementById('bmsIntroOverlay');
    var lockup   = document.getElementById('bmsIntroLockup');
    var brand    = document.getElementById('bmsIntroBrand');
    var tagline  = document.getElementById('bmsIntroTagline');

    // Guard: skip if any element missing
    if (!overlay || !lockup) { removeIntroEl(); return; }

    // Skip if already seen in this session (covers refresh + internal nav)
    try { if (sessionStorage.getItem(INTRO_KEY)) { removeIntroEl(); return; } } catch (e) {}

    // Skip if reduced motion preferred — show a brief simple fade instead
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      showReducedMotionIntro();
      return;
    }

    // Hide page elements behind the overlay
    document.body.classList.add('bms-intro-active');

    // Failsafe — force-dismiss after 5 seconds no matter what
    var failsafe = setTimeout(function () { dismissIntro(); }, 5000);

    // Collect page elements that will be revealed during Phase 5
    var revealEls = [
      document.querySelector('.navbar-bms'),
      document.querySelector('main'),
      document.querySelector('.footer-bms'),
      document.querySelector('.bottom-nav')
    ].filter(Boolean);

    // Start the sequence after a frame so overlay is painted
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        runSequence();
      });
    });

    /* ----------------------------------------------------------
       PHASE TIMELINE (event-driven, ~2.5s total):
       0.00s — Overlay fades in (dark cinematic background)
       0.45s — Logo image reveals (fade + scale + brightness)
       0.80s — Light sweep crosses logo
       1.20s — Brand text fades in below logo
       1.60s — Tagline fades in
       ~1.8s — Brand text reveal completes → IMMEDIATELY trigger FLIP
       1.8–2.5s — Logo moves to navbar + overlay fades + page reveals
       ~2.5s — Cleanup, overlay removed
       ---------------------------------------------------------- */
    function runSequence() {
      // Phase 1: Dark overlay appears (0ms)
      overlay.classList.add('is-active');

      // Phase 2: Light sweep across logo (0.80s)
      setTimeout(function () {
        var sweep = overlay.querySelector('.bms-intro-sweep');
        if (sweep) sweep.classList.add('is-sweeping');
      }, 800);

      // Phase 5: When brand text reveal completes, immediately begin FLIP
      // Brand CSS: transition-delay 1.2s + duration 0.6s → finishes ~1.8s
      // Primary: transitionend event (event-driven, instant trigger)
      // Fallback: setTimeout at 2000ms if transitionend doesn't fire
      // (known browser edge case where transitionend can be swallowed)
      var brandRevealed = false;

      function startFlip() {
        if (brandRevealed) return;
        brandRevealed = true;
        clearTimeout(brandFallback);
        brand.removeEventListener('transitionend', onBrandTransition);
        animateToNavbar();
      }

      function onBrandTransition(e) {
        if (e.propertyName === 'opacity' || e.propertyName === 'transform') {
          startFlip();
        }
      }

      brand.addEventListener('transitionend', onBrandTransition);

      // Safety net: CSS brand transition = 1.2s delay + 0.6s duration = 1.8s
      var brandFallback = setTimeout(startFlip, 2000);
    }

    function animateToNavbar() {
      // Find the real navbar brand element
      var navbarBrand = document.querySelector('.navbar-bms .navbar-brand');
      if (!navbarBrand) { dismissIntro(); return; }

      // Get target position (real navbar brand)
      var target = navbarBrand.getBoundingClientRect();

      // Get current lockup position (center of screen)
      var source = lockup.getBoundingClientRect();

      // Calculate FLIP delta
      var deltaX = target.left - source.left;
      var deltaY = target.top - source.top;

      // Scale: match the navbar brand image height
      var navbarImg = navbarBrand.querySelector('.brand-logo');
      var targetImgH = navbarImg ? navbarImg.offsetHeight : 44;
      var sourceImgH = source.height;
      var finalScale = targetImgH / sourceImgH;

      // Add animating class for CSS transition
      lockup.classList.add('is-animating');

      // Apply FLIP transform on next frame — fade lockup fully to 0 for clean handoff
      requestAnimationFrame(function () {
        lockup.style.transform = 'translate(' + deltaX + 'px, ' + deltaY + 'px) scale(' + finalScale + ')';
        lockup.style.opacity = '0';
      });

      // Simultaneously: fade overlay + reveal page while logo is in transit
      overlay.classList.add('is-fading');
      document.body.classList.remove('bms-intro-active');

      revealEls.forEach(function (el) {
        el.style.opacity = '0';
        el.style.pointerEvents = 'none';
      });

      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          revealEls.forEach(function (el, i) {
            var delay = i === 0 ? '0s' : (i === 1 ? '0.15s' : '0.3s');
            el.style.transition = 'opacity 0.8s cubic-bezier(0.4, 0, 0.2, 1) ' + delay;
            el.style.opacity = '1';
            el.style.pointerEvents = '';
          });
        });
      });

      // Cleanup when FLIP animation finishes (event-driven)
      lockup.addEventListener('transitionend', function onFlipDone(e) {
        if (e.propertyName !== 'transform') return;
        lockup.removeEventListener('transitionend', onFlipDone);
        // Small delay to let overlay finish fading, then clean up
        setTimeout(dismissIntro, 100);
      });
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

      // Mark intro as seen for this session
      try { sessionStorage.setItem(INTRO_KEY, '1'); } catch (e) {}

      // Remove overlay from DOM
      overlay.remove();
    }

    function showReducedMotionIntro() {
      // For users who prefer reduced motion: brief 0.6s fade-in/out
      document.body.classList.add('bms-intro-active');
      overlay.classList.add('is-active');

      var reducedFailsafe = setTimeout(function () {
        overlay.remove();
        document.body.classList.remove('bms-intro-active');
        try { sessionStorage.setItem(INTRO_KEY, '1'); } catch (e) {}
      }, 1200);

      setTimeout(function () {
        clearTimeout(reducedFailsafe);
        overlay.classList.add('is-fading');
        document.body.classList.remove('bms-intro-active');
        try { sessionStorage.setItem(INTRO_KEY, '1'); } catch (e) {}
        setTimeout(function () { overlay.remove(); }, 700);
      }, 600);
    }

    function removeIntroEl() {
      var el = document.getElementById('bmsIntroOverlay');
      if (el) el.remove();
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
