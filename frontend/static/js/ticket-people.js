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
    // Use an inline SVG human icon for consistent appearance across platforms.
    var svg = '' +
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" role="img" aria-hidden="true" focusable="false">' +
      '<path fill="currentColor" d="M12 2a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm-6 20a6 6 0 0 1 12 0h-12z"/>' +
      '</svg>';
    return '<span class="ticket-person" style="--i:' + i + '" aria-hidden="true" title="Person ' + (i + 1) + '">' + svg + '</span>';
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
