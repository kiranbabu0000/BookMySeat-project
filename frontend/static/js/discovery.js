/**
 * BookMySeat — Movie Discovery
 * AJAX search, filters, sorting and pagination for the movie list page.
 * Progressive enhancement: the page works as a normal GET form without JS.
 */
(function () {
  'use strict';

  var form = document.getElementById('discoveryForm');
  if (!form) return;

  var resultsEl = document.getElementById('discoveryResults');
  var loadingEl = document.getElementById('discoveryLoading');
  var searchInput = document.getElementById('movieSearchInput');
  var suggestionsEl = document.getElementById('discoverySuggestions');
  var citySelect = document.getElementById('citySelect');
  var theatreSelect = document.getElementById('theatreSelect');
  var clearButtons = document.querySelectorAll('[data-clear-filters]');

  var endpoint = form.getAttribute('action');
  var state = { page: 1, preserveScroll: false };
  var searchTimer = null;
  var suggestTimer = null;
  var filterFields = ['search', 'genre', 'language', 'city', 'theatre', 'release', 'rating', 'timing', 'date', 'price_min', 'price_max'];

  function collectParams() {
    var fd = new FormData(form);
    var out = new URLSearchParams();
    var keys = {};
    fd.forEach(function (value, key) { keys[key] = true; });
    Object.keys(keys).forEach(function (key) {
      var values = fd.getAll(key).filter(function (v) { return v.trim() !== ''; });
      values.forEach(function (v) { out.append(key, v.trim()); });
    });
    out.set('page', String(state.page));
    return out;
  }

  function hasFilters(params) {
    return filterFields.some(function (key) { return params.getAll(key).length > 0; });
  }

  function updateClearButton(params) {
    clearButtons.forEach(function (btn) {
      btn.classList.toggle('d-none', !hasFilters(params));
    });
  }

  function updateUrl(params) {
    var qs = params.toString();
    history.replaceState(null, '', endpoint + (qs ? '?' + qs : ''));
  }

  function showLoading(show) {
    loadingEl.classList.toggle('d-none', !show);
    resultsEl.classList.toggle('is-loading', show);
  }

  function refresh(params) {
    var scrollY = window.scrollY;
    showLoading(true);
    fetch(endpoint + '?' + params.toString(), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.ok) throw new Error('Bad response');
        resultsEl.innerHTML = data.html;
        showLoading(false);
        updateClearButton(params);
        updateUrl(params);
        if (state.preserveScroll) {
          window.scrollTo(0, scrollY);
        }
      })
      .catch(function () {
        showLoading(false);
      });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function highlightName(name, term) {
    var text = escapeHtml(name);
    if (!term) return text;
    var idx = name.toLowerCase().indexOf(term.toLowerCase());
    if (idx === -1) return text;
    return text.slice(0, idx) + '<mark>' + text.slice(idx, idx + term.length) + '</mark>' + text.slice(idx + term.length);
  }

  function fetchSuggestions(query) {
    if (query.length < 1) {
      suggestionsEl.classList.add('d-none');
      suggestionsEl.innerHTML = '';
      return;
    }
    fetch('/search-suggestions/?q=' + encodeURIComponent(query))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        suggestionsEl.innerHTML = '';
        if (data.length === 0) {
          suggestionsEl.innerHTML = '<div class="search-suggestions__item disabled">No movies found</div>';
        } else {
          data.forEach(function (m) {
            var a = document.createElement('a');
            a.href = m.url;
            a.className = 'search-suggestions__item';
            var span = document.createElement('span');
            span.className = 'search-suggestions__name';
            span.innerHTML = highlightName(m.name, query);
            a.appendChild(span);
            suggestionsEl.appendChild(a);
          });
        }
        suggestionsEl.classList.remove('d-none');
      })
      .catch(function () {
        suggestionsEl.classList.add('d-none');
      });
  }

  function filterTheatreOptions() {
    var selectedCity = citySelect.value.trim().toLowerCase();
    Array.prototype.forEach.call(theatreSelect.options, function (opt) {
      if (!opt.value) return;
      var optCity = (opt.getAttribute('data-city') || '').trim().toLowerCase();
      var visible = !selectedCity || optCity === selectedCity;
      opt.style.display = visible ? '' : 'none';
    });
    if (selectedCity && theatreSelect.value) {
      var opt = theatreSelect.options[theatreSelect.selectedIndex];
      if (opt && (opt.getAttribute('data-city') || '').trim().toLowerCase() !== selectedCity) {
        theatreSelect.value = '';
      }
    }
  }

  function resetFilters(keepSort) {
    var categoryInput = form.querySelector('input[name="category"]');
    var categoryValue = categoryInput ? categoryInput.value : '';
    form.querySelectorAll('input[name], select[name]').forEach(function (el) {
      if (keepSort && el.name === 'sort') return;
      if (el.type === 'checkbox') { el.checked = false; }
      else { el.value = ''; }
    });
    // "Clear All" must stay on the current category tab (Movies / events).
    if (categoryInput) { categoryInput.value = categoryValue; }
  }

  // Search-as-you-type + suggestions
  searchInput.addEventListener('input', function () {
    var value = this.value.trim();
    clearTimeout(searchTimer);
    clearTimeout(suggestTimer);
    state.page = 1;
    suggestTimer = setTimeout(function () { fetchSuggestions(value); }, 200);
    searchTimer = setTimeout(function () {
      state.preserveScroll = true;
      refresh(collectParams());
    }, 350);
  });

  // Hide suggestions on outside click / Escape
  document.addEventListener('click', function (e) {
    if (searchInput && !searchInput.contains(e.target) && !suggestionsEl.contains(e.target)) {
      suggestionsEl.classList.add('d-none');
    }
  });
  searchInput.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { suggestionsEl.classList.add('d-none'); }
  });

  // Form submit (fired by Enter, the Apply button, and requestSubmit from filters)
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    state.page = 1;
    state.preserveScroll = true;
    refresh(collectParams());
  });

  // Keep the theatre dropdown in sync with the chosen city
  if (citySelect && theatreSelect) {
    citySelect.addEventListener('change', filterTheatreOptions);
    filterTheatreOptions();
  }

  // Clear all filters
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-clear-filters]');
    if (!btn) return;
    e.preventDefault();
    resetFilters(true);
    state.page = 1;
    state.preserveScroll = true;
    refresh(collectParams());
  });

  // Pagination + chips are re-rendered after every refresh -> event delegation.
  resultsEl.addEventListener('click', function (e) {
    var pageLink = e.target.closest('.discovery-pagination a[data-page]');
    if (pageLink) {
      e.preventDefault();
      state.page = parseInt(pageLink.getAttribute('data-page'), 10) || 1;
      state.preserveScroll = false;
      refresh(collectParams());
      return;
    }

    var chip = e.target.closest('.discovery-chip');
    if (chip) {
      e.preventDefault();
      var param = chip.getAttribute('data-chip-param');
      var value = chip.getAttribute('data-chip-value');
      var control = form.querySelector('[name="' + param + '"]');
      if (param === 'search' && searchInput) {
        searchInput.value = '';
      } else if (control) {
        if (control.type === 'checkbox') { control.checked = false; }
        else { control.value = ''; }
      }
      state.page = 1;
      state.preserveScroll = true;
      refresh(collectParams());
    }
  });
})();
