(function () {
  'use strict';

  var STORAGE_KEY = 'bookmyseat-theme';
  var root = document.documentElement;

  function getPreferredTheme() {
    var stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(theme, persist) {
    root.setAttribute('data-theme', theme);
    if (persist !== false) {
      localStorage.setItem(STORAGE_KEY, theme);
    }
    updateToggleIcons(theme);
  }

  function updateToggleIcons(theme) {
    var isDark = theme === 'dark';
    var iconClass = isDark ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
    var label = isDark ? 'Switch to light mode' : 'Switch to dark mode';
    document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
      var icon = btn.querySelector('i');
      if (icon) icon.className = iconClass;
      btn.setAttribute('aria-label', label);
    });
    var themeIcon = document.getElementById('themeIcon');
    if (themeIcon) themeIcon.className = iconClass;
  }

  window.toggleTheme = function (btn) {
    var current = root.getAttribute('data-theme') || getPreferredTheme();
    var next = current === 'dark' ? 'light' : 'dark';
    animateThemeChange(next, btn);
  };

  function pulseIcon(btn) {
    var icon = btn.querySelector('i');
    if (!icon) return;
    icon.classList.remove('theme-icon--spin');
    void icon.offsetWidth; /* restart the animation */
    icon.classList.add('theme-icon--spin');
  }

  function animateThemeChange(next, btn) {
    var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (document.startViewTransition && !reduced) {
      root.classList.add('theme-switching');
      try {
        var t = document.startViewTransition(function () {
          applyTheme(next);
        });
        var finish = function () { root.classList.remove('theme-switching'); };
        if (t && t.finished) {
          t.finished.then(finish).catch(finish);
        } else {
          finish();
        }
      } catch (err) {
        root.classList.remove('theme-switching');
        applyTheme(next);
      }
    } else {
      applyTheme(next);
    }
    if (btn) pulseIcon(btn);
  }

  applyTheme(getPreferredTheme(), false);

  var mq = window.matchMedia('(prefers-color-scheme: dark)');
  var onSystemChange = function (e) {
    if (!localStorage.getItem(STORAGE_KEY)) {
      applyTheme(e.matches ? 'dark' : 'light', false);
    }
  };
  if (mq.addEventListener) {
    mq.addEventListener('change', onSystemChange);
  } else if (mq.addListener) {
    mq.addListener(onSystemChange);
  }

  document.addEventListener('DOMContentLoaded', function () {
    updateToggleIcons(root.getAttribute('data-theme') || getPreferredTheme());
    document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        root.style.setProperty('--wave-x', e.clientX + 'px');
        root.style.setProperty('--wave-y', e.clientY + 'px');
        window.toggleTheme(btn);
      });
    });
  });
})();
