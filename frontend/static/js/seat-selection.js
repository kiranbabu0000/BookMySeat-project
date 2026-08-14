/**
 * BookMySeat — Smart seat selection client
 *
 * Handles live seat states (AJAX polling with ETag/304), multi-seat selection,
 * temporary 2-minute reservation, countdown timer, seat modification and
 * hand-off to the payment page. All mutations are validated server-side.
 */
(function () {
  'use strict';

  var POLL_INTERVAL = 5000;
  var MAX_SEATS = 10;
  var MAX_SEATS_DEFAULT = 10;

  var layout = null;
  var csrfToken = '';
  var show = {};
  var els = {};
  var seatEls = new Map();
  var prices = {};
  var coupleMap = {};
  var state = {
    booked: new Set(),
    reserved: new Set(),
    selected: new Set(),
    held: new Set(),
    reservation: null,
    expiresAt: null,
    timeOffsetMs: 0,
    mode: 'select',
    /* Most recent selection snapshot so the user can undo a clear / re-pick. */
    undoSelection: null,
    undoTicketCount: null,
  };
  var timerHandle = null;
  var pollHandle = null;
  var lastEtag = null;
  var busy = false;

  /* Ticket-count bottom sheet */
  var sheetSelection = 1;

  /* Seat-map zoom */
  var MIN_ZOOM = 60;
  var MAX_ZOOM = 150;
  var ZOOM_STEP = 10;
  var zoomLevel = 100;
  var baseSeatSize = 34;

  function parseJson(input, fallback) {
    if (!input) return fallback;
    try {
      return JSON.parse(input);
    } catch (e) {
      return fallback;
    }
  }

  function fmtCurrency(amount) {
    return '\u20B9' + Number(amount || 0).toLocaleString('en-IN');
  }

  function round2(value) {
    return Math.round(Number(value) * 100) / 100;
  }

  function esc(value) {
    return String(value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function gstRateFor(taxable) {
    var slabs = show.gst_slabs || [];
    for (var i = 0; i < slabs.length; i++) {
      var slab = slabs[i];
      if (taxable < Number(slab.min_amount)) continue;
      if (slab.max_amount === null || slab.max_amount === '' || taxable <= Number(slab.max_amount)) {
        return Number(slab.rate) / 100;
      }
    }
    return slabs.length ? Number(slabs[slabs.length - 1].rate) / 100 : 0;
  }

  function pad2(value) {
    return String(value).padStart(2, '0');
  }

  document.addEventListener('DOMContentLoaded', init);

  function init() {
    layout = document.getElementById('seatLayout');
    if (!layout) return;

    var csrfInput = layout.querySelector('input[name=csrfmiddlewaretoken]');
    csrfToken = csrfInput ? csrfInput.value : '';

    els.timerBar = document.getElementById('reservationTimerBar');
    els.timer = document.getElementById('reservationTimer');
    els.message = document.getElementById('seatMessage');
    els.seatNumbers = document.getElementById('selectedSeatNumbers');
    els.seatCount = document.getElementById('selectedSeatCount');
    els.subtotal = document.getElementById('summarySubtotal');
    els.platformFee = document.getElementById('summaryPlatformFee');
    els.miscFee = document.getElementById('summaryMiscFee');
    els.fee = document.getElementById('summaryFee');
    els.gst = document.getElementById('summaryGst');
    els.gstLabel = document.getElementById('summaryGstLabel');
    els.total = document.getElementById('summaryTotal');
    els.continueBtn = document.getElementById('continueBtn');
    els.proceedPayBtn = document.getElementById('proceedPayBtn');
    els.releaseBtn = document.getElementById('releaseBtn');
    els.selActions = document.getElementById('selectionModeActions');
    els.holdActions = document.getElementById('reservationModeActions');
    els.processing = document.getElementById('processingState');
    els.processingText = document.getElementById('processingText');
    els.zoomIn = document.getElementById('zoomInBtn');
    els.zoomOut = document.getElementById('zoomOutBtn');
    els.zoomReset = document.getElementById('zoomResetBtn');
    els.zoomLevel = document.getElementById('zoomLevel');
    els.tierBreakdown = document.getElementById('summaryTierBreakdown');
    els.ticketCountRow = document.getElementById('ticketCountRow');
    els.ticketValue = document.getElementById('ticketCountValue');
    els.ticketCountLabel = document.getElementById('ticketCountLabel');
    els.ticketCountBtn = document.getElementById('ticketCountBtn');
    els.ticketHint = document.getElementById('ticketCountHint');
    els.ticketSheet = document.getElementById('ticketCountSheet');
    els.ticketSheetChips = els.ticketSheet
      ? Array.prototype.slice.call(els.ticketSheet.querySelectorAll('[data-ticket-count]'))
      : [];
    els.ticketSheetContinue = document.getElementById('ticketCountContinue');
    els.ticketPeople = document.getElementById('ticketPeoplePreview');
    els.mobileBar = document.getElementById('mobileActionBar');
    els.mobileTotal = document.getElementById('mobileTotal');
    els.mobileSeatCount = document.getElementById('mobileSeatCount');
    els.mobilePrimary = document.getElementById('mobilePrimaryBtn');
    els.mobilePrimaryLabel = document.getElementById('mobilePrimaryLabel');
    els.mobileRelease = document.getElementById('mobileReleaseBtn');
    els.clearBtn = document.getElementById('clearSelectionBtn');
    els.undoBtn = document.getElementById('undoClearBtn');
    els.bestBtn = document.getElementById('bestSeatsBtn');
    els.mobileBestBtn = document.getElementById('mobileBestBtn');

    show = parseJson(document.getElementById('showData').textContent, {});
    if (!show.ticket_price) show.ticket_price = '250';
    if (show.platform_fee === undefined || show.platform_fee === null) show.platform_fee = 5;
    if (show.misc_fee === undefined || show.misc_fee === null) show.misc_fee = 2.5;
    MAX_SEATS = Number(show.max_tickets) || MAX_SEATS_DEFAULT;
    state.ticketCount = Math.min(Math.max(Number(show.ticket_count) || 1, 1), MAX_SEATS);
    if (show.prices) {
      Object.keys(show.prices).forEach(function (id) {
        prices[id] = Number(show.prices[id]);
      });
    }
    (show.couple_pairs || []).forEach(function (pair) {
      if (pair && pair.length === 2) {
        coupleMap[String(pair[0])] = String(pair[1]);
        coupleMap[String(pair[1])] = String(pair[0]);
      }
    });

    collectSeatEls();
    initZoom();

    var savedRes = parseJson(document.getElementById('reservationData').textContent, null);
    if (savedRes && savedRes.token && new Date(savedRes.expires_at) > new Date()) {
      state.reservation = savedRes;
      state.mode = 'hold';
      state.held = new Set(savedRes.seats.map(String));
      state.expiresAt = new Date(savedRes.expires_at);
      syncClockOffset(savedRes);
      syncPrices(savedRes);
    }

    renderSeats();
    updateSummary();
    renderMode();

    layout.addEventListener('click', onSeatClick);
    els.continueBtn.addEventListener('click', onContinue);
    els.releaseBtn.addEventListener('click', onRelease);
    els.proceedPayBtn.addEventListener('click', function (e) {
      if (!state.reservation) {
        e.preventDefault();
        return;
      }
      var url = layout.dataset.paymentUrlTemplate.replace('TOKEN', state.reservation.token);
      window.location.href = url;
    });

    if (els.mobilePrimary) {
      els.mobilePrimary.addEventListener('click', function () {
        if (state.mode === 'hold') {
          els.proceedPayBtn.click();
        } else {
          els.continueBtn.click();
        }
      });
    }
    if (els.mobileRelease) {
      els.mobileRelease.addEventListener('click', function () {
        els.releaseBtn.click();
      });
    }
    if (els.clearBtn) {
      els.clearBtn.addEventListener('click', onClearSeats);
    }
    if (els.undoBtn) {
      els.undoBtn.addEventListener('click', onUndoSelection);
    }
    if (els.bestBtn) {
      els.bestBtn.addEventListener('click', function () { onBestSeats(); });
    }
    if (els.mobileBestBtn) {
      els.mobileBestBtn.addEventListener('click', function () { onBestSeats(); });
    }

    if (els.ticketCountBtn) {
      els.ticketCountBtn.addEventListener('click', openTicketSheet);
    }
    if (els.ticketSheet) {
      els.ticketSheet.addEventListener('click', function (e) {
        if (e.target.closest('[data-tc-close]')) { closeTicketSheet(); return; }
        var chip = e.target.closest('[data-ticket-count]');
        if (chip) selectChip(Number(chip.getAttribute('data-ticket-count')));
        if (e.target.closest('#ticketCountContinue')) {
          setTicketCount(sheetSelection);
          closeTicketSheet();
        }
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && els.ticketSheet.classList.contains('is-open')) closeTicketSheet();
      });
    }

    if (state.mode === 'hold') startTimer();
    startPolling();
  }

  function collectSeatEls() {
    layout.querySelectorAll('.seat').forEach(function (el) {
      var id = el.dataset.seatId;
      if (!id) return;
      seatEls.set(id, el);
      if (el.classList.contains('seat--booked')) state.booked.add(id);
      else if (el.classList.contains('seat--reserved')) state.reserved.add(id);
    });
  }

  /* ---------- seat-map zoom ---------- */

  function initZoom() {
    var grid = document.getElementById('seatGrid');
    if (!grid) return;
    baseSeatSize = parseFloat(getComputedStyle(grid).getPropertyValue('--seat-size')) || 34;
    if (els.zoomIn) {
      els.zoomIn.addEventListener('click', function () { applyZoom(zoomLevel + ZOOM_STEP); });
      els.zoomOut.addEventListener('click', function () { applyZoom(zoomLevel - ZOOM_STEP); });
      els.zoomReset.addEventListener('click', function () { applyZoom(100); });
    }
    window.addEventListener('resize', function () {
      if (zoomLevel !== 100) return;
      grid.style.removeProperty('--seat-size');
    });
  }

  function applyZoom(level) {
    var grid = document.getElementById('seatGrid');
    if (!grid) return;
    zoomLevel = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, level));
    var px = baseSeatSize * zoomLevel / 100;
    grid.style.setProperty('--seat-size', px.toFixed(2) + 'px');
    if (els.zoomLevel) els.zoomLevel.textContent = zoomLevel + '%';
  }

  function renderSeats() {
    seatEls.forEach(function (el, id) {
      el.classList.remove(
        'seat--available', 'seat--selected', 'seat--reserved', 'seat--booked'
      );
      if (state.booked.has(id)) {
        el.classList.add('seat--booked');
      } else if (state.mode === 'hold' && state.held.has(id)) {
        el.classList.add('seat--selected');
      } else if (state.selected.has(id)) {
        el.classList.add('seat--selected');
      } else if (state.reserved.has(id)) {
        el.classList.add('seat--reserved');
      } else {
        el.classList.add('seat--available');
      }
    });
  }

  function syncPrices(res) {
    if (!res || !res.prices) return;
    if (Array.isArray(res.prices)) {
      res.prices.forEach(function (p) {
        prices[String(p.seat_id)] = Number(p.price);
      });
    } else {
      Object.keys(res.prices).forEach(function (id) {
        prices[String(id)] = Number(res.prices[id]);
      });
    }
  }

  function syncClockOffset(res) {
    var serverNow = new Date(res.expires_at).getTime() - (Number(res.remaining) || 0) * 1000;
    if (!isNaN(serverNow)) {
      state.timeOffsetMs = serverNow - Date.now();
    }
  }

  function updateSummary() {
    var ids = state.mode === 'hold' ? state.held : state.selected;
    var names = [];
    var subtotal = 0;
    ids.forEach(function (id) {
      var el = seatEls.get(id);
      names.push(el ? el.dataset.seatNumber || id : id);
      subtotal += prices[String(id)] || Number(show.ticket_price) || 0;
    });
    names.sort();
    var platformFee = ids.size * Number(show.platform_fee || 0);
    var miscFee = ids.size ? Number(show.misc_fee || 0) : 0;
    var fee = round2(platformFee + miscFee);
    var taxable = round2(subtotal + platformFee + miscFee);
    var gstRate = gstRateFor(taxable);
    var gst = round2(taxable * gstRate);
    var total = round2(subtotal + platformFee + miscFee + gst);
    els.seatNumbers.textContent = names.length ? names.join(', ') : 'None';
    els.seatCount.textContent = ids.size;
    els.subtotal.textContent = fmtCurrency(subtotal);
    els.platformFee.textContent = fmtCurrency(platformFee);
    els.miscFee.textContent = fmtCurrency(miscFee);
    els.fee.textContent = fmtCurrency(fee);
    els.gst.textContent = fmtCurrency(gst);
    if (els.gstLabel) els.gstLabel.textContent = 'GST (' + Math.round(gstRate * 100) + '%)';
    els.total.textContent = fmtCurrency(total);
    els.continueBtn.disabled = state.mode === 'hold' || ids.size !== state.ticketCount;
    if (els.ticketValue) els.ticketValue.textContent = state.ticketCount;
    if (els.ticketCountLabel) els.ticketCountLabel.textContent = state.ticketCount === 1 ? 'Ticket' : 'Tickets';
    updateTicketHint(ids.size);
    renderTierBreakdown(ids);
    if (els.mobileTotal) els.mobileTotal.textContent = fmtCurrency(total);
    if (els.mobileSeatCount) {
      els.mobileSeatCount.innerHTML = '<i class="bi bi-ticket-perforated me-1"></i>' + ids.size;
    }
    if (els.mobilePrimary) {
      els.mobilePrimary.disabled = ids.size === 0 || (state.mode !== 'hold' && ids.size !== state.ticketCount);
    }
    // Toggle Clear Seats button state (disabled when nothing selected/held)
    if (els.clearBtn) {
      els.clearBtn.disabled = ids.size === 0;
    }
    // Undo available when a snapshot exists and we're not holding
    if (els.undoBtn) {
      els.undoBtn.classList.toggle('d-none', state.mode === 'hold' || !state.undoSelection || !state.undoSelection.length);
    }
    // Best Seats only meaningful in select mode (never while holding)
    var bestEnabled = state.mode !== 'hold';
    if (els.bestBtn) els.bestBtn.disabled = !bestEnabled;
    if (els.mobileBestBtn) {
      els.mobileBestBtn.classList.toggle('d-none', !bestEnabled);
    }
  }

  function setTicketCount(n) {
    if (state.mode === 'hold') return;
    n = Math.min(Math.max(Number(n) || 1, 1), MAX_SEATS);
    if (n === state.ticketCount) return;
    state.ticketCount = n;
    clearUndo();
    updateSummary();
  }

  function openTicketSheet() {
    if (state.mode === 'hold' || !els.ticketSheet) return;
    sheetSelection = state.ticketCount;
    els.ticketSheetChips.forEach(function (chip) {
      chip.disabled = Number(chip.getAttribute('data-ticket-count')) > MAX_SEATS;
    });
    selectChip(sheetSelection);
    els.ticketSheet.classList.add('is-open');
    els.ticketSheet.setAttribute('aria-hidden', 'false');
    document.body.classList.add('tc-locked');
  }

  function closeTicketSheet() {
    if (!els.ticketSheet) return;
    els.ticketSheet.classList.remove('is-open');
    els.ticketSheet.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('tc-locked');
  }

  function selectChip(n) {
    sheetSelection = n;
    els.ticketSheetChips.forEach(function (chip) {
      var active = Number(chip.getAttribute('data-ticket-count')) === n;
      chip.classList.toggle('is-active', active);
      chip.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    renderPeople(n);
  }

  function renderPeople(n) {
    if (window.renderTicketPeople) window.renderTicketPeople(els.ticketPeople, n);
  }

  function updateTicketHint(selectedCount) {
    if (!els.ticketHint) return;
    var target = state.ticketCount;
    if (state.mode === 'hold') {
      els.ticketHint.textContent = 'Held seats can be added or removed before payment.';
      els.ticketHint.className = 'text-muted small mt-1 mb-3';
      return;
    }
    var need = target - selectedCount;
    if (need > 0) {
      if (selectedCount === 0) {
        els.ticketHint.textContent = 'Select exactly ' + target + ' seat' + (target === 1 ? '' : 's') + ' to continue.';
      } else {
        els.ticketHint.textContent = 'Select ' + need + ' more seat' + (need === 1 ? '' : 's') + ' to match your ' + target + '-ticket order.';
      }
      els.ticketHint.className = 'text-muted small mt-1 mb-3';
    } else if (need < 0) {
      els.ticketHint.textContent = 'You\u2019ve selected ' + selectedCount + ' seats for a ' + target + '-ticket order \u2014 deselect ' + (-need) + ' to continue.';
      els.ticketHint.className = 'small mt-1 mb-3 is-invalid';
    } else {
      els.ticketHint.textContent = 'All set \u2014 ' + target + ' of ' + target + ' seat' + (target === 1 ? '' : 's') + ' selected.';
      els.ticketHint.className = 'small mt-1 mb-3 is-valid';
    }
  }

  function renderTierBreakdown(ids) {
    var container = els.tierBreakdown;
    if (!container) return;
    if (!ids.size) {
      container.classList.add('d-none');
      container.innerHTML = '';
      return;
    }
    var groups = {};
    var order = [];
    ids.forEach(function (id) {
      var el = seatEls.get(id);
      var tier = (el && el.dataset.tier) || 'General';
      if (!(tier in groups)) {
        groups[tier] = { count: 0, subtotal: 0 };
        order.push(tier);
      }
      groups[tier].count += 1;
      groups[tier].subtotal += prices[String(id)] || Number(show.ticket_price) || 0;
    });
    var html = order.map(function (tier) {
      var g = groups[tier];
      var cls = tier.toLowerCase().replace(/[^a-z0-9]+/g, '-');
      return '<div class="summary-tier-breakdown__row">' +
        '<span class="tier-legend__dot tier-legend__dot--' + cls + '"></span>' +
        '<span class="summary-tier-breakdown__label">' + esc(tier) + ' × ' + g.count + '</span>' +
        '<span class="summary-tier-breakdown__amount">' + fmtCurrency(g.subtotal) + '</span>' +
        '</div>';
    }).join('');
    container.classList.remove('d-none');
    container.innerHTML = html;
  }

  function renderMode() {
    var hold = state.mode === 'hold';
    els.selActions.classList.toggle('d-none', hold);
    els.holdActions.classList.toggle('d-none', !hold);
    els.timerBar.classList.toggle('d-none', !hold);
    if (els.ticketCountRow) els.ticketCountRow.classList.toggle('d-none', hold);
    if (els.mobilePrimaryLabel) {
      els.mobilePrimaryLabel.innerHTML = hold
        ? '<i class="bi bi-credit-card me-1"></i> Proceed to Pay'
        : '<i class="bi bi-calendar2-check me-1"></i> Continue';
    }
    if (els.mobileRelease) els.mobileRelease.classList.toggle('d-none', !hold);
  }

  /* ---------- seat interaction ---------- */
  
  function onSeatClick(e) {
    var el = e.target.closest('.seat');
    if (!el || !seatEls.has(el.dataset.seatId)) return;
    if (state.mode === 'hold') {
      handleHoldSeatClick(el);
    } else {
      handleSelectSeatClick(el);
    }
  }
  
  /*
   * Auto-select adjacent seats LEFT → RIGHT within the SAME row, starting from
   * the clicked seat. Only genuinely adjacent, available and compatible seats
   * are added:
   *
   *   - never crosses an aisle (a designed couple pair may straddle one)
   *   - never jumps over a booked / reserved / already-selected / incompatible seat
   *   - never changes rows and never exceeds `needed`
   *
   * Returns an array of seat IDs to select (always includes the start seat).
   */
  function autoSelectAdjacentSeats(startEl, needed) {
    var selected = [];
    if (!startEl || needed < 1) return selected;

    var startId = String(startEl.dataset.seatId);
    var startTier = startEl.getAttribute('data-tier') || '';
    var startType = startEl.getAttribute('data-type') || '';

    var rowSeats = startEl.closest('.seat-row__seats');
    if (!rowSeats) return selected;
    if (!isAutoSelectableSeat(startEl, startTier, startType)) return selected;

    function canTake(id) {
      return !state.booked.has(id) && !state.reserved.has(id) && !state.selected.has(id);
    }

    // The clicked seat always starts the sequence.
    var count = 0;
    var startExtra = partnerOf(startId);
    if (startExtra) {
      var startExtraEl = seatEls.get(startExtra);
      if (!startExtraEl || !isAdjacentPartner(startEl, startExtraEl) || count + 2 > needed) {
        return selected;
      }
      if (!canTake(startId) || !canTake(startExtra)) return selected;
      selected.push(startId, String(startExtra));
      count += 2;
    } else {
      if (!canTake(startId)) return selected;
      selected.push(startId);
      count += 1;
    }

    // Walk rightwards. Any unavailable / incompatible seat, or an aisle for a
    // plain seat, is a hard stop — we never skip over anything. After a start
    // couple pair resume from its right-hand member (either one may be clicked).
    var node;
    if (startExtra) {
      var startPairRightEl = isNextSeatAfter(startEl, startExtraEl) ? startExtraEl : startEl;
      node = startPairRightEl.nextElementSibling;
    } else {
      node = startEl.nextElementSibling;
    }
    var crossedAisle = false;

    while (node && count < needed) {
      if (node.classList && node.classList.contains('seat-grid__aisle')) {
        crossedAisle = true;
        node = node.nextElementSibling;
        continue;
      }
      if (!node.classList || !node.classList.contains('seat')) {
        node = node.nextElementSibling;
        continue;
      }

      var id = String(node.dataset.seatId);
      if (!isAutoSelectableSeat(node, startTier, startType)) break;
      // Only a designed couple partner may sit across an aisle.
      if (crossedAisle && partnerOf(selected[selected.length - 1]) !== id) break;

      var extra = partnerOf(id);
      if (extra) {
        if (count + 2 > needed) break;
        var extraEl = seatEls.get(extra);
        if (!extraEl || !isAdjacentPartner(node, extraEl)) break;
        if (!canTake(id) || !canTake(extra)) break;
        selected.push(id, String(extra));
        count += 2;
        node = extraEl.nextElementSibling;
      } else {
        if (!canTake(id)) break;
        selected.push(id);
        count += 1;
        node = node.nextElementSibling;
      }
      crossedAisle = false;
    }

    return selected;
  }

  /*
   * A seat may be auto-selected only when it is currently rendered as an
   * available button and is compatible with the starting seat:
   *   - same tier / category
   *   - wheelchair seats are special — they are never folded into a normal or
   *     couple group (and vice-versa); couple seats pair via their partner
   */
  function isAutoSelectableSeat(el, tier, type) {
    if (!el || !el.classList || !el.classList.contains('seat')) return false;
    if (el.tagName !== 'BUTTON' || !el.classList.contains('seat--available')) return false;
    var id = String(el.dataset.seatId);
    if (state.booked.has(id) || state.reserved.has(id) || state.selected.has(id)) return false;
    if (tier && (el.getAttribute('data-tier') || '') !== tier) return false;
    var seatType = el.getAttribute('data-type') || '';
    var startWheelchair = type === 'wheelchair';
    if ((seatType === 'wheelchair') !== startWheelchair) return false;
    return true;
  }

  /*
   * True when `b` is the next seat element after `a` — an aisle may sit
   * between them but no other seat.
   */
  function isNextSeatAfter(a, b) {
    var node = a.nextElementSibling;
    while (node) {
      if (node.classList && node.classList.contains('seat')) return node === b;
      if (node.classList && node.classList.contains('seat-grid__aisle')) {
        node = node.nextElementSibling;
        continue;
      }
      node = node.nextElementSibling;
    }
    return false;
  }

  /*
   * True when `b` is the next seat element after `a` (or vice-versa) — an
   * aisle may sit between them but no other seat — i.e. `a`/`b` form a
   * designed couple pair. Checked in both directions so a pair can be started
   * by clicking either of its two seats.
   */
  function isAdjacentPartner(a, b) {
    return isNextSeatAfter(a, b) || isNextSeatAfter(b, a);
  }

  /*
   * Replays the existing seat-pop animation on the newly auto-selected seats
   * with a short stagger so the user sees the group expand left → right.
   */
  function animateAutoSeats(ids) {
    ids.forEach(function (id, i) {
      var el = seatEls.get(id);
      if (!el) return;
      (function (seatEl, delay) {
        setTimeout(function () {
          if (!state.selected.has(String(seatEl.dataset.seatId))) return;
          seatEl.classList.remove('seat--selected');
          void seatEl.offsetWidth; /* reflow so the CSS animation restarts */
          seatEl.classList.add('seat--selected');
        }, delay);
      })(el, i * 90);
    });
  }

  function handleSelectSeatClick(el) {
    if (busy) return;
    var id = String(el.dataset.seatId);
    if (state.booked.has(id) || state.reserved.has(id)) {
      flashMessage('This seat is no longer available.', 'danger');
      return;
    }
    if (state.selected.has(id)) {
      // Deselection only — never re-trigger auto-selection here.
      removeSelection(id);
      renderSeats();
      updateSummary();
      return;
    }

    var limit = state.ticketCount || MAX_SEATS;
    var remaining = limit - state.selected.size;
    if (remaining <= 0) {
      flashMessage('You can only select up to ' + limit + ' seat' + (limit === 1 ? '' : 's') + ' for this booking.', 'danger');
      return;
    }

    var extra = partnerOf(id);
    if (extra && (state.booked.has(extra) || state.reserved.has(extra) || state.selected.has(extra))) {
      flashMessage('This couple seat is only available with its partner.', 'danger');
      return;
    }

    var toSelect;
    if (extra) {
      // Couple seats always come as a pair — this needs at least 2 free slots.
      if (remaining < 2) {
        flashMessage('You can only select up to ' + limit + ' seat' + (limit === 1 ? '' : 's') + ' for this booking.', 'danger');
        return;
      }
      var pair = autoSelectAdjacentSeats(el, remaining);
      if (!pair.length || pair.indexOf(id) === -1 || pair.indexOf(extra) === -1) {
        // Partner is not genuinely adjacent — never select a broken pair.
        flashMessage('This couple seat is only available with its partner.', 'danger');
        return;
      }
      toSelect = pair;
    } else if (remaining === 1) {
      toSelect = [id];
    } else {
      toSelect = autoSelectAdjacentSeats(el, remaining);
      if (!toSelect.length || toSelect.indexOf(id) === -1) toSelect = [id];
    }

    var newly = [];
    var added = 0;
    for (var i = 0; i < toSelect.length && added < remaining; i++) {
      var sid = String(toSelect[i]);
      if (state.selected.has(sid)) continue;
      if (state.booked.has(sid) || state.reserved.has(sid)) continue;
      state.selected.add(sid);
      newly.push(sid);
      added += 1;
    }

    renderSeats();
    updateSummary();
    if (newly.length > 1) animateAutoSeats(newly);
  }

  function removeSelection(id) {
    state.selected.delete(id);
    var extra = partnerOf(id);
    if (extra) state.selected.delete(extra);
  }

  function handleHoldSeatClick(el) {
    if (busy) return;
    var id = el.dataset.seatId;
    if (state.booked.has(id)) return;
    if (state.held.has(id)) {
      var extra = partnerOf(id);
      doModify(extra && state.held.has(extra) ? { remove: [id, extra] } : { remove: [id] });
    } else if (state.reserved.has(id)) {
      flashMessage('This seat has just been reserved by another user.', 'danger');
    } else {
      var extra = partnerOf(id);
      var limit = state.ticketCount || MAX_SEATS;
      var add = extra ? 2 : 1;
      if (state.held.size + add > limit) {
        flashMessage('You can only hold up to ' + limit + ' seat' + (limit === 1 ? '' : 's') + ' for this booking.', 'danger');
        return;
      }
      if (extra && (state.booked.has(extra) || state.reserved.has(extra))) {
        flashMessage('This couple seat is only available with its partner.', 'danger');
        return;
      }
      doModify(extra ? { add: [id, extra] } : { add: [id] });
    }
  }

  function partnerOf(id) {
    return coupleMap[String(id)] || null;
  }

  /* ---------- actions ---------- */

  /*
   * Undo the last clear / automatic selection: restores the seat snapshot
   * that was active right before the change (and the ticket count).
   */
  function onUndoSelection() {
    if (busy || state.mode === 'hold') return;
    if (!state.undoSelection || !state.undoSelection.length) return;
    var toRestore = state.undoSelection.slice();
    state.undoSelection = null;
    state.undoTicketCount = null;
    // Clear current selection then restore the snapshot.
    state.selected.clear();
    var limit = state.ticketCount || MAX_SEATS;
    for (var i = 0; i < toRestore.length; i++) {
      var id = String(toRestore[i]);
      if (state.selected.size >= limit) break;
      if (state.booked.has(id) || state.reserved.has(id)) continue;
      state.selected.add(id);
    }
    renderSeats();
    updateSummary();
    if (els.undoBtn) els.undoBtn.classList.add('d-none');
    flashMessage('Previous selection restored.', 'success');
  }

  function rememberSelectionForUndo() {
    if (state.mode === 'hold') return;
    state.undoSelection = Array.from(state.selected);
    state.undoTicketCount = state.ticketCount;
    if (els.undoBtn) {
      els.undoBtn.classList.toggle('d-none', state.undoSelection.length === 0);
    }
  }

  function clearUndo() {
    state.undoSelection = null;
    state.undoTicketCount = null;
    if (els.undoBtn) els.undoBtn.classList.add('d-none');
  }

  /* Best-seat auto pick: score every possible adjacent group in the layout and
     select the highest-scoring one that fits the current ticket count. Seats
     marked as best view score highest, then closeness to the row centre. */
  function bestGroupScore(ids) {
    if (!ids || !ids.length) return -Infinity;
    var bestCount = 0;
    var rowEl = null;
    ids.forEach(function (id) {
      var el = seatEls.get(String(id));
      if (el) {
        if (el.classList.contains('seat--best')) bestCount += 1;
        rowEl = el.closest('.seat-row');
      }
    });
    var centerPenalty = 0;
    if (rowEl && ids.length) {
      var all = Array.prototype.slice.call(rowEl.querySelectorAll('.seat'));
      var positions = ids.map(function (id) {
        return all.indexOf(seatEls.get(String(id)));
      }).filter(function (p) { return p !== -1; });
      if (positions.length) {
        var centre = (all.length - 1) / 2;
        var avg = positions.reduce(function (a, b) { return a + b; }, 0) / positions.length;
        centerPenalty = Math.abs(avg - centre);
      }
    }
    return bestCount * 1000 - centerPenalty * 2 - ids.length;
  }

  function onBestSeats() {
    if (busy || state.mode === 'hold') return;
    var limit = state.ticketCount || MAX_SEATS;
    rememberSelectionForUndo();

    var bestIds = null;
    var bestScore = -Infinity;
    seatEls.forEach(function (el, id) {
      if (!el || el.tagName !== 'BUTTON' || !el.classList.contains('seat--available')) return;
      if (state.booked.has(String(id)) || state.reserved.has(String(id)) || state.selected.has(String(id))) return;
      var group = autoSelectAdjacentSeats(el, limit);
      if (!group.length || group.indexOf(String(id)) === -1) return;
      if (group.length !== limit) return;
      var score = bestGroupScore(group);
      if (score > bestScore) {
        bestScore = score;
        bestIds = group;
      }
    });

    if (!bestIds || !bestIds.length) {
      flashMessage('No adjacent seats available for ' + limit + ' ticket' + (limit === 1 ? '' : 's') + '.', 'danger');
      return;
    }

    state.selected.clear();
    bestIds.forEach(function (id) {
      if (state.selected.size >= limit) return;
      state.selected.add(String(id));
    });
    renderSeats();
    updateSummary();
    animateAutoSeats(Array.from(state.selected));
    flashMessage('Best available seats selected.', 'success');
  }

  /*
   * Clear all of the user's currently selected seats (select mode) or release
   * the whole held reservation (hold mode) so they can start a new selection.
   * Uses the existing backend release flow — it never unbooks or releases
   * another user's seats and keeps the user on the same screen.
   */
  function onClearSeats() {
    if (busy) return;
    if (state.mode === 'hold') {
      if (!state.held || state.held.size === 0) return;
      onRelease();
      return;
    }
    if (!state.selected || state.selected.size === 0) return;
    rememberSelectionForUndo();
    state.selected.clear();
    renderSeats();
    updateSummary();
    if (els.undoBtn) els.undoBtn.classList.remove('d-none');
    flashMessage('All selected seats cleared. Pick a seat to start a new selection.', 'info');
  }

  function onContinue() {
    if (busy) return;
    if (state.selected.size === 0) {
      flashMessage('Please select at least one seat.', 'danger');
      return;
    }
    busy = true;
    setProcessing(true, 'Holding your seats\u2026');
    api(layout.dataset.reserveUrl, {
      show_id: show.id,
      seats: Array.from(state.selected),
      ticket_count: state.ticketCount,
    }).then(function (data) {
      busy = false;
      setProcessing(false);
      if (data.ok) {
        applyReservation(data.reservation);
        flashMessage('Your seats are held for 5 minutes.', 'success');
      } else {
        flashMessage(data.error || 'Unable to reserve seats.', 'danger');
        refreshSeats();
      }
    }).catch(function () {
      busy = false;
      setProcessing(false);
      flashMessage('Network error. Please try again.', 'danger');
    });
  }

  function doModify(changes) {
    if (busy || !state.reservation) return;
    busy = true;
    setProcessing(true, 'Updating your seats\u2026');
    var url = layout.dataset.modifyUrlTemplate.replace('TOKEN', state.reservation.token);
    api(url, changes).then(function (data) {
      busy = false;
      setProcessing(false);
      if (data.ok) {
        syncReservation(data.reservation);
        flashMessage('Your seats have been updated.', 'success');
      } else {
        flashMessage(data.error || 'Unable to update seats.', 'danger');
        refreshSeats();
      }
    }).catch(function () {
      busy = false;
      setProcessing(false);
      flashMessage('Network error. Please try again.', 'danger');
    });
  }

  function onRelease() {
    if (busy || !state.reservation) return;
    busy = true;
    setProcessing(true, 'Releasing seats\u2026');
    var url = layout.dataset.releaseUrlTemplate.replace('TOKEN', state.reservation.token);
    api(url, {}).then(function (data) {
      busy = false;
      setProcessing(false);
      exitHoldMode();
      refreshSeats();
      flashMessage(data.ok ? 'Seats released.' : (data.error || 'Unable to release seats.'), data.ok ? 'success' : 'danger');
    }).catch(function () {
      busy = false;
      setProcessing(false);
      flashMessage('Network error. Please try again.', 'danger');
    });
  }

  function api(path, data) {
    return fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify(data || {}),
    }).then(function (resp) {
      return resp.json().then(function (json) {
        json._status = resp.status;
        return json;
      }).catch(function () {
        return { ok: false, error: 'Unexpected server response (' + resp.status + ').' };
      });
    });
  }

  /* ---------- reservation mode ---------- */

  function applyReservation(res) {
    state.reservation = res;
    state.mode = 'hold';
    state.selected.clear();
    clearUndo();
    state.held = new Set(res.seats.map(String));
    state.expiresAt = new Date(res.expires_at);
    if (Number(res.ticket_count) >= 1) state.ticketCount = Math.min(Number(res.ticket_count), MAX_SEATS);
    syncClockOffset(res);
    syncPrices(res);
    renderSeats();
    updateSummary();
    renderMode();
    startTimer();
  }

  function syncReservation(res) {
    state.reservation = res;
    state.held = new Set(res.seats.map(String));
    state.expiresAt = new Date(res.expires_at);
    if (Number(res.ticket_count) >= 1) state.ticketCount = Math.min(Number(res.ticket_count), MAX_SEATS);
    syncClockOffset(res);
    syncPrices(res);
    renderSeats();
    updateSummary();
  }

  function exitHoldMode() {
    stopTimer();
    state.reservation = null;
    state.mode = 'select';
    state.held.clear();
    state.selected.clear();
    clearUndo();
    renderSeats();
    updateSummary();
    renderMode();
  }

  function startTimer() {
    stopTimer();
    timerHandle = setInterval(tick, 1000);
    tick();
  }

  function stopTimer() {
    if (timerHandle) {
      clearInterval(timerHandle);
      timerHandle = null;
    }
  }

  function tick() {
    if (!state.reservation) return;
    var remaining = Math.max(0, Math.floor((state.expiresAt.getTime() - state.timeOffsetMs - Date.now()) / 1000));
    els.timer.textContent = pad2(Math.floor(remaining / 60)) + ':' + pad2(remaining % 60);
    els.timerBar.classList.toggle('reservation-timer-bar--urgent', remaining <= 30);
    if (remaining <= 0) onReservationExpired();
  }

  function onReservationExpired() {
    stopTimer();
    if (state.reservation) {
      var url = layout.dataset.releaseUrlTemplate.replace('TOKEN', state.reservation.token);
      fetch(url, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken },
        body: '{}',
      }).catch(function () {});
    }
    exitHoldMode();
    flashMessage('Your reservation expired and the seats have been released.', 'danger');
    refreshSeats();
  }

  /* ---------- live polling ---------- */

  function startPolling() {
    stopPolling();
    pollHandle = setInterval(poll, POLL_INTERVAL);
  }

  function stopPolling() {
    if (pollHandle) {
      clearInterval(pollHandle);
      pollHandle = null;
    }
  }

  function refreshSeats() {
    lastEtag = null;
    poll();
  }

  function poll() {
    fetch(layout.dataset.statusUrl, {
      headers: { 'If-None-Match': lastEtag || '' },
      cache: 'no-store',
    }).then(function (resp) {
      if (resp.status === 304) return null;
      if (resp.status !== 200) return null;
      lastEtag = resp.headers.get('ETag') || null;
      return resp.json();
    }).then(function (data) {
      if (!data) return;
      applySeatStates(data.seats);
      if (data.reservation) {
        if (!state.reservation || state.reservation.token !== data.reservation.token) {
          state.mode = 'hold';
          state.selected.clear();
          renderMode();
          startTimer();
        }
        syncReservation(data.reservation);
      } else if (state.mode === 'hold') {
        exitHoldMode();
        flashMessage('Your reservation is no longer active.', 'danger');
      }
    }).catch(function () {});
  }

  function applySeatStates(seatMap) {
    var nextBooked = new Set();
    var nextReserved = new Set();
    Object.keys(seatMap).forEach(function (id) {
      var st = seatMap[id];
      if (st === 'booked') nextBooked.add(id);
      else if (st === 'reserved') nextReserved.add(id);
    });
    state.booked = nextBooked;
    state.reserved = nextReserved;
    state.selected.forEach(function (id) {
      var mine = state.mode === 'hold' && state.held.has(id);
      if (nextBooked.has(id) || (nextReserved.has(id) && !mine)) {
        state.selected.delete(id);
      }
    });
    renderSeats();
    updateSummary();
  }

  /* ---------- UI helpers ---------- */

  function flashMessage(text, type) {
    els.message.textContent = text;
    els.message.className = 'alert alert-bms alert-' + type + ' mb-4';
    els.message.classList.remove('d-none');
    clearTimeout(flashMessage._t);
    flashMessage._t = setTimeout(function () {
      els.message.classList.add('d-none');
    }, 5000);
  }

  function setProcessing(show, text) {
    els.processing.classList.toggle('d-none', !show);
    els.selActions.classList.toggle('d-none', show);
    els.holdActions.classList.toggle('d-none', show);
    if (text) els.processingText.textContent = text;
  }
})();
