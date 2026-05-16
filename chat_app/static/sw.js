// Caches static assets for offline support and faster loading
const CACHE_NAME = 'rooted-v5';
const STATIC_ASSETS = [
  '/',
  '/static/manifest.json',
  '/static/favicon.png',
  'https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=DM+Sans:wght@300;400;500;600;700&display=swap',
  'https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.js'
];

// Install event — cache static shell
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS).catch(() => { });
    })
  );
  self.skipWaiting();
});

// Activate event — clean up old caches (v1 -> v2)
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

// Fetch event — Network First for the root, Cache First for static assets
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  if (event.request.url.includes('/socket.io/')) return;
  if (event.request.url.includes('/api/')) return;

  const url = new URL(event.request.url);

  // Network First strategy for the main page to ensure UI updates reflect
  if (url.pathname === '/') {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Cache First for static assets
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        if (response && response.status === 200) {
          if (url.pathname.startsWith('/static/')) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
        }
        return response;
      });
    })
  );
});
