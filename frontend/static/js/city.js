/* BookMySeat — city selector: geolocation, persistence and page sync. */
(function () {
  'use strict';

  var STORAGE_KEY = 'bms_city';
  var COOKIE_NAME = 'bms_city';
  var COOKIE_DAYS = 7;
  var RELOAD_GUARD = 'bms_city_geo_reloaded';

  /* Approximate city coordinates for offline nearest-city detection.
     Keep in sync with seeded theatre cities. */
  var CITY_COORDS = {
    'Chennai': [13.0827, 80.2707],
    'Mumbai': [19.0760, 72.8777],
    'Delhi': [28.6139, 77.2090],
    'Bengaluru': [12.9716, 77.5946],
    'Hyderabad': [17.3850, 78.4867],
    'Kolkata': [22.5726, 88.3639],
    'Pune': [18.5204, 73.8567],
  };
  var GEO_MAX_KM = 300;
  var MANUAL_KEY = 'bms_city_manual';

  /* Common city-name variants so reverse geocoding results match the seeded
     cities even when the geocoder returns an alias (Bangalore vs Bengaluru). */
  var CITY_ALIASES = {
    'bangalore': 'Bengaluru',
    'calcutta': 'Kolkata',
    'bombay': 'Mumbai',
    'madras': 'Chennai',
    'new delhi': 'Delhi',
    'delhi ncr': 'Delhi',
  };

  function getCities() {
    return Array.prototype.map.call(
      document.querySelectorAll('.city-selector__item[data-city]'),
      function (el) { return el.getAttribute('data-city'); }
    );
  }

  function getStoredCity() {
    var c = '';
    try { c = localStorage.getItem(STORAGE_KEY) || ''; } catch (e) {}
    if (c) return c;
    var match = document.cookie.match(/(?:^|;\s*)bms_city=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function setStoredCity(city) {
    try { localStorage.setItem(STORAGE_KEY, city); } catch (e) {}
    var expires = new Date();
    expires.setDate(expires.getDate() + COOKIE_DAYS);
    document.cookie = COOKIE_NAME + '=' + encodeURIComponent(city) +
      '; expires=' + expires.toUTCString() + '; path=/; SameSite=Lax';
  }

  function hasExplicitCityParam() {
    return new URLSearchParams(window.location.search).has('city');
  }

  function updateLabel(city) {
    var label = document.querySelector('[data-city-label]');
    if (label) label.textContent = city;
    var items = document.querySelectorAll('.city-selector__item[data-city]');
    items.forEach(function (el) {
      var active = el.getAttribute('data-city') === city;
      el.classList.toggle('is-active', active);
      var check = el.querySelector('.city-selector__check');
      if (active && !check) {
        var icon = document.createElement('i');
        icon.className = 'bi bi-check-lg city-selector__check';
        icon.setAttribute('aria-hidden', 'true');
        el.appendChild(icon);
      } else if (!active && check) {
        check.remove();
      }
    });
  }

  function setDropdownOpen(open) {
    var btn = document.getElementById('citySelectorBtn');
    var root = document.getElementById('citySelector');
    var menu = root ? root.querySelector('.dropdown-menu') : null;
    if (!btn || !menu) return;
    btn.classList.toggle('show', open);
    menu.classList.toggle('show', open);
    menu.style.display = open ? 'block' : 'none';
    btn.setAttribute('aria-expanded', String(open));
  }

  function closeDropdown() {
    setDropdownOpen(false);
  }

  function toggleDropdown() {
    var btn = document.getElementById('citySelectorBtn');
    if (!btn) return;
    var isOpen = btn.getAttribute('aria-expanded') === 'true';
    setDropdownOpen(!isOpen);
  }

  function selectCity(city, options) {
    options = options || {};
    if (!city) return;
    setStoredCity(city);
    updateLabel(city);

    if (document.getElementById('discoveryForm')) {
      var sel = document.getElementById('citySelect');
      if (sel) sel.value = city;
      if (options.navigate) {
        var form = document.getElementById('discoveryForm');
        if (form.requestSubmit) form.requestSubmit();
        else form.submit();
      }
      return;
    }
    if (document.getElementById('theaterResults')) {
      if (options.navigate || options.guardedReload) {
        var url = new URL(window.location.href);
        url.searchParams.set('city', city);
        window.location.href = url.toString();
      }
      return;
    }
    if (options.navigate) {
      var root = document.getElementById('citySelector');
      var base = root ? root.getAttribute('data-movies-url') || '/movies/' : '/movies/';
      window.location.href = base + '?city=' + encodeURIComponent(city);
    }
  }

  function nearestCity(lat, lng) {
    var best = null;
    var bestKm = Infinity;
    for (var name in CITY_COORDS) {
      if (!Object.prototype.hasOwnProperty.call(CITY_COORDS, name)) continue;
      var km = haversineKm(lat, lng, CITY_COORDS[name][0], CITY_COORDS[name][1]);
      if (km < bestKm) {
        bestKm = km;
        best = name;
      }
    }
    return bestKm <= GEO_MAX_KM ? best : null;
  }

  function haversineKm(lat1, lng1, lat2, lng2) {
    var R = 6371;
    var dLat = (lat2 - lat1) * Math.PI / 180;
    var dLng = (lng2 - lng1) * Math.PI / 180;
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
      Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function knownCities() {
    var cities = getCities();
    return cities.length ? cities : Object.keys(CITY_COORDS);
  }

  /* Match a reverse-geocoded place name against the cities we actually serve,
     resolving common aliases. Returns the canonical city name or null. */
  function normalizeCity(raw) {
    if (!raw) return null;
    var cleaned = String(raw).trim().replace(/\s+/g, ' ');
    if (!cleaned) return null;
    if (CITY_ALIASES[cleaned.toLowerCase()]) cleaned = CITY_ALIASES[cleaned.toLowerCase()];
    var cities = knownCities();
    for (var i = 0; i < cities.length; i++) {
      if (cities[i].toLowerCase() === cleaned.toLowerCase()) return cities[i];
    }
    return null;
  }

  /* Real reverse geocoding via OpenStreetMap Nominatim (free, no API key).
     Falls back to offline nearest-city matching when the network is unavailable. */
  function reverseGeocode(lat, lng, cb) {
    if (typeof fetch !== 'function') { cb(null); return; }
    var url = 'https://nominatim.openstreetmap.org/reverse' +
      '?format=jsonv2&zoom=12&accept-language=en&lat=' + encodeURIComponent(lat) +
      '&lon=' + encodeURIComponent(lng);
    var controller = null;
    if (typeof AbortController === 'function') {
      controller = new AbortController();
      setTimeout(function () { controller.abort(); }, 8000);
    }
    fetch(url, { signal: controller ? controller.signal : undefined })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.address) { cb(null); return; }
        var a = data.address;
        var city = a.city || a.town || a.village || a.municipality ||
          a.state_district || a.county || a.state || '';
        cb(city || null);
      })
      .catch(function () { cb(null); });
  }

  function detectViaGeolocation(cb) {
    if (!navigator.geolocation) { cb(null); return; }
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        var lat = pos.coords.latitude;
        var lng = pos.coords.longitude;
        reverseGeocode(lat, lng, function (named) {
          cb(normalizeCity(named) || nearestCity(lat, lng));
        });
      },
      function () { cb(null); },
      { timeout: 8000, maximumAge: 600000 }
    );
  }

  function init() {
    var stored = getStoredCity();
    var cities = getCities();
    var manual = false;
    try { manual = localStorage.getItem(MANUAL_KEY) === '1'; } catch (e) {}
    if (manual) {
      if (stored) {
        updateLabel(stored);
        var sel = document.getElementById('citySelect');
        if (sel && !sel.value) sel.value = stored;
      }
      return;
    }
    if (stored) {
      if (cities.indexOf(stored) !== -1) {
        updateLabel(stored);
        if (document.getElementById('discoveryForm')) {
          var sel = document.getElementById('citySelect');
          if (sel && !sel.value) sel.value = stored;
        }
      }
      return;
    }
    if (!navigator.geolocation) return;
    var guarded = false;
    try { guarded = sessionStorage.getItem(RELOAD_GUARD) === '1'; } catch (e) {}
    detectViaGeolocation(function (city) {
      if (!city || cities.indexOf(city) === -1) return;
      setStoredCity(city);
      updateLabel(city);
      /* First visit on the discovery / theater pages: reflect the detected
         city server-side once (guarded so we never loop). */
      var onListPage = document.getElementById('discoveryForm') ||
        document.getElementById('theaterResults');
      if (onListPage && !guarded && !hasExplicitCityParam()) {
        try { sessionStorage.setItem(RELOAD_GUARD, '1'); } catch (e) {}
        selectCity(city, { navigate: true });
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var root = document.getElementById('citySelector');
    if (!root) return;

    var btn = document.getElementById('citySelectorBtn');
    if (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        toggleDropdown();
      });
    }

    document.addEventListener('click', function (e) {
      var item = e.target.closest('.city-selector__item[data-city]');
      if (!item) return;
      e.preventDefault();
      e.stopPropagation();
      closeDropdown();
      try { localStorage.setItem(MANUAL_KEY, '1'); } catch (e2) {}
      selectCity(item.getAttribute('data-city'), { navigate: true });
    });
    document.addEventListener('click', function (e) {
      var btn = document.getElementById('citySelectorBtn');
      var menu = root.querySelector('.dropdown-menu');
      if (!btn || !menu) return;
      if (btn.contains(e.target) || menu.contains(e.target)) return;
      if (!root.contains(e.target)) closeDropdown();
    });

    init();
  });
})();
