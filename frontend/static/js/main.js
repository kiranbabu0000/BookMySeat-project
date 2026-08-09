/**
 * BookMySeat — Global interactions
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    initNavbarScroll();
    initPasswordToggles();
    initFormLoading();
    setActiveNavLink();
    initFitText();
    initImageFallback();
  });

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
