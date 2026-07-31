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
      if (count > 0) {
        badge.textContent = count > 99 ? '99+' : count;
        badge.style.display = 'flex';
      } else {
        badge.style.display = 'none';
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

  function init() {
    initSidebar();
    initTooltips();
    initPopovers();
    initNotificationPolling();
    initAlertAutoHide();
    initConfirmDialogs();
    initCharts();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
