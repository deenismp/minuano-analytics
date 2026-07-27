/*!
 * minuano browser snippet -- Apache 2.0
 *
 * Zero dependencies. Cookies, not localStorage, because GTM sandboxed templates can read and
 * write cookies -- one storage model across both install methods.
 *
 * Session rules are the reference platform's, verbatim: 30 minutes of inactivity, no midnight reset, no split on
 * a new campaign, session_id = unix seconds at session start.
 *
 * Configure before loading:
 *   window.minuanoConfig = { endpoint: 'https://collect.example.com/collect' };
 * or with a data attribute on the script tag:
 *   <script src="minuano.js" data-endpoint="https://collect.example.com/collect"></script>
 *
 * Custom events:
 *   minuano.track('signup_completed', { plan: 'pro', seats: 3 });
 * Calls made before this file finishes loading are not lost, as long as the install snippet
 * declared the queue stub: window.minuano = window.minuano || [];
 */
(function (w, d) {
  var cfg = w.minuanoConfig || {};
  var script = d.currentScript;
  var ENDPOINT = cfg.endpoint || (script && script.getAttribute('data-endpoint')) || '/collect';
  var DOMAIN = cfg.cookieDomain || (script && script.getAttribute('data-cookie-domain')) || '';
  var SESSION_SECONDS = (cfg.sessionMinutes || 30) * 60;
  var TWO_YEARS = 63072000;
  var SIX_MONTHS = 15552000;
  var NAME_RE = /^[a-z][a-z0-9_]{0,39}$/;
  var MAX_PARAMS = 25;

  function warn(message) {
    if (w.console && w.console.warn) w.console.warn('[minuano] ' + message);
  }

  function getCookie(name) {
    var match = d.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]*)');
    return match ? decodeURIComponent(match[2]) : '';
  }

  function setCookie(name, value, seconds) {
    d.cookie = name + '=' + encodeURIComponent(value) +
      ';path=/;max-age=' + seconds + ';SameSite=Lax' +
      (DOMAIN ? ';domain=' + DOMAIN : '') +
      (w.location.protocol === 'https:' ? ';Secure' : '');
  }

  function uid() {
    return 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  // --- identity -----------------------------------------------------------------------
  var isNewVisitor = !getCookie('_mnu_id');
  var anonymousId = getCookie('_mnu_id') || uid();
  setCookie('_mnu_id', anonymousId, TWO_YEARS);

  // The session cookie's own expiry *is* the inactivity window: refreshed on every event,
  // so when it lapses the session is over. No timestamp arithmetic.
  function sessionId() {
    var id = getCookie('_mnu_ses') || String(Math.floor(Date.now() / 1000));
    setCookie('_mnu_ses', id, SESSION_SECONDS);
    return id;
  }

  // --- campaign -----------------------------------------------------------------------
  var query = new URLSearchParams(w.location.search);

  function observedCampaign() {
    var c = {
      source: query.get('utm_source'),
      medium: query.get('utm_medium'),
      campaign: query.get('utm_campaign'),
      content: query.get('utm_content'),
      term: query.get('utm_term')
    };
    return (c.source || c.medium || c.campaign || c.content || c.term) ? c : null;
  }

  var observed = observedCampaign();
  var firstTouch = getCookie('_mnu_ft');
  if (!firstTouch) {
    firstTouch = JSON.stringify(observed || {});
    setCookie('_mnu_ft', firstTouch, TWO_YEARS);
  }
  if (observed) setCookie('_mnu_lt', JSON.stringify(observed), SIX_MONTHS);

  // A brand-new visitor's first event carries first-touch; everything after carries
  // last-touch. Downstream filters on campaign.attribution instead of reconstructing
  // first-touch with a window function over event ordering.
  function campaign() {
    var raw = isNewVisitor ? firstTouch : (getCookie('_mnu_lt') || firstTouch);
    var c;
    try { c = JSON.parse(raw) || {}; } catch (e) { c = {}; }
    c.attribution = isNewVisitor ? 'first_touch' : 'last_touch';
    isNewVisitor = false;
    return c;
  }

  // --- events -------------------------------------------------------------------------
  function cleanParams(params) {
    var out = {}, keys = Object.keys(params || {}), n = 0;
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i], value = params[key], type = typeof value;
      if (value !== null && type !== 'string' && type !== 'number' && type !== 'boolean') {
        warn('dropped param "' + key + '": params are flat scalars only');
        continue;
      }
      if (n++ >= MAX_PARAMS) { warn('param cap ' + MAX_PARAMS); break; }
      out[key] = value;
    }
    return out;
  }

  function build(name, params) {
    return {
      schema_version: '0',
      event_name: name,
      event_timestamp: new Date().toISOString(),
      anonymous_id: anonymousId,
      session_id: sessionId(),
      user_id: cfg.userId || null,
      page: {
        url: w.location.href,
        path: w.location.pathname,
        title: d.title,
        referrer: d.referrer || null
      },
      campaign: campaign(),
      device: {
        platform: 'web',
        user_agent: w.navigator.userAgent,
        language: w.navigator.language,
        screen_width: w.screen.width,
        screen_height: w.screen.height
      },
      params: cleanParams(params)
    };
  }

  function base64url(text) {
    var bytes = encodeURIComponent(text).replace(/%([0-9A-F]{2})/g, function (_, hex) {
      return String.fromCharCode('0x' + hex);
    });
    return btoa(bytes).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  // sendBeacon survives page unload and, sent as text/plain, skips the CORS preflight.
  // fetch(keepalive) is the fallback; the pixel is the last resort and the path a GTM
  // custom template would take, since sandboxed `sendPixel` is GET only.
  function send(event) {
    var body = JSON.stringify(event);
    try {
      if (w.navigator.sendBeacon &&
          w.navigator.sendBeacon(ENDPOINT, new Blob([body], { type: 'text/plain;charset=UTF-8' }))) return;
    } catch (e) { /* fall through */ }
    try {
      if (w.fetch) {
        w.fetch(ENDPOINT, {
          method: 'POST', body: body, keepalive: true, mode: 'cors',
          headers: { 'Content-Type': 'text/plain;charset=UTF-8' }
        });
        return;
      }
    } catch (e) { /* fall through */ }
    new Image().src = ENDPOINT + '?e=' + base64url(body);
  }

  function track(name, params) {
    if (!NAME_RE.test(name)) return warn('ignored event "' + name + '": name must match ' + NAME_RE);
    send(build(name, params));
  }

  // --- boot ---------------------------------------------------------------------------
  var queued = Array.isArray(w.minuano) ? w.minuano : [];

  w.minuano = {
    track: track,
    anonymousId: function () { return anonymousId; },
    sessionId: function () { return getCookie('_mnu_ses'); }
  };

  var pageParams = {};
  var gclid = query.get('gclid'), fbclid = query.get('fbclid');
  if (gclid) pageParams.gclid = gclid;
  if (fbclid) pageParams.fbclid = fbclid;
  send(build('page_view', pageParams));

  for (var i = 0; i < queued.length; i++) track(queued[i][0], queued[i][1]);
})(window, document);
