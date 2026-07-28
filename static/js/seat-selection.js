/**
 * BookMySeat — Seat selection interactions
 */
(function () {
  'use strict';

  var PRICE_PER_SEAT = 250;

  document.addEventListener('DOMContentLoaded', function () {
    initSeatSelection();
  });

  function initSeatSelection() {
    var form = document.getElementById('seat-form');
    if (!form) return;

    var seats = form.querySelectorAll('.seat:not(.seat--booked)');
    var selectedList = document.getElementById('selected-seats-list');
    var seatCountEl = document.getElementById('summary-seat-count');
    var totalEl = document.getElementById('summary-total');
    var bookBtn = document.getElementById('book-btn');

    seats.forEach(function (seatEl) {
      var checkbox = seatEl.querySelector('input[type="checkbox"]');
      if (!checkbox) return;

      seatEl.addEventListener('click', function (e) {
        if (seatEl.classList.contains('seat--booked')) return;
        e.preventDefault();
        checkbox.checked = !checkbox.checked;
        seatEl.classList.toggle('seat--selected', checkbox.checked);
        updateSummary();
      });

      checkbox.addEventListener('change', function () {
        seatEl.classList.toggle('seat--selected', checkbox.checked);
        updateSummary();
      });
    });

    function updateSummary() {
      var checked = form.querySelectorAll('input[type="checkbox"]:checked');
      var count = checked.length;
      var total = count * PRICE_PER_SEAT;

      if (seatCountEl) seatCountEl.textContent = count;
      if (totalEl) totalEl.textContent = '\u20B9' + total.toLocaleString('en-IN');

      if (selectedList) {
        if (count === 0) {
          selectedList.innerHTML = '<span class="text-muted">No seats selected</span>';
        } else {
          var numbers = Array.from(checked).map(function (cb) {
            return cb.closest('.seat').querySelector('label, span')?.textContent?.trim() || cb.value;
          });
          selectedList.innerHTML = numbers.map(function (n) {
            return '<span class="badge bg-danger me-1 mb-1">' + n + '</span>';
          }).join('');
        }
      }

      if (bookBtn) {
        bookBtn.disabled = count === 0;
        bookBtn.textContent = count > 0
          ? 'Book ' + count + ' Seat' + (count > 1 ? 's' : '') + ' — \u20B9' + total.toLocaleString('en-IN')
          : 'Select Seats to Book';
      }
    }

    updateSummary();

    form.addEventListener('submit', function (e) {
      var checked = form.querySelectorAll('input[type="checkbox"]:checked');
      if (checked.length === 0) {
        e.preventDefault();
        showSeatError('Please select at least one seat.');
      }
    });
  }

  function showSeatError(msg) {
    var alert = document.getElementById('seat-error');
    if (alert) {
      alert.textContent = msg;
      alert.classList.remove('d-none');
      alert.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }
})();
