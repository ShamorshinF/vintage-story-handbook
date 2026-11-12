/* Handbook site: index by mod, search, page view. No dependencies, no network calls except its own data files. */
(function () {
  'use strict';

  var view = document.getElementById('view');
  var q = document.getElementById('q');
  var foot = document.getElementById('foot');
  var prefsBtn = document.getElementById('prefs-btn');
  var prefsBox = document.getElementById('prefs');

  var D = null;                 // data/handbook.json
  var itemsPromise = null;      // data/items.json, loaded when first needed
  var itemByCode = null;        // code -> {name, mod, pos}
  var itemLists = null;         // mod index -> [[code, name], ...]
  var chunkSize = 500;
  var guideById = new Map();
  var chunkCache = new Map();
  var BATCH = 200;
  var scrolls = {};
  var shown = {};
  var navKey = '0';
  var navCount = 0;
  var renderToken = 0;

  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }
  function enc(s) { return encodeURIComponent(s); }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  function plainText(h) {
    return h.replace(/<br\s*\/?>/g, ' ').replace(/<[^>]*>/g, '')
      .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&amp;/g, '&')
      .replace(/\s+/g, ' ');
  }

  // ------------------------------------------------------------------ data
  function getJson(url) {
    return fetch(url, { credentials: 'omit' }).then(function (r) {
      if (!r.ok) { throw new Error(url + ' ' + r.status); }
      return r.json();
    });
  }

  function loadItems() {
    if (!D.meta.items) { return Promise.resolve(false); }
    if (!itemsPromise) {
      itemsPromise = getJson('data/items.json').then(function (I) {
        chunkSize = I.chunk;
        itemByCode = new Map();
        itemLists = {};
        I.mods.forEach(function (pair) {
          var mi = pair[0], list = pair[1];
          itemLists[mi] = list;
          for (var i = 0; i < list.length; i++) {
            itemByCode.set(list[i][0], { name: list[i][1], mod: mi, pos: i, low: null });
          }
        });
        return true;
      }).catch(function (e) { itemsPromise = null; throw e; });
    }
    return itemsPromise;
  }

  function loadChunk(mi, pos) {
    var k = Math.floor(pos / chunkSize);
    var url = 'data/items/' + D.mods[mi].id + '-' + k + '.json';
    if (!chunkCache.has(url)) {
      chunkCache.set(url, getJson(url).catch(function (e) { chunkCache.delete(url); throw e; }));
    }
    return chunkCache.get(url).then(function (arr) { return arr[pos - k * chunkSize] || {}; });
  }

  // ------------------------------------------------------------------ routing
  function parseHash() {
    var h = location.hash.replace(/^#/, '');
    var i = h.indexOf('/');
    var kind = i < 0 ? h : h.slice(0, i);
    var arg = i < 0 ? '' : h.slice(i + 1);
    try { arg = decodeURIComponent(arg); } catch (e) { /* keep as typed */ }
    return { kind: kind || 'g', arg: arg };
  }

  function route() {
    if (!D) { return; }
    var r = parseHash();
    var st = history.state;
    var revisit = !!(st && st.k);
    if (revisit) { navKey = st.k; }
    else {
      navKey = 'n' + (++navCount) + '-' + Date.now();
      try { history.replaceState({ k: navKey }, '', location.href); } catch (e) { /* file: pages may refuse */ }
    }
    var token = ++renderToken;
    var done = function () {
      if (token !== renderToken) { return; }
      var y = revisit ? (scrolls[navKey] || 0) : 0;
      window.scrollTo(0, y);
    };
    if (r.kind !== 's') { q.value = ''; }
    if (!prefsBox.hidden) { prefsBox.hidden = true; prefsBtn.setAttribute('aria-expanded', 'false'); }
    var p;
    if (r.kind === 'p') { p = showPage(r.arg, token); }
    else if (r.kind === 's') { p = showSearch(r.arg, token); }
    else if (r.kind === 'i') { p = showItemMods(token); }
    else if (r.kind === 'm') { p = showModItems(r.arg, token); }
    else { p = showGuides(); }
    Promise.resolve(p).then(done, function (e) {
      if (token !== renderToken) { return; }
      view.innerHTML = '<p class="note">' + (navigator.onLine === false
        ? 'You are offline and this page has not been opened on this phone before. Guides and search still work.'
        : 'That part of the handbook could not be loaded. Check the connection and try again.') + '</p>';
      if (window.console) { console.error(e); }
    });
  }

  function setView(htmlText, title) {
    view.innerHTML = htmlText;
    document.title = title ? title + ' - Handbook' : 'Handbook';
  }

  /* The game first, then every mod by its display name. */
  function modsInOrder() {
    if (!D._order) {
      D._order = D.mods.slice(1).sort(function (a, b) {
        return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : a.name.toLowerCase() > b.name.toLowerCase() ? 1 : 0;
      });
      D._order.unshift(D.mods[0]);
    }
    return D._order;
  }

  function tabs(active) {
    if (!D.meta.items) { return ''; }
    return '<nav class="tabs" aria-label="Sections">' +
      '<a href="#g"' + (active === 'g' ? ' aria-current="page"' : '') + '>Guides</a>' +
      '<a href="#i"' + (active === 'i' ? ' aria-current="page"' : '') + '>Blocks and items</a></nav>';
  }

  // ------------------------------------------------------------------ views
  function showGuides() {
    var out = [tabs('g')];
    modsInOrder().forEach(function (m) {
      if (!m.g.length) { return; }
      out.push('<details class="mod" open><summary><span>' + esc(m.name) + '</span><span class="count">' +
        plural(m.g.length, 'guide', 'guides') + '</span></summary><ul class="rows">');
      m.g.forEach(function (gi) {
        var g = D.guides[gi];
        out.push('<li><a href="#p/' + enc(g.id) + '">' + esc(g.t) + '</a></li>');
      });
      out.push('</ul></details>');
    });
    setView(out.join(''), '');
  }

  function showItemMods(token) {
    var out = [tabs('i'), '<ul class="rows plain">'];
    modsInOrder().forEach(function (m) {
      if (!m.n) { return; }
      out.push('<li><a href="#m/' + enc(m.id) + '">' + esc(m.name) + '<span class="sub">' +
        plural(m.n, 'block or item', 'blocks and items') + '</span></a></li>');
    });
    out.push('</ul>');
    setView(out.join(''), 'Blocks and items');
  }

  function modIndexById(id) {
    for (var i = 0; i < D.mods.length; i++) { if (D.mods[i].id === id) { return i; } }
    return -1;
  }

  function itemRow(code, name, sub) {
    return '<li><a href="#p/' + enc(code) + '">' + esc(name) + (sub ? '<span class="sub">' + esc(sub) + '</span>' : '') + '</a></li>';
  }

  /* Long lists are added in batches so a phone never has to lay out thousands of rows at once. */
  function batched(container, rows, key) {
    var ul = container.querySelector('ul.rows');
    var btn = container.querySelector('.more-btn');
    var n = 0;
    function add(upTo) {
      var parts = [];
      for (; n < rows.length && n < upTo; n++) { parts.push(rows[n]()); }
      ul.insertAdjacentHTML('beforeend', parts.join(''));
      shown[key] = n;
      if (btn) {
        btn.hidden = n >= rows.length;
        btn.textContent = 'Show more (' + (rows.length - n) + ' left)';
      }
    }
    add(Math.max(BATCH, shown[key] || 0));
    if (btn) { btn.addEventListener('click', function () { add(n + BATCH); }); }
  }

  function showModItems(id, token) {
    return loadItems().then(function () {
      if (token !== renderToken) { return; }
      var mi = modIndexById(id);
      var list = (itemLists && itemLists[mi]) || [];
      var m = D.mods[mi] || { name: id };
      setView('<div class="crumbs"><a href="#i">&lsaquo; Blocks and items</a></div>' +
        '<div class="page"><h1>' + esc(m.name) + '</h1><p class="meta">' + plural(list.length, 'block or item', 'blocks and items') +
        '</p></div><div id="lst"><ul class="rows plain"></ul><button type="button" class="more-btn" hidden></button></div>', m.name);
      var rows = list.map(function (it, i) {
        return function () {
          var twin = (i > 0 && list[i - 1][1] === it[1]) || (i + 1 < list.length && list[i + 1][1] === it[1]);
          return itemRow(it[0], it[1], twin ? it[0].replace(/^(block|item)-([a-z0-9]+:)?/, '') : '');
        };
      });
      if (rows.length) { batched(document.getElementById('lst'), rows, navKey); }
    });
  }

  function crumbs(extra) {
    return '<div class="crumbs"><button type="button" data-back>&lsaquo; Back</button>' + (extra || '') + '</div>';
  }

  function showPage(id, token) {
    var g = guideById.get(id);
    if (g) {
      var m = D.mods[g.m];
      setView(crumbs('<a href="#g">Guides</a>') + '<article class="page"><h1>' + esc(g.t) + '</h1><p class="meta">' +
        esc(m.name) + '</p><div class="body">' + g.h + '</div></article>', g.t);
      return;
    }
    return loadItems().then(function (ok) {
      if (token !== renderToken) { return; }
      var it = ok && itemByCode.get(id);
      if (!it) {
        setView(crumbs('<a href="#g">Guides</a>') + '<p class="note">This handbook page is not in this copy: <span class="codeline">' +
          esc(id) + '</span></p>', 'Not found');
        return;
      }
      return loadChunk(it.mod, it.pos).then(function (d) {
        if (token !== renderToken) { return; }
        renderItem(id, it, d);
      });
    });
  }

  function itemLink(code, label, cls) {
    var known = code && itemByCode && itemByCode.has(code);
    var text = esc(label || (known ? itemByCode.get(code).name : code));
    return known ? '<a class="' + cls + '" href="#p/' + enc(code) + '">' + text + '</a>' : '<span class="' + cls + '">' + text + '</span>';
  }

  function renderRecipe(r) {
    var out = ['<div class="recipe">'];
    var ings = r.i || [];
    function label(ing) { return (ing[2] > 1 ? ing[2] + '× ' : '') + ing[0]; }
    if (r.s) {
      out.push('<div class="rnote">Any arrangement in the crafting grid:</div><ul class="ings">');
      ings.forEach(function (ing) {
        out.push('<li>' + itemLink(ing[1], label(ing), 'ing') + (ing[3] ? ' <span class="rnote">' + esc(ing[3]) + '</span>' : '') + '</li>');
      });
      out.push('</ul>');
    } else {
      out.push('<div class="grid" style="grid-template-columns:repeat(' + (r.w || 3) + ',minmax(0,1fr))">');
      (r.c || []).forEach(function (ci) {
        if (ci < 0 || !ings[ci]) { out.push('<span class="cell empty"></span>'); }
        else { out.push(itemLink(ings[ci][1], label(ings[ci]), 'cell')); }
      });
      out.push('</div>');
      var notes = ings.filter(function (ing) { return ing[3]; });
      if (notes.length) {
        out.push('<div class="rnote">' + notes.map(function (ing) { return esc(ing[0] + ': ' + ing[3]); }).join('<br>') + '</div>');
      }
    }
    out.push('<div class="out">Makes ' + (r.q || 1) + '</div>');
    if (r.n) { out.push('<div class="rnote">' + esc(r.n) + '</div>'); }
    out.push('</div>');
    return out.join('');
  }

  function renderItem(code, it, d) {
    var m = D.mods[it.mod];
    var out = [crumbs('<a href="#m/' + enc(m.id) + '">' + esc(m.name) + '</a>'),
      '<article class="page"><h1>' + esc(it.name) + '</h1><p class="meta">' + esc(m.name) +
      ' &middot; <span class="codeline">' + esc(code) + '</span></p>'];
    if (d.d) { out.push('<div class="body">' + d.d + '</div>'); }
    (d.x || []).forEach(function (sec) {
      out.push((sec[0] ? '<h2 class="sec">' + esc(sec[0]) + '</h2>' : '') + '<div class="body">' + sec[1] + '</div>');
    });
    if (d.r && d.r.length) {
      out.push('<h2 class="sec">Crafting</h2>');
      d.r.forEach(function (r) { out.push(renderRecipe(r)); });
    }
    if (d.o && d.o.length) {
      out.push('<h2 class="sec">Also made by</h2><div class="body">' + d.o.map(esc).join('<br>') + '</div>');
    }
    if (d.u && d.u.length) {
      out.push('<h2 class="sec">Used to craft</h2><div id="lst"><ul class="rows plain"></ul>' +
        '<button type="button" class="more-btn" hidden></button></div>');
    }
    if (!d.d && !(d.x && d.x.length) && !(d.r && d.r.length) && !(d.u && d.u.length) && !(d.o && d.o.length)) {
      out.push('<p class="note">No written description or grid recipe. The game works out the rest of this page ' +
        'while it runs, so it is only in the in-game handbook.</p>');
    }
    out.push('</article>');
    setView(out.join(''), it.name);
    if (d.u && d.u.length) {
      var rows = d.u.map(function (c) {
        return function () { var t = itemByCode.get(c); return itemRow(c, t ? t.name : c, ''); };
      });
      BATCH = 60;
      batched(document.getElementById('lst'), rows, navKey + 'u');
      BATCH = 200;
    }
  }

  // ------------------------------------------------------------------ search
  function tokens(s) { return s.toLowerCase().split(/\s+/).filter(Boolean); }

  function snippet(text, low, toks) {
    var at = -1;
    for (var i = 0; i < toks.length; i++) { var p = low.indexOf(toks[i]); if (p >= 0 && (at < 0 || p < at)) { at = p; } }
    if (at < 0) { return ''; }
    var a = Math.max(0, at - 50), b = Math.min(text.length, at + 110);
    var s = (a > 0 ? '…' : '') + text.slice(a, b) + (b < text.length ? '…' : '');
    var h = esc(s);
    toks.forEach(function (t) {
      var rx = new RegExp(esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
      h = h.replace(rx, function (m0) { return '\u0001' + m0 + '\u0002'; });
    });
    return h.replace(/\u0001/g, '<mark>').replace(/\u0002/g, '</mark>');
  }

  function searchGuides(toks) {
    var hits = [];
    D.guides.forEach(function (g) {
      if (g._t === undefined) { g._t = g.t.toLowerCase(); g._x = plainText(g.h); g._xl = g._x.toLowerCase(); }
      var inTitle = true, inAll = true;
      for (var i = 0; i < toks.length; i++) {
        var t = toks[i];
        if (g._t.indexOf(t) < 0) { inTitle = false; if (g._xl.indexOf(t) < 0) { inAll = false; break; } }
      }
      if (inAll) { hits.push({ g: g, rank: inTitle ? 0 : 1 }); }
    });
    hits.sort(function (a, b) { return a.rank - b.rank; });
    return hits;
  }

  function searchItems(toks, whole) {
    var hits = [];
    itemByCode.forEach(function (it, code) {
      if (it.low === null) { it.low = it.name.toLowerCase(); }
      for (var i = 0; i < toks.length; i++) {
        if (it.low.indexOf(toks[i]) < 0 && code.indexOf(toks[i]) < 0) { return; }
      }
      var rank = it.low === whole ? 0 : it.low.indexOf(whole) === 0 ? 1 : it.low.indexOf(whole) > 0 ? 2 : 3;
      hits.push({ code: code, it: it, rank: rank });
    });
    hits.sort(function (a, b) { return a.rank - b.rank || a.it.name.length - b.it.name.length || (a.it.name < b.it.name ? -1 : 1); });
    return hits;
  }

  function showSearch(text, token) {
    if (document.activeElement !== q && q.value !== text) { q.value = text; }
    var toks = tokens(text);
    if (!toks.length) { showGuides(); return; }
    var gh = searchGuides(toks);
    var out = ['<h2 class="sec">Guides (' + gh.length + ')</h2>'];
    if (gh.length) {
      out.push('<ul class="rows plain">');
      gh.forEach(function (h) {
        var g = h.g;
        var sn = snippet(g._x, g._xl, toks);
        out.push('<li><a href="#p/' + enc(g.id) + '">' + esc(g.t) + '<span class="sub">' + esc(D.mods[g.m].name) + '</span>' +
          (sn ? '<span class="sub snip">' + sn + '</span>' : '') + '</a></li>');
      });
      out.push('</ul>');
    } else {
      out.push('<p class="note">No guide mentions that.</p>');
    }
    if (D.meta.items) {
      out.push('<h2 class="sec" id="ih">Blocks and items</h2><div id="lst"><p class="note">Searching&hellip;</p></div>');
    }
    setView(out.join(''), 'Search: ' + text);
    if (!D.meta.items) { return; }
    return loadItems().then(function () {
      if (token !== renderToken) { return; }
      var ih = searchItems(toks, toks.join(' '));
      document.getElementById('ih').textContent = 'Blocks and items (' + ih.length + ')';
      var box = document.getElementById('lst');
      if (!ih.length) { box.innerHTML = '<p class="note">No block or item has that in its name.</p>'; return; }
      box.innerHTML = '<ul class="rows plain"></ul><button type="button" class="more-btn" hidden></button>';
      var rows = ih.map(function (h) {
        return function () { return itemRow(h.code, h.it.name, D.mods[h.it.mod].name); };
      });
      BATCH = 50;
      batched(box, rows, navKey);
      BATCH = 200;
    });
  }

  // ------------------------------------------------------------------ events
  var typing = null;
  q.addEventListener('input', function () {
    clearTimeout(typing);
    typing = setTimeout(function () {
      var v = q.value.trim();
      var target = v ? '#s/' + enc(v) : '#g';
      if (location.hash === target) { return; }
      if (parseHash().kind === 's') { location.replace(target); } else { location.hash = target; }
    }, 180);
  });
  q.addEventListener('keydown', function (e) { if (e.key === 'Enter') { q.blur(); } });

  view.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('[data-back]') : null;
    if (b) {
      if (history.length > 1 && navCount > 1) { history.back(); } else { location.hash = '#g'; }
    }
  });

  var ticking = false;
  window.addEventListener('scroll', function () {
    if (ticking) { return; }
    ticking = true;
    setTimeout(function () { scrolls[navKey] = window.scrollY; ticking = false; }, 120);
  }, { passive: true });
  if ('scrollRestoration' in history) { history.scrollRestoration = 'manual'; }
  window.addEventListener('hashchange', route);

  function syncPrefs() {
    var P = window.HandbookPrefs;
    Array.prototype.forEach.call(prefsBox.querySelectorAll('[data-pref]'), function (b) {
      b.setAttribute('aria-pressed', String(P.get(b.getAttribute('data-pref')) === b.getAttribute('data-val')));
    });
  }
  prefsBtn.addEventListener('click', function () {
    var open = prefsBox.hidden;
    prefsBox.hidden = !open;
    prefsBtn.setAttribute('aria-expanded', String(open));
  });
  prefsBox.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('button') : null;
    if (!b) { return; }
    var P = window.HandbookPrefs;
    if (b.hasAttribute('data-pref')) { P.set(b.getAttribute('data-pref'), b.getAttribute('data-val')); }
    else if (b.hasAttribute('data-step')) {
      var step = parseInt(b.getAttribute('data-step'), 10);
      var cur = parseInt(P.get('size'), 10) || 0;
      P.set('size', step === 0 ? 0 : Math.max(-2, Math.min(5, cur + step)));
    }
    syncPrefs();
  });
  syncPrefs();

  // ------------------------------------------------------------------ start
  /* Offline reading and "Add to Home screen". Browsers only allow this on https or on this computer. */
  if ('serviceWorker' in navigator &&
      (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1')) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('sw.js').catch(function () { /* the site works without it */ });
    });
  }

  getJson('data/handbook.json').then(function (data) {
    D = data;
    D.guides.forEach(function (g) { guideById.set(g.id, g); });
    var m = D.meta;
    foot.innerHTML = 'Vintage Story ' + esc(m.game) + ' with ' + plural(m.mods, 'mod', 'mods') + ': ' +
      plural(m.guides, 'guide', 'guides') + (m.items ? ', ' + plural(m.items, 'block or item page', 'block and item pages') : '') +
      '. Built ' + esc(m.generated) + '.<br>Handbook text belongs to the game and to each mod&rsquo;s authors. ' +
      'This is a personal reading copy.';
    route();
  }).catch(function (e) {
    view.innerHTML = '<p class="note">The handbook data could not be loaded. If you opened this file straight from a folder, ' +
      'start it with the small local server described in the README instead.</p>';
    if (window.console) { console.error(e); }
  });
})();
