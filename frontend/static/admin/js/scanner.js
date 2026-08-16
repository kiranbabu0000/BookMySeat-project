(function () {
  var video = document.getElementById('scannerVideo');
  var stage = document.getElementById('scannerStage');
  var placeholder = document.getElementById('scannerPlaceholder');
  var status = document.getElementById('scannerStatus');
  var startBtn = document.getElementById('startCamera');
  var switchBtn = document.getElementById('switchCamera');
  var stopBtn = document.getElementById('stopCamera');
  var chip = document.getElementById('cameraChip');
  var chipText = document.getElementById('cameraChipText');
  var stateIcon = document.getElementById('scannerStateIcon');
  var stateTitle = document.getElementById('scannerStateTitle');
  var stateMsg = document.getElementById('scannerStateMsg');
  var actions = document.getElementById('scannerActions');
  var scanNextBtn = document.getElementById('scanNextBtn');
  var resultPanel = document.getElementById('scanResultPanel');
  var resultBody = document.getElementById('scanResultBody');
  var manualPayload = document.getElementById('manualPayload');
  var manualValidateBtn = document.getElementById('manualValidate');
  var imageInput = document.getElementById('imageInput');

  var scanUrl = document.getElementById('ticketScanUrl').value;
  var csrfToken = '';
  var csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
  if (csrfInput) csrfToken = csrfInput.value;

  var STATE = { OFF: 'off', STARTING: 'starting', SCANNING: 'scanning', VALIDATING: 'validating', RESULT: 'result', ERROR: 'error' };
  var state = STATE.OFF;
  var stream = null;
  var scanLoop = null;
  var decodeCanvas = document.createElement('canvas');
  var decodeCtx = decodeCanvas.getContext('2d');
  var lastFrameAt = 0;
  var THROTTLE_MS = 90;
  var busy = false;
  var facingMode = 'environment';

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = String(s == null ? '' : s);
    return d.innerHTML;
  }

  function setStatus(msg, cls) {
    status.className = 'alert small mt-3 mb-0' + (cls ? ' ' + cls : '');
    status.innerHTML = msg;
  }

  function setChip(label, tone) {
    chipText.textContent = label;
    chip.classList.remove('is-active', 'is-warn', 'is-error');
    if (tone) chip.classList.add(tone === 'active' ? 'is-active' : tone === 'warn' ? 'is-warn' : 'is-error');
  }

  function setState(next) {
    state = next;
    stage.classList.toggle('is-scanning', next === STATE.SCANNING);
    stage.classList.toggle('is-validating', next === STATE.VALIDATING);
    stage.classList.remove('has-success', 'has-used', 'has-invalid', 'has-error');
    stage.classList.toggle('has-result', next === STATE.RESULT);
  }

  function setOverlay(resultClass, icon, title, msg) {
    stateIcon.innerHTML = '<i class="bi bi-' + icon + '"></i>';
    stateTitle.textContent = title;
    stateMsg.textContent = msg || '';
    stage.classList.add(resultClass);
  }

  function formatShowDate(iso) {
    if (!iso) return null;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return null;
    return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  }

  function formatShowTime(iso) {
    if (!iso) return null;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return null;
    return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }

  function seatsText(seats) {
    if (Array.isArray(seats)) return seats.join(', ');
    if (typeof seats === 'string' && seats) return seats;
    return '-';
  }

  function resultMeta(data) {
    var ok = !!(data.valid && data.scanned);
    var reason = data.reason || 'not_found';
    if (ok) return { variant: 'success', overlay: 'has-success', icon: 'check-lg', title: 'TICKET VERIFIED' };
    if (reason === 'already_scanned') return { variant: 'warning', overlay: 'has-used', icon: 'x-lg', title: 'TICKET ALREADY USED' };
    if (reason === 'unpaid') return { variant: 'danger', overlay: 'has-invalid', icon: 'x-lg', title: 'UNPAID BOOKING' };
    if (reason === 'cancelled') return { variant: 'danger', overlay: 'has-invalid', icon: 'x-lg', title: 'CANCELLED / REFUNDED' };
    return { variant: 'danger', overlay: 'has-invalid', icon: 'x-lg', title: 'INVALID TICKET' };
  }

  function detailRow(dt, dd) {
    if (dd === null || dd === undefined || dd === '') return '';
    return '<dt>' + dt + '</dt><dd>' + dd + '</dd>';
  }

  function renderResult(data) {
    var meta = resultMeta(data);
    var used = data.reason === 'already_scanned';
    var usedAt = used ? formatShowTime(data.scanned_at) : null;
    var message = data.message || '';

    if (used && usedAt) {
      message = 'This ticket was already used for entry at ' + usedAt +
        (data.scan_count ? ' (attempt #' + data.scan_count + ')' : '') + '.';
    }

    setState(STATE.RESULT);
    setOverlay(meta.overlay, meta.icon, meta.title, message);

    var alertCls = meta.variant === 'success' ? 'alert-success' : (meta.variant === 'warning' ? 'alert-warning' : 'alert-danger');
    setStatus('<i class="bi bi-' + (meta.variant === 'success' ? 'check-circle' : 'x-circle') + ' me-1"></i>' + esc(meta.title), alertCls);

    var scanTime = data.scanned_at ? new Date(data.scanned_at).toLocaleString() : null;
    var rows = '';
    rows += detailRow('Booking ID', '<code>' + esc(data.booking_ref || '-') + '</code>');
    if (data.customer) rows += detailRow('Customer', esc(data.customer));
    rows += detailRow('Movie', esc(data.movie || '-'));
    rows += detailRow('Theatre', esc(data.theatre || '-'));
    rows += detailRow('Screen', esc(data.screen || '-'));
    rows += detailRow('Show Date', esc(formatShowDate(data.show_time) || '-'));
    rows += detailRow('Show Time', esc(formatShowTime(data.show_time) || '-'));
    rows += detailRow('Seats', esc(seatsText(data.seats)));
    if (scanTime) rows += detailRow(used ? 'First scanned at' : 'Scan time', esc(scanTime));

    resultBody.innerHTML =
      '<div class="d-flex align-items-center gap-3 mb-3">' +
        '<div class="scan-result-icon text-bg-' + meta.variant + '"><i class="bi bi-' + meta.icon + '"></i></div>' +
        '<div>' +
          '<div class="scan-badge badge-bms-' + meta.variant + '">' + esc(meta.title) + '</div>' +
          '<div class="small text-muted mt-1">' + esc(message || '') + '</div>' +
        '</div>' +
      '</div>' +
      (rows ? '<dl class="scan-detail-grid mb-0">' + rows + '</dl>' : '');
    resultPanel.classList.remove('d-none');
    actions.classList.remove('d-none');

    if (navigator.vibrate) {
      try { navigator.vibrate(meta.variant === 'success' ? 60 : 160); } catch (e) {}
    }
  }

  function postScan(payload) {
    if (busy) return;
    busy = true;
    setState(STATE.VALIDATING);
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
      busy = false;
      renderResult(data);
    }).catch(function () {
      busy = false;
      setState(STATE.RESULT);
      setOverlay('has-error', 'exclamation-triangle', 'NETWORK ERROR', 'Could not reach the server. Press SCAN NEXT TICKET to retry.');
      setStatus('<i class="bi bi-exclamation-triangle me-1"></i>Network error. Press SCAN NEXT TICKET to try again.', 'alert-danger');
      actions.classList.remove('d-none');
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

  function handleCode(codeData) {
    var payload = parsePayload(codeData);
    if (payload && typeof payload.booking_id === 'string' && payload.sig) {
      setStatus('<i class="bi bi-check-circle me-1"></i>QR detected &mdash; validating&hellip;', 'alert-info');
      postScan(payload);
    }
  }

  function startScanLoop() {
    stopScanLoop();
    setState(STATE.SCANNING);
    scanLoop = requestAnimationFrame(function tick(ts) {
      if (state === STATE.SCANNING && video.readyState === video.HAVE_ENOUGH_DATA) {
        if (ts - lastFrameAt >= THROTTLE_MS) {
          lastFrameAt = ts;
          decodeCanvas.width = video.videoWidth;
          decodeCanvas.height = video.videoHeight;
          decodeCtx.drawImage(video, 0, 0, decodeCanvas.width, decodeCanvas.height);
          var code = decodeFromImageData(decodeCtx.getImageData(0, 0, decodeCanvas.width, decodeCanvas.height));
          if (code && code.data) handleCode(code.data);
        }
      }
      if (state === STATE.SCANNING) scanLoop = requestAnimationFrame(tick);
    });
  }

  function stopScanLoop() {
    if (scanLoop) {
      cancelAnimationFrame(scanLoop);
      scanLoop = null;
    }
    if (state === STATE.SCANNING) setState(STATE.OFF);
  }

  function cameraErrorLabel(err) {
    var name = err && err.name;
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError' || name === 'SecurityError') {
      return 'Camera permission denied. Allow camera access for this site, or use Manual Entry / Scan Image below.';
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError') {
      return 'No camera was found on this device. Use Manual Entry / Scan Image below.';
    }
    return 'The camera is unavailable right now. Use Manual Entry / Scan Image below.';
  }

  function startCamera(switchTo) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setState(STATE.OFF);
      setChip('Not supported', 'error');
      setStatus('<i class="bi bi-exclamation-triangle me-1"></i>Camera is not supported on this device. Use Manual Entry or Scan Image below.', 'alert-warning');
      return;
    }
    if (switchTo) facingMode = switchTo;
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    stream = null;
    setState(STATE.STARTING);
    setChip('Requesting camera&hellip;', 'warn');
    setStatus('<i class="bi bi-hourglass-split me-1"></i>Requesting camera access&hellip;', 'alert-info');

    navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: facingMode } }
    }).then(function (s) {
      stream = s;
      video.srcObject = s;
      placeholder.style.display = 'none';
      startBtn.classList.add('d-none');
      stopBtn.classList.remove('d-none');
      switchBtn.classList.remove('d-none');
      setChip('Scanning live', 'active');
      setStatus('<i class="bi bi-camera-video-fill me-1"></i>Scanning live. Point the camera at the ticket QR.', 'alert-success');
      video.play().catch(function () {});
      video.addEventListener('ended', onStreamEnded);
      stream.getVideoTracks().forEach(function (t) {
        t.addEventListener('ended', onStreamEnded);
      });
      startScanLoop();
    }).catch(function (err) {
      setState(STATE.OFF);
      setChip('Camera unavailable', 'error');
      setStatus('<i class="bi bi-exclamation-triangle me-1"></i>' + esc(cameraErrorLabel(err)), 'alert-danger');
    });
  }

  function onStreamEnded() {
    stopCamera(false);
    setChip('Camera closed', 'warn');
    setStatus('<i class="bi bi-exclamation-triangle me-1"></i>The camera was closed. Press Start Camera to resume scanning.', 'alert-warning');
  }

  function stopCamera(quiet) {
    stopScanLoop();
    if (stream) {
      stream.getTracks().forEach(function (t) {
        t.removeEventListener('ended', onStreamEnded);
        t.stop();
      });
      stream = null;
    }
    video.srcObject = null;
    video.removeEventListener('ended', onStreamEnded);
    placeholder.style.display = 'flex';
    startBtn.classList.remove('d-none');
    stopBtn.classList.add('d-none');
    switchBtn.classList.add('d-none');
    setState(STATE.OFF);
    setChip('Camera off', null);
    if (!quiet) setStatus('<i class="bi bi-info-circle me-1"></i>Camera is off. Press Start Camera to begin scanning.', 'alert-info');
  }

  function scanNext() {
    busy = false;
    resultPanel.classList.add('d-none');
    resultBody.innerHTML = '';
    actions.classList.add('d-none');
    setState(STATE.OFF);
    if (stream && video.srcObject) {
      setStatus('<i class="bi bi-camera-video-fill me-1"></i>Ready. Scan the next ticket QR.', 'alert-success');
      startScanLoop();
    } else {
      placeholder.style.display = 'flex';
      setStatus('<i class="bi bi-info-circle me-1"></i>Camera is off. Press Start Camera to begin scanning.', 'alert-info');
    }
  }

  startBtn.addEventListener('click', function () { startCamera(); });
  switchBtn.addEventListener('click', function () {
    startCamera(facingMode === 'environment' ? 'user' : 'environment');
  });
  stopBtn.addEventListener('click', function () { stopCamera(false); });
  scanNextBtn.addEventListener('click', scanNext);

  manualValidateBtn.addEventListener('click', function () {
    if (busy) return;
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
    if (busy) return;
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
          handleCode(code.data);
          if (!busy) {
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
    if (document.hidden) {
      stopScanLoop();
    } else if (state === STATE.OFF && stream && video.srcObject && !busy) {
      startScanLoop();
    }
  });
  window.addEventListener('pagehide', function () { stopCamera(true); });
})();
