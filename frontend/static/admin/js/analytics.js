/* Analytics dashboard for the BookMySeat admin portal.
 * Renders Chart.js charts + the peak heatmap from embedded JSON on load, and
 * refreshes everything in place (charts, stats, tables, heatmap) via AJAX when
 * the date range changes.
 */
(function () {
  'use strict';

  var charts = {};

  function getCSSVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || null;
  }

  function chartOptions(isHBar) {
    var tickColor = getCSSVar('--text-muted') || '#94a3b8';
    var gridColor = getCSSVar('--border-color') || '#e2e8f0';
    var legendColor = getCSSVar('--text-secondary') || '#475569';
    var opts = {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          labels: { color: legendColor, boxWidth: 12, boxHeight: 12 }
        },
        tooltip: {
          backgroundColor: getCSSVar('--bg-elevated') || 'rgba(15,23,42,0.9)',
          titleColor: getCSSVar('--text-primary') || '#fff',
          bodyColor: getCSSVar('--text-secondary') || '#e2e8f0'
        }
      }
    };
    var scaleConfig = {
      ticks: { color: tickColor },
      grid: { color: gridColor }
    };
    if (isHBar) {
      opts.scales = { x: scaleConfig, y: { ticks: { color: tickColor } } };
    } else {
      opts.scales = { x: scaleConfig, y: scaleConfig };
    }
    return opts;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderChart(key, spec) {
    var canvas = document.querySelector('canvas[data-chart="' + key + '"]');
    if (!canvas) return;
    if (charts[key]) {
      charts[key].destroy();
      delete charts[key];
    }
    var ctx = canvas.getContext('2d');
    charts[key] = new Chart(ctx, {
      type: spec.type,
      data: {
        labels: spec.labels || [],
        datasets: spec.datasets || []
      },
      options: chartOptions(spec.indexAxis === 'y')
    });
  }

  function renderCharts(specs) {
    Object.keys(specs || {}).forEach(function (key) {
      var spec = specs[key];
      if (spec && spec.type === 'heatmap') {
        renderHeatmap(spec);
      } else {
        renderChart(key, spec);
      }
    });
  }

  function renderHeatmap(spec) {
    var tbody = document.querySelector('[data-heatmap]');
    if (!tbody || !spec || !spec.matrix) return;
    var matrix = spec.matrix;
    var flat = [];
    matrix.forEach(function (row) { flat = flat.concat(row); });
    var max = Math.max.apply(null, flat.concat([1]));
    var rows = tbody.querySelectorAll('tr');
    for (var di = 0; di < rows.length; di++) {
      var cells = rows[di].querySelectorAll('.heat-cell');
      for (var ci = 0; ci < cells.length; ci++) {
        var td = cells[ci];
        var h = parseInt(td.getAttribute('data-h'), 10);
        var dow = parseInt(td.getAttribute('data-dow'), 10);
        var v = (matrix[dow] && matrix[dow][h]) || 0;
        var alpha = 0.08 + 0.92 * (v / max);
        td.style.backgroundColor = 'rgba(220, 38, 38, ' + alpha.toFixed(3) + ')';
        td.style.color = alpha > 0.55 ? '#fff' : 'inherit';
        td.title = (spec.weekdays[dow] || '') + ' ' + ('0' + h).slice(-2) + ':00 — ' + v + ' bookings';
        td.textContent = v > 0 ? v : '';
      }
    }
  }

  function resolvePath(obj, path) {
    return path.split('.').reduce(function (o, k) {
      return o == null ? undefined : o[k];
    }, obj);
  }

  function updateStats(data) {
    document.querySelectorAll('[data-stat]').forEach(function (el) {
      var value = resolvePath(data, el.getAttribute('data-stat'));
      if (value !== undefined && value !== null) {
        el.textContent = value;
      }
    });
    document.querySelectorAll('[data-change]').forEach(function (el) {
      var value = resolvePath(data, el.getAttribute('data-change'));
      if (value === undefined || value === null) {
        el.className = 'stat-change';
        el.innerHTML = '&mdash;';
        return;
      }
      var up = Number(value) >= 0;
      el.className = 'stat-change ' + (up ? 'up' : 'down');
      el.innerHTML = (up
        ? '<i class="bi bi-arrow-up-right"></i>'
        : '<i class="bi bi-arrow-down-right"></i>') +
        Number(value).toFixed(1) + '% vs prev period';
    });
  }

  function updateRangeLabel(data) {
    var el = document.querySelector('[data-range-label]');
    if (el && data.range) {
      el.textContent = data.range.label + ' · ' + data.range.start + ' → ' + data.range.end;
    }
  }

  function renderTable(key, table) {
    var tbody = document.querySelector('tbody[data-table="' + key + '"]');
    if (!tbody) return;
    if (!table || !table.rows || !table.rows.length) {
      tbody.innerHTML = '<tr><td colspan="' +
        ((table && table.columns ? table.columns.length : 1)) +
        '" class="text-center text-muted py-4">No data in this period.</td></tr>';
      return;
    }
    tbody.innerHTML = table.rows.map(function (row) {
      return '<tr>' + row.map(function (cell) {
        return '<td>' + escapeHtml(cell == null ? '\u2014' : cell) + '</td>';
      }).join('') + '</tr>';
    }).join('');
  }

  function renderTables(tables) {
    Object.keys(tables || {}).forEach(function (key) {
      renderTable(key, tables[key]);
    });
  }

  function applyData(data) {
    updateRangeLabel(data);
    updateStats(data);
    renderCharts(data.charts);
    renderTables(data.tables);
  }

  function formParams(form) {
    return new URLSearchParams(new FormData(form)).toString();
  }

  function statusEl(form) {
    return form.querySelector('[data-ajax-status]');
  }

  function fetchAnalytics(form) {
    var status = statusEl(form);
    var endpoint = form.getAttribute('data-endpoint');
    if (!endpoint) return;
    var params = formParams(form);
    if (status) {
      status.textContent = 'Loading…';
      status.className = 'ms-2 text-muted small';
    }
    fetch(endpoint + '?' + params, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    }).then(function (res) {
      if (!res.ok) throw new Error('Request failed with status ' + res.status);
      return res.json();
    }).then(function (data) {
      applyData(data);
      if (status) {
        status.textContent = 'Updated';
        status.className = 'ms-2 text-success small';
      }
      window.history.replaceState(null, '', '?' + params);
    }).catch(function () {
      if (status) {
        status.textContent = 'Error refreshing';
        status.className = 'ms-2 text-danger small';
      }
    });
  }

  function init() {
    var embedded = document.getElementById('analytics-data');
    if (embedded) {
      try {
        var payload = JSON.parse(embedded.textContent);
        renderCharts(payload.charts);
        renderTables(payload.tables);
      } catch (err) {
        // Embedded payload malformed — charts simply stay unrendered.
      }
    }

    document.addEventListener('submit', function (e) {
      var form = e.target.closest('form[data-analytics-filter]');
      if (form) {
        e.preventDefault();
        fetchAnalytics(form);
      }
    });

    document.addEventListener('change', function (e) {
      var select = e.target.closest('[data-range-preset]');
      if (!select) return;
      var form = select.closest('form[data-analytics-filter]');
      if (!form) return;
      var customFields = form.querySelectorAll('.custom-range-fields');
      if (select.value === 'custom') {
        customFields.forEach(function (f) { f.classList.remove('d-none'); });
      } else {
        customFields.forEach(function (f) { f.classList.add('d-none'); });
        fetchAnalytics(form);
      }
    });

    document.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-refresh-btn]');
      if (btn) {
        var form = btn.closest('form[data-analytics-filter]');
        if (form) fetchAnalytics(form);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
