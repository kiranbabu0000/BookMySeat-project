/**
 * BookMySeat — Dynamic "Group of People" emoji visualization for ticket count & seat selection.
 *
 * Renders full-size person emojis (👤) dynamically grouped into rows:
 * - 1 seat: 👤
 * - 2 seats: 👤 👤
 * - 4 seats: 👤 👤 / 👤 👤 (2x2)
 * - 6 seats: 👤 👤 👤 / 👤 👤 👤 (2x3)
 * - 10 seats: 👤 👤 👤 👤 👤 / 👤 👤 👤 👤 👤 (2x5)
 *
 * Dynamically updates whenever seat count changes and animates entry smoothly.
 */
(function () {
  'use strict';

  function personEmojiMarkup(i) {
    return '<span class="ticket-person" style="--i:' + i + '" aria-hidden="true" title="Person ' + (i + 1) + '">👤</span>';
  }

  window.renderTicketPeople = function (container, count) {
    if (!container) return;

    count = Number(count) || 0;
    count = Math.max(0, Math.min(10, count));
    container.dataset.count = String(count);
    container.innerHTML = '';

    if (count === 0) {
      container.classList.add('is-empty');
      var empty = document.createElement('div');
      empty.className = 'ticket-people__empty';
      empty.innerHTML = '<i class="bi bi-person-slash" aria-hidden="true"></i><span>No seats selected</span>';
      container.appendChild(empty);
      return;
    }

    container.classList.remove('is-empty');
    var stage = document.createElement('div');
    stage.className = 'ticket-people__stage';
    
    // Grouping per row: 1-3 -> 1 row, 4-6 -> 2 rows (max 3/row), 7-10 -> 2 rows (max 5/row)
    var itemsPerRow = count <= 3 ? count : (count <= 6 ? Math.ceil(count / 2) : 5);
    stage.style.setProperty('--per-row', itemsPerRow);
    container.appendChild(stage);

    for (var i = 0; i < count; i++) {
      stage.insertAdjacentHTML('beforeend', personEmojiMarkup(i));
    }

    var badge = document.createElement('span');
    badge.className = 'ticket-people__badge';
    badge.textContent = count + (count === 1 ? ' Person' : ' People');
    container.appendChild(badge);
  };
})();
