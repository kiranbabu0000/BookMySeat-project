/* BookMySeat — graceful fallback for missing uploaded media.
 *
 * If a poster/banner URL fails to load (e.g. an image lost before
 * persistent storage was enabled) we:
 *   1. swap it for an inline BookMySeat placeholder so no broken-image
 *      icon appears and card layouts stay intact, and
 *   2. beacon the broken URL to /movies/api/images/missing-log/ so the
 *      situation is logged server-side for production debugging
 *      (once per URL per page view).
 */
(function () {
  'use strict';

  var reported = Object.create(null);
  var PLACEHOLDER =
    'data:image/svg+xml;charset=utf-8,' +
    encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900">' +
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0" stop-color="#1f2937"/><stop offset="1" stop-color="#0b1120"/>' +
        '</linearGradient></defs>' +
        '<rect width="600" height="900" fill="url(#g)"/>' +
        '<circle cx="300" cy="330" r="120" fill="none" stroke="#4b5563" stroke-width="6"/>' +
        '<circle cx="300" cy="330" r="88" fill="none" stroke="#374151" stroke-width="2"/>' +
        '<text x="300" y="355" font-family="Arial,Helvetica,sans-serif" font-size="96" fill="#9ca3af" text-anchor="middle">?</text>' +
        '<text x="300" y="560" font-family="Arial,Helvetica,sans-serif" font-size="40" font-weight="bold" letter-spacing="6" fill="#e5e7eb" text-anchor="middle">BOOKMYSEAT</text>' +
        '<text x="300" y="615" font-family="Arial,Helvetica,sans-serif" font-size="22" letter-spacing="3" fill="#6b7280" text-anchor="middle">POSTER UNAVAILABLE</text>' +
      '</svg>'
    );

  function report(url) {
    if (!url || reported[url]) return;
    reported[url] = true;
    try {
      var payload = JSON.stringify({ url: url });
      if (navigator.sendBeacon) {
        navigator.sendBeacon(
          '/movies/api/images/missing-log/',
          new Blob([payload], { type: 'application/json' })
        );
      } else {
        fetch('/movies/api/images/missing-log/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: payload,
          keepalive: true,
        }).catch(function () {});
      }
    } catch (e) { /* telemetry must never break the page */ }
  }

  function handle(errorEvent) {
    var img = errorEvent.target;
    if (!(img instanceof HTMLImageElement)) return;
    var src = img.currentSrc || img.src || '';
    // Only guard user-uploaded media; leave third-party assets alone.
    if (src.indexOf('/media/') === -1 && src.indexOf('res.cloudinary.com') === -1) return;
    report(src);
    if (img.dataset.bmsFallback) return;
    img.dataset.bmsFallback = '1';
    img.removeAttribute('srcset');
    img.src = PLACEHOLDER;
  }

  document.addEventListener('error', handle, true);
})();
