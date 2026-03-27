const CACHE_NAME = "sven-bed-cache-v1";
const URLS_TO_CACHE = [
  "/",
  "/login",
  "/login.html",
  "/index.html",
  "/manifest.json",
  "/ico_lg.png",
  "/jquery-3.6.0.min.js"
];

// Install event  cache core assets
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(URLS_TO_CACHE);
    })
  );
});

// Activate event  clean old caches if needed
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
    )
  );
});

// Fetch event  serve from cache or fetch from network
self.addEventListener("fetch", (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});
