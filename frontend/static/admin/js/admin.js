(function () {
  'use strict';

  function setSidebar(show) {
    const sidebar = document.getElementById('adminSidebar');
    const backdrop = document.getElementById('sidebarBackdrop');
    if (sidebar) sidebar.classList.toggle('show', show);
    if (backdrop) backdrop.classList.toggle('show', show);
  }

  function toggleSidebar() {
    const sidebar = document.getElementById('adminSidebar');
    if (sidebar) {
      setSidebar(!sidebar.classList.contains('show'));
    }
  }

  function closeSidebar() {
    if (window.innerWidth < 992) {
      setSidebar(false);
    }
  }

  window.toggleSidebar = toggleSidebar;
  window.closeSidebar = closeSidebar;

  function handleClickOutsideSidebar(e) {
    const sidebar = document.getElementById('adminSidebar');
    const toggleBtn = document.querySelector('.sidebar-toggle');
    if (
      sidebar &&
      sidebar.classList.contains('show') &&
      !sidebar.contains(e.target) &&
      toggleBtn &&
      !toggleBtn.contains(e.target)
    ) {
      closeSidebar();
    }
  }

  function initSidebar() {
    const toggleBtn = document.querySelector('.sidebar-toggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', toggleSidebar);
    }

    const sidebarLinks = document.querySelectorAll('.sidebar-link');
    sidebarLinks.forEach(function (link) {
      link.addEventListener('click', function () {
        if (window.innerWidth < 992) {
          closeSidebar();
        }
      });
    });

    document.addEventListener('click', handleClickOutsideSidebar);

    window.addEventListener('resize', function () {
      if (window.innerWidth >= 992) {
        setSidebar(false);
      }
    });
  }


  function initTooltips() {
    const tooltips = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    if (typeof bootstrap !== 'undefined' && bootstrap.Tooltip) {
      tooltips.forEach(function (el) {
        new bootstrap.Tooltip(el);
      });
    }
  }

  function initPopovers() {
    const popovers = document.querySelectorAll('[data-bs-toggle="popover"]');
    if (typeof bootstrap !== 'undefined' && bootstrap.Popover) {
      popovers.forEach(function (el) {
        new bootstrap.Popover(el);
      });
    }
  }

  function initNotificationPolling() {
    const badge = document.querySelector('.notification-badge');
    if (!badge) return;

    function updateBadge(count) {
      var badgeEl = badge.querySelector('.badge');
      if (!badgeEl) {
        badgeEl = document.createElement('span');
        badgeEl.className = 'badge rounded-pill bg-danger';
        badge.appendChild(badgeEl);
      }
      if (count > 0) {
        badgeEl.textContent = count > 99 ? '99+' : count;
        badgeEl.style.display = '';
      } else {
        badgeEl.style.display = 'none';
      }
    }

    function fetchUnreadCount() {
      fetch('/notifications/unread-count/')
        .then(function (res) {
          if (!res.ok) throw new Error('Network error');
          return res.json();
        })
        .then(function (data) {
          if (typeof data.count === 'number') {
            updateBadge(data.count);
          }
        })
        .catch(function () {});
    }

    fetchUnreadCount();
    setInterval(fetchUnreadCount, 30000);
  }

  function initAlertAutoHide() {
    var alerts = document.querySelectorAll('.alert:not(.alert-permanent)');
    alerts.forEach(function (alert) {
      setTimeout(function () {
        if (alert.parentNode) {
          alert.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
          alert.style.opacity = '0';
          alert.style.transform = 'translateY(-8px)';
          setTimeout(function () {
            if (alert.parentNode) {
              alert.remove();
            }
          }, 300);
        }
      }, 5000);
    });
  }

  function initConfirmDialogs() {
    document.addEventListener('click', function (e) {
      var trigger = e.target.closest('[data-confirm]');
      if (!trigger) return;

      var message = trigger.getAttribute('data-confirm') || 'Are you sure you want to proceed?';
      if (!confirm(message)) {
        e.preventDefault();
        e.stopPropagation();
        return false;
      }
    });
  }

  function initCharts() {
    window.BMS = window.BMS || {};

    window.BMS.chart = {
      createLineChart: function (canvasId, config) {
        var canvas = document.getElementById(canvasId);
        if (!canvas || typeof Chart === 'undefined') return null;

        var ctx = canvas.getContext('2d');
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        var gridColor = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(15,23,42,0.06)';
        var textColor = isDark ? '#94a3b8' : '#64748b';

        return new Chart(ctx, {
          type: 'line',
          data: {
            labels: config.labels || [],
            datasets: (config.datasets || []).map(function (ds) {
              return {
                label: ds.label || '',
                data: ds.data || [],
                borderColor: ds.borderColor || '#dc2626',
                backgroundColor: ds.backgroundColor || 'rgba(220,38,38,0.08)',
                borderWidth: ds.borderWidth || 2.5,
                pointRadius: ds.pointRadius || 3,
                pointBackgroundColor: ds.pointBackgroundColor || '#dc2626',
                pointBorderColor: '#fff',
                pointBorderWidth: 1.5,
                tension: ds.tension || 0.35,
                fill: ds.fill !== undefined ? ds.fill : true,
              };
            }),
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                display: config.showLegend !== undefined ? config.showLegend : true,
                position: 'bottom',
                labels: {
                  color: textColor,
                  font: { family: "'Inter', system-ui, sans-serif", size: 12 },
                  padding: 16,
                  usePointStyle: true,
                  pointStyle: 'circle',
                },
              },
              tooltip: {
                backgroundColor: isDark ? '#1e1e1e' : '#fff',
                titleColor: isDark ? '#f8fafc' : '#0f172a',
                bodyColor: isDark ? '#cbd5e1' : '#475569',
                borderColor: isDark ? '#2a2a2a' : '#e2e8f0',
                borderWidth: 1,
                padding: 12,
                cornerRadius: 8,
                titleFont: { weight: '600' },
                bodyFont: { size: 13 },
              },
            },
            scales: {
              x: {
                grid: { color: gridColor, drawBorder: false },
                ticks: { color: textColor, font: { size: 11 } },
              },
              y: {
                grid: { color: gridColor, drawBorder: false },
                ticks: { color: textColor, font: { size: 11 } },
                beginAtZero: config.beginAtZero !== undefined ? config.beginAtZero : true,
              },
            },
            interaction: {
              intersect: false,
              mode: 'index',
            },
          },
        });
      },

      createDoughnutChart: function (canvasId, config) {
        var canvas = document.getElementById(canvasId);
        if (!canvas || typeof Chart === 'undefined') return null;

        var ctx = canvas.getContext('2d');
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        var textColor = isDark ? '#94a3b8' : '#64748b';

        return new Chart(ctx, {
          type: 'doughnut',
          data: {
            labels: config.labels || [],
            datasets: [
              {
                data: config.data || [],
                backgroundColor: config.colors || ['#dc2626', '#2563eb', '#d97706', '#16a34a', '#8b5cf6', '#64748b'],
                borderWidth: 0,
                hoverOffset: 8,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '68%',
            plugins: {
              legend: {
                display: config.showLegend !== undefined ? config.showLegend : true,
                position: 'bottom',
                labels: {
                  color: textColor,
                  font: { family: "'Inter', system-ui, sans-serif", size: 12 },
                  padding: 16,
                  usePointStyle: true,
                  pointStyle: 'circle',
                },
              },
              tooltip: {
                backgroundColor: isDark ? '#1e1e1e' : '#fff',
                titleColor: isDark ? '#f8fafc' : '#0f172a',
                bodyColor: isDark ? '#cbd5e1' : '#475569',
                borderColor: isDark ? '#2a2a2a' : '#e2e8f0',
                borderWidth: 1,
                padding: 12,
                cornerRadius: 8,
                callbacks: {
                  label: function (context) {
                    var total = context.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                    var pct = ((context.parsed / total) * 100).toFixed(1);
                    return context.label + ': ' + context.parsed + ' (' + pct + '%)';
                  },
                },
              },
            },
          },
        });
      },

      createBarChart: function (canvasId, config) {
        var canvas = document.getElementById(canvasId);
        if (!canvas || typeof Chart === 'undefined') return null;

        var ctx = canvas.getContext('2d');
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        var gridColor = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(15,23,42,0.06)';
        var textColor = isDark ? '#94a3b8' : '#64748b';

        return new Chart(ctx, {
          type: 'bar',
          data: {
            labels: config.labels || [],
            datasets: (config.datasets || []).map(function (ds) {
              return {
                label: ds.label || '',
                data: ds.data || [],
                backgroundColor: ds.backgroundColor || 'rgba(220,38,38,0.75)',
                borderColor: ds.borderColor || '#dc2626',
                borderWidth: ds.borderWidth || 1,
                borderRadius: ds.borderRadius || 4,
                borderSkipped: false,
              };
            }),
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                display: config.showLegend !== undefined ? config.showLegend : true,
                position: 'bottom',
                labels: {
                  color: textColor,
                  font: { family: "'Inter', system-ui, sans-serif", size: 12 },
                  padding: 16,
                  usePointStyle: true,
                  pointStyle: 'rectRounded',
                },
              },
              tooltip: {
                backgroundColor: isDark ? '#1e1e1e' : '#fff',
                titleColor: isDark ? '#f8fafc' : '#0f172a',
                bodyColor: isDark ? '#cbd5e1' : '#475569',
                borderColor: isDark ? '#2a2a2a' : '#e2e8f0',
                borderWidth: 1,
                padding: 12,
                cornerRadius: 8,
              },
            },
            scales: {
              x: {
                grid: { display: false },
                ticks: { color: textColor, font: { size: 11 } },
              },
              y: {
                grid: { color: gridColor, drawBorder: false },
                ticks: { color: textColor, font: { size: 11 } },
                beginAtZero: config.beginAtZero !== undefined ? config.beginAtZero : true,
              },
            },
          },
        });
      },
    };
  }

  function initSidebarGroups() {
    const storeKey = 'bms-admin-sidebar-groups';
    let saved = {};
    try {
      saved = JSON.parse(localStorage.getItem(storeKey) || '{}');
    } catch (e) {
      saved = {};
    }

    document.querySelectorAll('.sidebar-group[data-group]').forEach(function (group) {
      const name = group.getAttribute('data-group');
      const hasActive = group.querySelector('.sidebar-link.active');
      const toggle = group.querySelector('[data-sidebar-group-toggle]');

      if (hasActive) {
        saved[name] = false;
      }
      if (saved[name]) {
        group.classList.add('collapsed');
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
      }

      if (toggle) {
        toggle.addEventListener('click', function () {
          const collapsed = group.classList.toggle('collapsed');
          toggle.setAttribute('aria-expanded', String(!collapsed));
          saved[name] = collapsed;
          try {
            localStorage.setItem(storeKey, JSON.stringify(saved));
          } catch (e) {}
        });
      }
    });
  }

  function initGlobalSearch() {
    const container = document.getElementById('adminSearch');
    const input = document.getElementById('adminSearchInput');
    const results = document.getElementById('adminSearchResults');
    if (!container || !input || !results) return;

    const GROUP_ICONS = {
      movies: 'bi-film',
      users: 'bi-people',
      bookings: 'bi-ticket-perforated',
      theatres: 'bi-building',
      shows: 'bi-calendar-event',
    };

    let debounceTimer = null;
    let lastQuery = '';
    let items = [];
    let activeIndex = -1;

    function esc(s) {
      return String(s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
    }

    function setActiveItem() {
      results.querySelectorAll('.admin-search__item').forEach(function (el, i) {
        el.style.background = i === activeIndex ? 'var(--surface-muted)' : '';
      });
    }

    function moveActive(dir) {
      if (!items.length) return;
      activeIndex = (activeIndex + dir + items.length) % items.length;
      setActiveItem();
    }

    function closeResults() {
      results.classList.remove('open');
      items = [];
      activeIndex = -1;
    }

    function render(data) {
      let html = '';
      let total = 0;
      items = [];
      activeIndex = -1;

      Object.keys(data.results || {}).forEach(function (group) {
        const list = data.results[group];
        if (!list || !list.length) return;
        html += '<div class="admin-search__group-title">' + esc(group) + '</div>';
        list.forEach(function (item) {
          items.push(item);
          html +=
            '<a href="' + item.url + '" class="admin-search__item">' +
              '<span class="admin-search__item-icon"><i class="bi ' + (GROUP_ICONS[group] || 'bi-search') + '"></i></span>' +
              '<span style="min-width:0;flex:1;">' +
                '<span class="admin-search__item-title d-block">' + esc(item.title) + '</span>' +
                '<span class="admin-search__item-sub d-block">' + esc(item.subtitle || '') + '</span>' +
              '</span>' +
            '</a>';
          total += 1;
        });
      });

      if (!total) {
        html = '<div class="admin-search__empty"><i class="bi bi-search me-2"></i>No results for &ldquo;' + esc(lastQuery) + '&rdquo;</div>';
      }

      html +=
        '<div class="admin-search__footer">' +
          '<span><kbd>&uarr;</kbd><kbd>&darr;</kbd> navigate</span>' +
          '<span><kbd>Enter</kbd> open</span>' +
          '<span><kbd>Esc</kbd> close</span>' +
        '</div>';

      results.innerHTML = html;
      results.classList.add('open');
    }

    function fetchResults(q) {
      results.innerHTML = '<div class="admin-search__loading">Searching&hellip;</div>';
      results.classList.add('open');
      fetch('/admin-search/?q=' + encodeURIComponent(q))
        .then(function (res) {
          if (!res.ok) throw new Error('Network error');
          return res.json();
        })
        .then(function (data) {
          render(data);
        })
        .catch(function () {
          results.innerHTML = '<div class="admin-search__empty">Something went wrong. Please try again.</div>';
          results.classList.add('open');
        });
    }

    input.addEventListener('input', function () {
      const q = input.value.trim();
      if (!q) {
        lastQuery = '';
        items = [];
        closeResults();
        return;
      }
      lastQuery = q;
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        fetchResults(q);
      }, 250);
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        closeResults();
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        moveActive(1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        moveActive(-1);
      } else if (e.key === 'Enter') {
        if (activeIndex >= 0 && items[activeIndex]) {
          e.preventDefault();
          window.location.href = items[activeIndex].url;
        }
      }
    });

    document.addEventListener('click', function (e) {
      if (!container.contains(e.target)) {
        closeResults();
      }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
        e.preventDefault();
        input.focus();
        input.select();
      }
    });
  }

  function initDashboardChart() {
    const canvas = document.getElementById('businessChart');
    const ranges = window.BMS_DASHBOARD_RANGES;
    if (!canvas || typeof Chart === 'undefined' || !ranges) return;

    let chart = null;

    function buildChart(rangeKey) {
      const data = ranges[rangeKey];
      if (!data) return;
      if (chart) chart.destroy();
      chart = window.BMS.chart.createLineChart('businessChart', {
        labels: data.labels,
        datasets: [
          {
            label: 'Revenue',
            data: data.revenue,
            borderColor: '#e50914',
            backgroundColor: 'rgba(229,9,20,0.10)',
            fill: true,
          },
          {
            label: 'Bookings',
            data: data.bookings,
            borderColor: '#2563eb',
            backgroundColor: 'rgba(37,99,235,0.08)',
            fill: false,
          },
        ],
        showLegend: true,
        beginAtZero: true,
      });
    }

    buildChart('7d');

    document.querySelectorAll('[data-range]').forEach(function (pill) {
      pill.addEventListener('click', function () {
        document.querySelectorAll('[data-range]').forEach(function (p) {
          p.classList.remove('active');
          p.setAttribute('aria-selected', 'false');
        });
        pill.classList.add('active');
        pill.setAttribute('aria-selected', 'true');
        buildChart(pill.getAttribute('data-range'));
      });
    });
  }

  function init() {
    initSidebar();
    initTooltips();
    initPopovers();
    initNotificationPolling();
    initAlertAutoHide();
    initConfirmDialogs();
    initCharts();
    initSidebarGroups();
    initGlobalSearch();
    initDashboardChart();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
