const CACHE_NAME = "sven-bed-cache-v5";
const URLS_TO_CACHE = [
  "/",
  "/login",
  "/index.html",
  "/manifest.json",
  "/ico_lg.png",
  "/style.css",
  "/jquery-4.0.0.min.js",
  "/jcanvas.min.js"
];

// Install event  cache core assets, activate immediately
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(URLS_TO_CACHE);
    })
  );
  self.skipWaiting();
});

// Activate event  clean old caches and take over all clients
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) =>
      Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME) {
            return caches.delete(cache);
          }
        })
      )
    ).then(() => self.clients.claim())
  );
});

// Fetch event  network-first for HTML, cache-first for static assets
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const path = url.pathname;
  const isHTML = path === "/" || path === "/index.html" || path === "/login";
  if (isHTML) {
    // Network-first: ensures fresh CSRF tokens on each load
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
  } else {
    // Cache-first for static assets (JS, CSS, images)
    event.respondWith(
      caches.match(event.request).then((response) => {
        return response || fetch(event.request);
      })
    );
  }
});
