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
  var MAX_SEATS = 12;

  var layout = null;
  var csrfToken = '';
  var show = {};
  var els = {};
  var seatEls = new Map();
  var prices = {};
  var state = {
    booked: new Set(),
    reserved: new Set(),
    selected: new Set(),
    held: new Set(),
    reservation: null,
    expiresAt: null,
    mode: 'select',
  };
  var timerHandle = null;
  var pollHandle = null;
  var lastEtag = null;
  var busy = false;

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

    show = parseJson(document.getElementById('showData').textContent, {});
    if (!show.ticket_price) show.ticket_price = '250';
    if (show.platform_fee === undefined || show.platform_fee === null) show.platform_fee = 5;
    if (show.misc_fee === undefined || show.misc_fee === null) show.misc_fee = 2.5;
    if (show.prices) {
      Object.keys(show.prices).forEach(function (id) {
        prices[id] = Number(show.prices[id]);
      });
    }

    collectSeatEls();

    var savedRes = parseJson(document.getElementById('reservationData').textContent, null);
    if (savedRes && savedRes.token && new Date(savedRes.expires_at) > new Date()) {
      state.reservation = savedRes;
      state.mode = 'hold';
      state.held = new Set(savedRes.seats.map(String));
      state.expiresAt = new Date(savedRes.expires_at);
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
    els.continueBtn.disabled = ids.size === 0 || state.mode === 'hold';
  }

  function renderMode() {
    var hold = state.mode === 'hold';
    els.selActions.classList.toggle('d-none', hold);
    els.holdActions.classList.toggle('d-none', !hold);
    els.timerBar.classList.toggle('d-none', !hold);
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

  function handleSelectSeatClick(el) {
    if (busy) return;
    var id = el.dataset.seatId;
    if (state.booked.has(id) || state.reserved.has(id)) {
      flashMessage('This seat is no longer available.', 'danger');
      return;
    }
    if (state.selected.has(id)) {
      state.selected.delete(id);
    } else {
      if (state.selected.size >= MAX_SEATS) {
        flashMessage('You can select a maximum of ' + MAX_SEATS + ' seats.', 'danger');
        return;
      }
      state.selected.add(id);
    }
    renderSeats();
    updateSummary();
  }

  function handleHoldSeatClick(el) {
    if (busy) return;
    var id = el.dataset.seatId;
    if (state.booked.has(id)) return;
    if (state.held.has(id)) {
      doModify({ remove: [id] });
    } else if (state.reserved.has(id)) {
      flashMessage('This seat has just been reserved by another user.', 'danger');
    } else {
      if (state.held.size >= MAX_SEATS) {
        flashMessage('You can hold a maximum of ' + MAX_SEATS + ' seats.', 'danger');
        return;
      }
      doModify({ add: [id] });
    }
  }

  /* ---------- actions ---------- */

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
    }).then(function (data) {
      busy = false;
      setProcessing(false);
      if (data.ok) {
        applyReservation(data.reservation);
        flashMessage('Your seats are held for 2 minutes.', 'success');
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
    state.held = new Set(res.seats.map(String));
    state.expiresAt = new Date(res.expires_at);
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
    var remaining = Math.max(0, Math.floor((state.expiresAt.getTime() - Date.now()) / 1000));
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
