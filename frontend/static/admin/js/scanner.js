(function () {
  var video = document.getElementById('scannerVideo');
  var viewport = document.getElementById('scannerViewport');
  var frame = document.getElementById('scannerFrame');
  var placeholder = document.getElementById('scannerPlaceholder');
  var status = document.getElementById('scannerStatus');
  var startBtn = document.getElementById('startCamera');
  var stopBtn = document.getElementById('stopCamera');
  var resultPanel = document.getElementById('scanResultPanel');
  var resultBody = document.getElementById('scanResultBody');
  var manualPayload = document.getElementById('manualPayload');
  var manualValidateBtn = document.getElementById('manualValidate');
  var imageInput = document.getElementById('imageInput');

  var scanUrl = document.getElementById('ticketScanUrl').value;
  var csrfToken = '';
  var csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
  if (csrfInput) csrfToken = csrfInput.value;

  var stream = null;
  var scanLoop = null;
  var decodeCanvas = document.createElement('canvas');
  var decodeCtx = decodeCanvas.getContext('2d');
  var cooldownUntil = 0;
  var COOLDOWN_MS = 2500;

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = String(s == null ? '' : s);
    return d.innerHTML;
  }

  function setStatus(msg, cls) {
    status.className = 'alert small mt-3 mb-0' + (cls ? ' ' + cls : '');
    status.innerHTML = msg;
  }

  function resultVariant(result) {
    if (result === 'admitted') return 'success';
    if (result === 'already_scanned') return 'warning';
    if (result === 'unpaid' || result === 'cancelled' || result === 'invalid_signature') return 'danger';
    return 'secondary';
  }

  function resultLabel(result) {
    if (result === 'admitted') return 'ENTRY ALLOWED';
    if (result === 'already_scanned') return 'ALREADY USED';
    if (result === 'invalid_signature') return 'INVALID QR';
    if (result === 'unpaid') return 'UNPAID';
    if (result === 'cancelled') return 'CANCELLED / REFUNDED';
    return 'NOT FOUND';
  }

  function seatsText(seats) {
    if (Array.isArray(seats)) return seats.join(', ');
    if (typeof seats === 'string' && seats) return seats;
    return '-';
  }

  function renderResult(data) {
    var variant = resultVariant(data.reason || 'not_found');
    var ok = !!data.valid && !!data.scanned;
    var icon = ok ? 'check-circle-fill' : (data.reason === 'already_scanned' ? 'clock-history' : (data.reason === 'invalid_signature' ? 'shield-x' : 'x-circle-fill'));
    resultBody.innerHTML =
      '<div class="d-flex align-items-center gap-3 mb-3">' +
        '<div class="scan-result-icon text-bg-' + variant + '"><i class="bi bi-' + icon + '"></i></div>' +
        '<div>' +
          '<div class="scan-badge badge-bms-' + variant + '">' + resultLabel(data.reason || 'not_found') + '</div>' +
          '<div class="small text-muted mt-1">' + esc(data.message || '') + '</div>' +
        '</div>' +
      '</div>' +
      '<dl class="scan-detail-grid mb-0">' +
        '<dt>Booking</dt><dd><code>' + esc(data.booking_ref || '-') + '</code></dd>' +
        '<dt>Movie</dt><dd>' + esc(data.movie || '-') + '</dd>' +
        '<dt>Theatre</dt><dd>' + esc(data.theatre || '-') + '</dd>' +
        '<dt>Show</dt><dd>' + esc(data.show_time ? new Date(data.show_time).toLocaleString() : '-') + '</dd>' +
        '<dt>Seats</dt><dd>' + esc(seatsText(data.seats)) + '</dd>' +
        '<dt>Scan time</dt><dd>' + esc(data.scanned_at ? new Date(data.scanned_at).toLocaleString() : '-') + '</dd>' +
      '</dl>';
    resultPanel.classList.remove('d-none');
  }

  function postScan(payload) {
    if (Date.now() < cooldownUntil) return;
    setStatus('<i class="bi bi-hourglass-split me-1"></i>Validating ticket&hellip;', 'alert-info');
    fetch(scanUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify(payload)
    }).then(function (res) {
      return res.json().catch(function () {
        return { valid: false, reason: 'not_found', message: 'Unexpected server response. Please sign in again.' };
      });
    }).then(function (data) {
      renderResult(data);
      var variant = resultVariant(data.reason || 'not_found');
      var ok = !!data.valid && !!data.scanned;
      var cls = ok ? 'alert-success' : (variant === 'warning' ? 'alert-warning' : 'alert-danger');
      setStatus(
        '<i class="bi bi-' + (ok ? 'check-circle' : 'x-circle') + ' me-1"></i>' + esc(resultLabel(data.reason || 'not_found')),
        cls
      );
      cooldownUntil = Date.now() + COOLDOWN_MS;
      setTimeout(function () {
        setStatus('Ready for the next scan.', 'alert-info');
      }, COOLDOWN_MS);
    }).catch(function () {
      setStatus('<i class="bi bi-exclamation-triangle me-1"></i>Network error. Please try again.', 'alert-danger');
    });
  }

  function parsePayload(text) {
    try {
      var obj = JSON.parse(text);
      return obj && typeof obj === 'object' ? obj : null;
    } catch (e) {
      return null;
    }
  }

  function decodeFromImageData(imageData) {
    if (typeof jsQR !== 'function') return null;
    try {
      return jsQR(imageData.data, imageData.width, imageData.height, { inversionAttempts: 'dontInvert' });
    } catch (e) {
      return null;
    }
  }

  function startScanning() {
    if (typeof jsQR !== 'function') {
      setStatus('<i class="bi bi-exclamation-triangle me-1"></i>QR decoder failed to load. Use manual entry below.', 'alert-danger');
      return;
    }
    if (!video.srcObject) {
      setStatus('<i class="bi bi-exclamation-triangle me-1"></i>Camera is not running. Press Start Camera.', 'alert-warning');
      return;
    }
    stopScanLoop();
    frame.classList.add('is-scanning');
    scanLoop = requestAnimationFrame(function tick() {
      if (video.readyState === video.HAVE_ENOUGH_DATA) {
        decodeCanvas.width = video.videoWidth;
        decodeCanvas.height = video.videoHeight;
        decodeCtx.drawImage(video, 0, 0, decodeCanvas.width, decodeCanvas.height);
        var code = decodeFromImageData(decodeCtx.getImageData(0, 0, decodeCanvas.width, decodeCanvas.height));
        if (code && code.data) {
          var payload = parsePayload(code.data);
          if (payload) {
            setStatus('<i class="bi bi-check-circle me-1"></i>QR detected &mdash; validating&hellip;', 'alert-info');
            postScan(payload);
            stopScanLoop();
            setTimeout(startScanning, COOLDOWN_MS + 400);
            return;
          }
        }
      }
      scanLoop = requestAnimationFrame(tick);
    });
  }

  function stopScanLoop() {
    if (scanLoop) {
      cancelAnimationFrame(scanLoop);
      scanLoop = null;
    }
    frame.classList.remove('is-scanning');
  }

  function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus('<i class="bi bi-exclamation-triangle me-1"></i>Camera is not supported on this device. Use manual entry or image scan.', 'alert-warning');
      return;
    }
    setStatus('<i class="bi bi-hourglass-split me-1"></i>Requesting camera access&hellip;', 'alert-info');
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } } })
      .then(function (s) {
        stream = s;
        video.srcObject = s;
        video.play().catch(function () {});
        placeholder.style.display = 'none';
        startBtn.classList.add('d-none');
        stopBtn.classList.remove('d-none');
        setStatus('Scanning live. Point the camera at the ticket QR.', 'alert-success');
        startScanning();
      })
      .catch(function () {
        setStatus('<i class="bi bi-exclamation-triangle me-1"></i>Camera unavailable or permission denied. Use manual entry or image scan below.', 'alert-danger');
      });
  }

  function stopCamera() {
    stopScanLoop();
    if (stream) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
    }
    video.srcObject = null;
    placeholder.style.display = 'flex';
    startBtn.classList.remove('d-none');
    stopBtn.classList.add('d-none');
    setStatus('Camera is off. Press Start Camera to begin scanning.', 'alert-info');
  }

  startBtn.addEventListener('click', startCamera);
  stopBtn.addEventListener('click', stopCamera);

  manualValidateBtn.addEventListener('click', function () {
    var text = (manualPayload.value || '').trim();
    if (!text) {
      setStatus('<i class="bi bi-exclamation-triangle me-1"></i>Paste a ticket QR payload first.', 'alert-warning');
      return;
    }
    var payload = parsePayload(text);
    if (!payload) {
      setStatus('<i class="bi bi-exclamation-triangle me-1"></i>That does not look like valid QR JSON.', 'alert-danger');
      return;
    }
    postScan(payload);
  });

  imageInput.addEventListener('change', function () {
    var file = imageInput.files && imageInput.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function (e) {
      var img = new Image();
      img.onload = function () {
        decodeCanvas.width = img.width;
        decodeCanvas.height = img.height;
        decodeCtx.drawImage(img, 0, 0);
        var code = decodeFromImageData(decodeCtx.getImageData(0, 0, img.width, img.height));
        if (code && code.data) {
          var payload = parsePayload(code.data);
          if (payload) {
            postScan(payload);
          } else {
            setStatus('<i class="bi bi-exclamation-triangle me-1"></i>QR decoded but it is not a valid BookMySeat payload.', 'alert-danger');
          }
        } else {
          setStatus('<i class="bi bi-exclamation-triangle me-1"></i>No QR code found in that image.', 'alert-warning');
        }
      };
      img.onerror = function () {
        setStatus('<i class="bi bi-exclamation-triangle me-1"></i>Could not read that image.', 'alert-danger');
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
    imageInput.value = '';
  });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden && stream) stopCamera();
  });
  window.addEventListener('pagehide', stopCamera);
})();
