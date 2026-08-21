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
     Cinematic Intro — "LIGHTS. CAMERA. BOOKMYSEAT."
     Clapperboard entrance → realistic clap → logo reveal → FLIP.
     Two spotlights, dust burst, sequential tagline. ~3.8 s.
     ============================================================ */
  function initCinematicIntro() {
    var INTRO_KEY = 'bms-intro-seen';
    var overlay  = document.getElementById('bmsIntroOverlay');
    var lockup   = document.getElementById('bmsIntroLockup');
    var brand    = document.getElementById('bmsIntroBrand');
    var dustBox  = document.getElementById('bmsIntroDust');
    var clapEl   = document.getElementById('bmsIntroClap');
    var flashEl  = document.getElementById('bmsIntroFlash');

    if (!overlay || !lockup) { removeIntroEl(); return; }

    try {
      if (sessionStorage.getItem(INTRO_KEY)) {
        console.log('[BMS Intro] Skipped — already seen this session');
        removeIntroEl();
        triggerMicroSweep();
        return;
      }
    } catch (e) {}

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      console.log('[BMS Intro] Skipped — prefers-reduced-motion');
      showReducedMotionIntro();
      return;
    }

    document.body.classList.add('bms-intro-active');
    var failsafe = setTimeout(function () { dismissIntro(); }, 5000);
    var audioCtx = null;

    createDust(dustBox);

    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        runSequence();
      });
    });

    /* ----------------------------------------------------------
       CINEMATIC TIMELINE (~3.8 s total):

       0.00s — Overlay fades in
       0.20s — Letterbox bars expand from edges
       0.50s — Spotlights gradually activate
       0.50s — Clapperboard enters (3D perspective + scale)
       0.60s — Arm starts opening (hinged rotation)
       1.00s — Arm fully open (board visible with open arm)
       1.30s — CLAP! Arm snaps shut + flash + shake + audio + dust burst
       1.40s — Clapperboard fades out (downward + blur)
       1.70s — Logo reveal (light streak + brightness)
       2.10s — Brand name appears (tracking cinch)
       2.30s — Tagline lines stagger in (YOUR SHOW → YOUR SEAT → YOUR EXPERIENCE)
       3.00s — FLIP lockup to navbar (0.9s transition)
       3.90s — Cleanup
       ---------------------------------------------------------- */
    function runSequence() {
      overlay.classList.add('is-active');
      console.log('[BMS Intro] is-active added — sequence running');

      var sweep = overlay.querySelector('.bms-intro-sweep');

      /* CLAP — arm snaps shut + flash + shake + audio + dust burst */
      setTimeout(function () {
        console.log('[BMS Intro] CLAP at ' + Date.now());
        if (clapEl) clapEl.classList.add('is-clapping');
        if (flashEl) flashEl.classList.add('is-flashing');
        overlay.classList.add('is-shaking');
        playClapSound();
        createDustBurst(overlay);
      }, 1300);

      /* Board fades out (downward + blur) — delayed so clap is visible */
      setTimeout(function () {
        if (clapEl) clapEl.classList.add('is-done');
      }, 1800);

      /* Logo + brand reveal (CSS delay handles sequential tagline) */
      setTimeout(function () {
        overlay.classList.add('is-revealing');
      }, 2100);

      /* Cinematic sweep */
      setTimeout(function () {
        if (sweep) sweep.classList.add('is-sweeping');
      }, 2800);

      /* FLIP to navbar */
      setTimeout(function () {
        animateToNavbar();
      }, 3200);
    }

    /* ----------------------------------------------------------
       playClapSound — Web Audio synthesis of a realistic clap.
       Two layers: high-freq noise crack + low resonant thump.
       ---------------------------------------------------------- */
    function playClapSound() {
      try {
        if (!audioCtx) {
          var AC = window.AudioContext || window.webkitAudioContext;
          if (AC) audioCtx = new AC();
        }
        if (!audioCtx) return;
        if (audioCtx.state === 'suspended') audioCtx.resume();

        var now = audioCtx.currentTime;

        /* High-frequency crack (noise burst through bandpass) */
        var bufLen = audioCtx.sampleRate * 0.06;
        var buf = audioCtx.createBuffer(1, bufLen, audioCtx.sampleRate);
        var data = buf.getChannelData(0);
        for (var i = 0; i < bufLen; i++) {
          data[i] = Math.random() * 2 - 1;
        }
        var noise = audioCtx.createBufferSource();
        noise.buffer = buf;

        var bandpass = audioCtx.createBiquadFilter();
        bandpass.type = 'bandpass';
        bandpass.frequency.value = 2200;
        bandpass.Q.value = 1.2;

        var noiseGain = audioCtx.createGain();
        noiseGain.gain.setValueAtTime(0.35, now);
        noiseGain.gain.exponentialRampToValueAtTime(0.001, now + 0.06);

        noise.connect(bandpass);
        bandpass.connect(noiseGain);
        noiseGain.connect(audioCtx.destination);
        noise.start(now);
        noise.stop(now + 0.06);

        /* Low resonant thump (body of the clap) */
        var osc = audioCtx.createOscillator();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(250, now);
        osc.frequency.exponentialRampToValueAtTime(80, now + 0.07);

        var oscGain = audioCtx.createGain();
        oscGain.gain.setValueAtTime(0.22, now);
        oscGain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);

        osc.connect(oscGain);
        oscGain.connect(audioCtx.destination);
        osc.start(now);
        osc.stop(now + 0.08);
      } catch (e) {}
    }

    function createDust(container) {
      if (!container) return;
      var COUNT = 14;
      for (var i = 0; i < COUNT; i++) {
        var dot = document.createElement('div');
        dot.className = 'bms-intro-dust-particle';
        var sz = 1 + Math.random() * 2;
        dot.style.width  = sz + 'px';
        dot.style.height = sz + 'px';
        dot.style.left   = (25 + Math.random() * 50) + '%';
        dot.style.top    = (15 + Math.random() * 70) + '%';
        dot.style.setProperty('--dust-dx', (Math.random() * 20 - 10) + 'px');
        dot.style.setProperty('--dust-dy', (-30 - Math.random() * 50) + 'px');
        dot.style.setProperty('--dust-op', (0.1 + Math.random() * 0.2).toFixed(2));
        dot.style.animationDuration = (5 + Math.random() * 7) + 's';
        dot.style.animationDelay    = (-Math.random() * 8) + 's';
        container.appendChild(dot);
      }
    }

    /* createDustBurst — burst of small particles radiating from centre on clap */
    function createDustBurst(container) {
      if (!container) return;
      var burst = document.createElement('div');
      burst.className = 'bms-intro-dust-burst';
      var PARTICLE_COUNT = 8;
      for (var i = 0; i < PARTICLE_COUNT; i++) {
        var p = document.createElement('div');
        p.className = 'bms-dust-burst-particle';
        var angle = (i / PARTICLE_COUNT) * 360 + (Math.random() * 30 - 15);
        var dist  = 30 + Math.random() * 50;
        var rad   = angle * Math.PI / 180;
        var dx    = Math.cos(rad) * dist;
        var dy    = Math.sin(rad) * dist;
        var size  = 1.5 + Math.random() * 2.5;
        p.style.width  = size + 'px';
        p.style.height = size + 'px';
        p.style.setProperty('--burst-dx', dx.toFixed(1) + 'px');
        p.style.setProperty('--burst-dy', dy.toFixed(1) + 'px');
        p.style.setProperty('--burst-dur', (0.35 + Math.random() * 0.3).toFixed(2) + 's');
        burst.appendChild(p);
      }
      container.appendChild(burst);
      setTimeout(function () { burst.remove(); }, 900);
    }

    function animateToNavbar() {
      var navbarBrand = document.querySelector('.navbar-bms .navbar-brand');
      if (!navbarBrand) { dismissIntro(); return; }
      var target = navbarBrand.getBoundingClientRect();
      var source = lockup.getBoundingClientRect();
      var deltaX = target.left - source.left;
      var deltaY = target.top  - source.top;
      var navbarImg   = navbarBrand.querySelector('.brand-logo');
      var targetImgH  = navbarImg ? navbarImg.offsetHeight : 44;
      var sourceImgH  = source.height;
      var finalScale  = targetImgH / sourceImgH;
      overlay.classList.add('is-exiting');
      lockup.style.willChange = 'transform, opacity';
      lockup.classList.add('is-animating');
      requestAnimationFrame(function () {
        lockup.style.transform = 'translate(' + deltaX + 'px, ' + deltaY + 'px) scale(' + finalScale + ')';
        lockup.style.opacity   = '0';
      });
      overlay.classList.add('is-fading');
      document.body.classList.remove('bms-intro-active');
      document.body.classList.add('bms-intro-revealing');
      lockup.addEventListener('transitionend', function onFlipDone(e) {
        if (e.propertyName !== 'transform') return;
        lockup.removeEventListener('transitionend', onFlipDone);
        setTimeout(dismissIntro, 60);
      });
    }

    function dismissIntro() {
      clearTimeout(failsafe);
      if (!overlay || overlay.dataset.dismissed) return;
      overlay.dataset.dismissed = '1';
      document.body.classList.remove('bms-intro-active');
      document.body.classList.remove('bms-intro-revealing');
      lockup.style.willChange = '';
      try { sessionStorage.setItem(INTRO_KEY, '1'); } catch (e) {}
      overlay.remove();
    }

    function showReducedMotionIntro() {
      document.body.classList.add('bms-intro-active');
      overlay.classList.add('is-active');
      var rf = setTimeout(function () {
        overlay.remove();
        document.body.classList.remove('bms-intro-active');
        try { sessionStorage.setItem(INTRO_KEY, '1'); } catch (e) {}
      }, 1400);
      setTimeout(function () {
        clearTimeout(rf);
        overlay.classList.add('is-fading');
        document.body.classList.remove('bms-intro-active');
        try { sessionStorage.setItem(INTRO_KEY, '1'); } catch (e) {}
        setTimeout(function () { overlay.remove(); }, 700);
      }, 700);
    }

    function triggerMicroSweep() {
      var nb = document.querySelector('.navbar-bms .navbar-brand');
      if (!nb) return;
      nb.classList.add('bms-intro-micro-sweep');
      setTimeout(function () { nb.classList.remove('bms-intro-micro-sweep'); }, 1500);
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
