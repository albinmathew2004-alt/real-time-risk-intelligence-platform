/*
Risk Telemetry SDK (Phase 27)

Goals:
- Collect lightweight behavioral telemetry in-browser.
- Send metadata-only events to an existing backend ingest endpoint.
- Do NOT collect sensitive content (no answer text, no clipboard content).

Backend target:
POST {baseUrl}/v1/events/ingest

Event schema (per event):
{
  attempt_id,
  candidate_id,
  candidate_name,
  candidate_email,
  assessment_id,
  assessment_name,
  event_type,
  payload,
  occurred_at
}

Usage:
  RiskTelemetry.init({ baseUrl, attemptId, ... , devMode: true })
  RiskTelemetry.startExam()
  RiskTelemetry.enterQuestion('q1')
  RiskTelemetry.trackAnswerChange('q1', input.value)
  RiskTelemetry.endExam()
  RiskTelemetry.destroy()
*/

(function (global) {
  'use strict';

  /** @typedef {{
   *  baseUrl: string,
   *  attemptId: string,
   *  candidateId?: string,
   *  candidateName?: string,
   *  candidateEmail?: string,
   *  assessmentId?: string,
   *  assessmentName?: string,
   *  devMode?: boolean,
   *  idleTimeoutMs?: number,
   *  typingStopMs?: number,
   *  flushIntervalMs?: number,
   *  maxQueueSize?: number,
   *  maxRetries?: number,
   * }} InitOptions */

  /**
   * Small helper to safely build ISO timestamps.
   * Backend expects string timestamps; we use UTC ISO8601.
   */
  function nowIso() {
    return new Date().toISOString();
  }

  function clamp(n, min, max) {
    return Math.max(min, Math.min(max, n));
  }

  function safeStr(x) {
    if (x === null || x === undefined) return undefined;
    return String(x);
  }

  function pickPasteSizeCategory(len) {
    // Metadata-only. Thresholds are intentionally simple.
    if (!Number.isFinite(len) || len <= 0) return 'unknown';
    if (len <= 20) return 'small';
    if (len <= 120) return 'medium';
    return 'large';
  }

  function queueStorageKey(attemptId) {
    return 'riskTelemetryQueue:' + String(attemptId || 'unknown');
  }

  function tryLoadQueue(attemptId) {
    try {
      var raw = localStorage.getItem(queueStorageKey(attemptId));
      if (!raw) return [];
      var parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed;
    } catch (e) {
      return [];
    }
  }

  function trySaveQueue(attemptId, queue) {
    try {
      localStorage.setItem(queueStorageKey(attemptId), JSON.stringify(queue));
    } catch (e) {
      // ignore
    }
  }

  function removeSavedQueue(attemptId) {
    try {
      localStorage.removeItem(queueStorageKey(attemptId));
    } catch (e) {
      // ignore
    }
  }

  var RiskTelemetry = (function () {
    /** @type {InitOptions | null} */
    var _opts = null;

    /**
     * Event queue.
     * Each entry is: { event: {...}, retries: number }
     */
    var _queue = [];

    var _destroyed = false;
    var _flushTimer = null;

    // Idle detection
    var _idleTimeout = null;
    var _lastActivityAtMs = 0;
    var _idle = false;
    var _idleStartedAtMs = 0;

    // Typing detection (per question)
    var _typingTimersByQ = new Map();
    var _typingStartAtMsByQ = new Map();
    var _typingKeystrokesByQ = new Map();

    // Helpful metadata for answer changes
    var _activeQuestionId = null;
    var _lastPasteAtMsByQ = new Map();
    var _lastPasteSizeByQ = new Map();

    function _log() {
      if (!_opts || !_opts.devMode) return;
      try {
        // eslint-disable-next-line no-console
        console.log.apply(console, ['[RiskTelemetry]'].concat([].slice.call(arguments)));
      } catch (e) {
        // ignore
      }
    }

    function _warn() {
      if (!_opts || !_opts.devMode) return;
      try {
        // eslint-disable-next-line no-console
        console.warn.apply(console, ['[RiskTelemetry]'].concat([].slice.call(arguments)));
      } catch (e) {
        // ignore
      }
    }

    function _buildBaseEvent(eventType, payload, occurredAtIso) {
      if (!_opts) throw new Error('RiskTelemetry not initialized');

      return {
        attempt_id: String(_opts.attemptId),

        candidate_id: safeStr(_opts.candidateId),
        candidate_name: safeStr(_opts.candidateName),
        candidate_email: safeStr(_opts.candidateEmail),

        assessment_id: safeStr(_opts.assessmentId),
        assessment_name: safeStr(_opts.assessmentName),

        event_type: String(eventType),
        payload: payload || {},
        occurred_at: occurredAtIso || nowIso(),
      };
    }

    function _enqueue(eventObj) {
      if (!_opts) return;

      var maxQueueSize = Number.isFinite(_opts.maxQueueSize) ? _opts.maxQueueSize : 5000;
      maxQueueSize = clamp(maxQueueSize, 100, 50000);

      _queue.push({ event: eventObj, retries: 0 });

      if (_queue.length > maxQueueSize) {
        // Drop oldest to avoid unbounded growth.
        _queue = _queue.slice(_queue.length - maxQueueSize);
      }

      trySaveQueue(_opts.attemptId, _queue);
    }

    function _isOnline() {
      // navigator.onLine is imperfect, but good enough for a lightweight SDK.
      try {
        return navigator.onLine !== false;
      } catch (e) {
        return true;
      }
    }

    function _postJson(url, body) {
      return fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    }

    async function _trySendQueueOnce() {
      if (_destroyed || !_opts) return;

      if (!_isOnline()) {
        _log('offline; keeping', _queue.length, 'events queued');
        return;
      }

      if (_queue.length === 0) return;

      var baseUrl = String(_opts.baseUrl || '').replace(/\/$/, '');
      var url = baseUrl + '/v1/events/ingest';
      var maxRetries = Number.isFinite(_opts.maxRetries) ? _opts.maxRetries : 5;
      maxRetries = clamp(maxRetries, 0, 25);

      // Process in order. If an event fails, stop and try again later.
      while (_queue.length > 0) {
        var entry = _queue[0];
        try {
          var res = await _postJson(url, entry.event);

          if (!res.ok) {
            entry.retries += 1;
            _warn('ingest failed', res.status, 'retries=', entry.retries, 'event_type=', entry.event.event_type);

            if (entry.retries > maxRetries) {
              _warn('dropping event after max retries:', entry.event.event_type);
              _queue.shift();
            }

            // Stop the flush loop; we’ll retry later.
            break;
          }

          // Success: remove from queue and continue.
          _queue.shift();

        } catch (e) {
          entry.retries += 1;
          _warn('network error; retries=', entry.retries, e);

          if (entry.retries > maxRetries) {
            _warn('dropping event after max retries:', entry.event.event_type);
            _queue.shift();
          }

          break;
        }
      }

      // Persist updated queue.
      trySaveQueue(_opts.attemptId, _queue);

      if (_queue.length === 0) {
        removeSavedQueue(_opts.attemptId);
      }
    }

    function _startFlushLoop() {
      if (_destroyed || !_opts) return;

      var interval = Number.isFinite(_opts.flushIntervalMs) ? _opts.flushIntervalMs : 1500;
      interval = clamp(interval, 250, 10000);

      if (_flushTimer) {
        clearInterval(_flushTimer);
        _flushTimer = null;
      }

      _flushTimer = setInterval(function () {
        _trySendQueueOnce();
      }, interval);
    }

    function _send(eventType, payload, occurredAtIso) {
      if (_destroyed || !_opts) return;
      var ev = _buildBaseEvent(eventType, payload, occurredAtIso);
      _enqueue(ev);
      // Try to flush quickly (but still goes through queue+retry).
      _trySendQueueOnce();
    }

    // -------------------------
    // Browser listeners
    // -------------------------

    function _handleVisibilityChange() {
      if (_destroyed) return;
      _send('visibility_change', {
        state: document.visibilityState,
        reason: 'document.visibilitychange',
      });
    }

    function _handleWindowFocus() {
      if (_destroyed) return;
      _send('focus', { state: 'focused' });
    }

    function _handleWindowBlur() {
      if (_destroyed) return;
      _send('blur', { state: 'blurred' });
    }

    function _markActivity() {
      if (_destroyed) return;

      var nowMs = Date.now();
      _lastActivityAtMs = nowMs;

      // If we were idle, we end the idle period now.
      if (_idle) {
        _idle = false;

        var idleEndMs = nowMs;
        var idleDurationS = Math.max(0, (idleEndMs - _idleStartedAtMs) / 1000.0);

        // IMPORTANT: For compatibility with your feature engine, we emit an "idle" event
        // with duration_seconds for the *whole* idle period, then an "active" event.
        _send('idle_state', {
          state: 'idle',
          duration_seconds: idleDurationS,
          reason: 'inactivity',
        }, new Date(_idleStartedAtMs).toISOString());

        _send('idle_state', {
          state: 'active',
          reason: 'inactivity',
        }, new Date(idleEndMs).toISOString());
      }

      _armIdleTimer();
    }

    function _armIdleTimer() {
      if (_destroyed || !_opts) return;

      var idleTimeoutMs = Number.isFinite(_opts.idleTimeoutMs) ? _opts.idleTimeoutMs : 30000;
      idleTimeoutMs = clamp(idleTimeoutMs, 5000, 10 * 60 * 1000);

      if (_idleTimeout) {
        clearTimeout(_idleTimeout);
        _idleTimeout = null;
      }

      _idleTimeout = setTimeout(function () {
        if (_destroyed) return;

        // We became idle.
        _idle = true;
        _idleStartedAtMs = _lastActivityAtMs || Date.now();

        _log('idle started');
      }, idleTimeoutMs);
    }

    function _detectActiveQuestionIdFromDom() {
      // We try to infer the question_id from the focused element.
      // This keeps the SDK easy to embed without complex integration.
      try {
        var el = document.activeElement;
        if (!el) return null;
        var qid = el.getAttribute('data-question-id') || el.getAttribute('data-qid');
        return qid ? String(qid) : null;
      } catch (e) {
        return null;
      }
    }

    function _handlePaste(ev) {
      if (_destroyed) return;

      // PRIVACY: We read text ONLY to compute a length and then immediately discard it.
      // We do NOT store or transmit clipboard text.
      var textLen = 0;
      try {
        if (ev && ev.clipboardData) {
          var t = ev.clipboardData.getData('text');
          textLen = t ? t.length : 0;
        }
      } catch (e) {
        textLen = 0;
      }

      var qid = _detectActiveQuestionIdFromDom() || _activeQuestionId;
      var sizeCat = pickPasteSizeCategory(textLen);

      if (qid) {
        _lastPasteAtMsByQ.set(String(qid), Date.now());
        _lastPasteSizeByQ.set(String(qid), sizeCat);
      }

      _send('clipboard', {
        action: 'paste',
        size: sizeCat,
        question_id: qid || undefined,
      });
    }

    function _handleCopy() {
      if (_destroyed) return;
      var qid = _detectActiveQuestionIdFromDom() || _activeQuestionId;

      _send('clipboard', {
        action: 'copy',
        size: 'unknown',
        question_id: qid || undefined,
      });
    }

    function _handleOnline() {
      _log('online; flushing queue');
      _trySendQueueOnce();
    }

    // -------------------------
    // Public API
    // -------------------------

    function init(options) {
      if (!options || !options.baseUrl) throw new Error('baseUrl is required');
      if (!options.attemptId) throw new Error('attemptId is required');

      _opts = {
        baseUrl: String(options.baseUrl),
        attemptId: String(options.attemptId),

        candidateId: safeStr(options.candidateId),
        candidateName: safeStr(options.candidateName),
        candidateEmail: safeStr(options.candidateEmail),

        assessmentId: safeStr(options.assessmentId),
        assessmentName: safeStr(options.assessmentName),

        devMode: !!options.devMode,
        idleTimeoutMs: options.idleTimeoutMs,
        typingStopMs: options.typingStopMs,
        flushIntervalMs: options.flushIntervalMs,
        maxQueueSize: options.maxQueueSize,
        maxRetries: options.maxRetries,
      };

      _destroyed = false;

      // Load persisted queue (e.g., if the page refreshed while offline).
      _queue = tryLoadQueue(_opts.attemptId);

      _lastActivityAtMs = Date.now();
      _idle = false;
      _idleStartedAtMs = 0;

      // Listeners
      document.addEventListener('visibilitychange', _handleVisibilityChange);
      window.addEventListener('focus', _handleWindowFocus);
      window.addEventListener('blur', _handleWindowBlur);

      // Activity events for idle detection
      window.addEventListener('mousemove', _markActivity, { passive: true });
      window.addEventListener('keydown', _markActivity, { passive: true });
      window.addEventListener('scroll', _markActivity, { passive: true });
      window.addEventListener('mousedown', _markActivity, { passive: true });
      window.addEventListener('touchstart', _markActivity, { passive: true });

      document.addEventListener('paste', _handlePaste);
      document.addEventListener('copy', _handleCopy);

      window.addEventListener('online', _handleOnline);

      _armIdleTimer();
      _startFlushLoop();

      _log('initialized; queued events=', _queue.length);
      return RiskTelemetry;
    }

    function startExam() {
      _send('exam_started', {});
      return RiskTelemetry;
    }

    function endExam() {
      _send('exam_submitted', {});
      // Best-effort flush before leaving.
      _trySendQueueOnce();
      return RiskTelemetry;
    }

    function enterQuestion(questionId) {
      var qid = String(questionId || '');
      if (!qid) return RiskTelemetry;

      _activeQuestionId = qid;

      _send('question_view', {
        question_id: qid,
        action: 'enter',
      });

      return RiskTelemetry;
    }

    function leaveQuestion(questionId) {
      var qid = String(questionId || '');
      if (!qid) return RiskTelemetry;

      if (_activeQuestionId === qid) {
        _activeQuestionId = null;
      }

      _send('question_view', {
        question_id: qid,
        action: 'leave',
      });

      return RiskTelemetry;
    }

    function _sendTypingStarted(questionId) {
      _send('typing_started', {
        question_id: questionId,
      });
    }

    function _sendTypingStopped(questionId, durationSeconds, keystrokes) {
      _send('typing_stopped', {
        question_id: questionId,
        duration_seconds: durationSeconds,
        keystrokes: keystrokes,
      });
    }

    function trackAnswerChange(questionId, answerValue) {
      var qid = String(questionId || '');
      if (!qid) return RiskTelemetry;

      // PRIVACY: do not store or send the actual answer text.
      var answerLen = 0;
      try {
        if (typeof answerValue === 'string') {
          answerLen = answerValue.length;
        } else if (answerValue === null || answerValue === undefined) {
          answerLen = 0;
        } else {
          // If a non-string is passed (e.g. number), only use a string length.
          answerLen = String(answerValue).length;
        }
      } catch (e) {
        answerLen = 0;
      }

      // Typing session logic: treat answer changes as "typing activity".
      var nowMs = Date.now();
      var typingStopMs = Number.isFinite(_opts && _opts.typingStopMs) ? _opts.typingStopMs : 1500;
      typingStopMs = clamp(typingStopMs, 350, 8000);

      if (!_typingStartAtMsByQ.has(qid)) {
        _typingStartAtMsByQ.set(qid, nowMs);
        _typingKeystrokesByQ.set(qid, 0);
        _sendTypingStarted(qid);
      }

      // We count "answer change" events as input events; keystrokes are approximated.
      _typingKeystrokesByQ.set(qid, (_typingKeystrokesByQ.get(qid) || 0) + 1);

      if (_typingTimersByQ.has(qid)) {
        clearTimeout(_typingTimersByQ.get(qid));
      }

      _typingTimersByQ.set(
        qid,
        setTimeout(function () {
          var startMs = _typingStartAtMsByQ.get(qid) || nowMs;
          var durS = Math.max(0, (Date.now() - startMs) / 1000.0);
          var ks = _typingKeystrokesByQ.get(qid) || 0;

          _typingStartAtMsByQ.delete(qid);
          _typingKeystrokesByQ.delete(qid);
          _typingTimersByQ.delete(qid);

          _sendTypingStopped(qid, durS, ks);
        }, typingStopMs)
      );

      // Detect whether this change is likely from a recent paste.
      var pasteAtMs = _lastPasteAtMsByQ.get(qid) || 0;
      var pasteRecentMs = pasteAtMs ? (nowMs - pasteAtMs) : null;
      var pasteSize = _lastPasteSizeByQ.get(qid) || null;

      var changeType = 'input';
      if (pasteRecentMs !== null && pasteRecentMs >= 0 && pasteRecentMs <= 2000) {
        changeType = 'paste';
      }

      _send('answer_change', {
        question_id: qid,
        answer_length: answerLen,
        change_type: changeType,
        paste_recent_ms: pasteRecentMs,
        paste_size: pasteSize,
      });

      // Any input counts as activity for idle detection.
      _markActivity();

      return RiskTelemetry;
    }

    function destroy() {
      if (_destroyed) return;
      _destroyed = true;

      try {
        document.removeEventListener('visibilitychange', _handleVisibilityChange);
        window.removeEventListener('focus', _handleWindowFocus);
        window.removeEventListener('blur', _handleWindowBlur);

        window.removeEventListener('mousemove', _markActivity);
        window.removeEventListener('keydown', _markActivity);
        window.removeEventListener('scroll', _markActivity);
        window.removeEventListener('mousedown', _markActivity);
        window.removeEventListener('touchstart', _markActivity);

        document.removeEventListener('paste', _handlePaste);
        document.removeEventListener('copy', _handleCopy);

        window.removeEventListener('online', _handleOnline);
      } catch (e) {
        // ignore
      }

      if (_flushTimer) {
        clearInterval(_flushTimer);
        _flushTimer = null;
      }

      if (_idleTimeout) {
        clearTimeout(_idleTimeout);
        _idleTimeout = null;
      }

      // Clear typing timers
      try {
        _typingTimersByQ.forEach(function (t) { clearTimeout(t); });
      } catch (e) {
        // ignore
      }
      _typingTimersByQ.clear();
      _typingStartAtMsByQ.clear();
      _typingKeystrokesByQ.clear();

      _activeQuestionId = null;

      _log('destroyed');
    }

    return {
      init: init,
      startExam: startExam,
      endExam: endExam,
      enterQuestion: enterQuestion,
      leaveQuestion: leaveQuestion,
      trackAnswerChange: trackAnswerChange,
      destroy: destroy,
    };
  })();

  // Expose globally.
  global.RiskTelemetry = RiskTelemetry;

})(typeof window !== 'undefined' ? window : this);
