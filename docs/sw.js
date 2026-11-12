/* Offline support. The network is asked first, so a rebuilt site shows up at once;
   the saved copy is used when the phone is offline or the connection stalls. */
'use strict';

var CACHE = 'handbook-v1';
var SHELL = ['./', 'index.html', 'style.css', 'app.js', 'prefs.js', 'manifest.webmanifest',
  'icons/icon-192.png', 'data/handbook.json', 'data/items.json'];
var SLOW_MS = 4000;

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      // one missing file must not stop the rest from being saved
      return Promise.all(SHELL.map(function (url) { return cache.add(url).catch(function () {}); }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k.indexOf('handbook-') === 0 && k !== CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

function fetchAndStore(request) {
  return fetch(request).then(function (response) {
    if (response.ok && response.type === 'basic') {
      var copy = response.clone();
      caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
    }
    return response;
  });
}

function saved(request) {
  return caches.match(request, { ignoreSearch: true }).then(function (hit) {
    if (hit) { return hit; }
    if (request.mode === 'navigate') { return caches.match('index.html'); }
    return undefined;
  });
}

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') { return; }
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) { return; }

  if (url.pathname.indexOf('/fonts/') >= 0 || url.pathname.indexOf('/icons/') >= 0) {
    // fonts and icons never change between builds: saved copy first
    event.respondWith(saved(request).then(function (hit) { return hit || fetchAndStore(request); }));
    return;
  }

  event.respondWith(new Promise(function (resolve, reject) {
    var settled = false;
    var timer = setTimeout(function () {
      saved(request).then(function (hit) { if (hit && !settled) { settled = true; resolve(hit); } });
    }, SLOW_MS);
    fetchAndStore(request).then(function (response) {
      clearTimeout(timer);
      if (!settled) { settled = true; resolve(response); }
    }, function (err) {
      clearTimeout(timer);
      saved(request).then(function (hit) {
        if (settled) { return; }
        settled = true;
        if (hit) { resolve(hit); } else { reject(err); }
      });
    });
  }));
});
