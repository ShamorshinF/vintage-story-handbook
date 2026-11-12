/* Reading settings, applied before the first paint. Stored on this device only. */
(function () {
  'use strict';
  var root = document.documentElement;
  var defaults = { font: 'std', theme: 'auto', size: '0' };
  var BAR = { dark: '#382c22', light: '#eadfc8' };      /* the top bar colour, also used for Android's status bar */
  var lightQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

  function read(key) {
    try {
      var v = window.localStorage.getItem('handbook.' + key);
      return v === null ? defaults[key] : v;
    } catch (e) {
      return defaults[key];
    }
  }

  function write(key, value) {
    try { window.localStorage.setItem('handbook.' + key, value); } catch (e) { /* private mode: keep for this visit */ }
  }

  function paintBar(theme) {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) { return; }
    var shown = theme === 'dark' || theme === 'light' ? theme : (lightQuery && lightQuery.matches ? 'light' : 'dark');
    meta.setAttribute('content', BAR[shown]);
  }

  function apply(key, value) {
    if (key === 'font') {
      root.setAttribute('data-font', value === 'dys' ? 'dys' : 'std');
    } else if (key === 'theme') {
      if (value === 'dark' || value === 'light') { root.setAttribute('data-theme', value); }
      else { root.removeAttribute('data-theme'); }
      paintBar(value);
    } else if (key === 'size') {
      var n = Math.max(-2, Math.min(5, parseInt(value, 10) || 0));
      root.style.setProperty('--scale', String(1 + n * 0.1));
    }
  }

  var current = {};
  ['font', 'theme', 'size'].forEach(function (k) { current[k] = read(k); apply(k, current[k]); });

  if (lightQuery) {
    var onChange = function () { paintBar(current.theme); };
    if (lightQuery.addEventListener) { lightQuery.addEventListener('change', onChange); }
    else if (lightQuery.addListener) { lightQuery.addListener(onChange); }
  }

  window.HandbookPrefs = {
    get: function (k) { return current[k]; },
    set: function (k, v) { current[k] = String(v); apply(k, current[k]); write(k, current[k]); }
  };
})();
