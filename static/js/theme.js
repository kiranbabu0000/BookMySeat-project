(function () {
  'use strict';

  var STORAGE_KEY = 'bookmyseat-theme';
  var root = document.documentElement;

  function getPreferredTheme() {
    var stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    root.setAttribute('data-theme', theme);
    localStorage.setItem(STORAGE_KEY, theme);
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

  window.toggleTheme = function () {
    var current = root.getAttribute('data-theme') || getPreferredTheme();
    applyTheme(current === 'dark' ? 'light' : 'dark');
  };

  applyTheme(getPreferredTheme());

  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) {
    if (!localStorage.getItem(STORAGE_KEY)) {
      applyTheme(e.matches ? 'dark' : 'light');
    }
  });

  document.addEventListener('DOMContentLoaded', function () {
    updateToggleIcons(root.getAttribute('data-theme') || getPreferredTheme());
    document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
      btn.addEventListener('click', window.toggleTheme);
    });
  });
})();
