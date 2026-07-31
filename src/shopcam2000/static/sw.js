/* Minimal service worker.
 *
 * Deliberately does NOT cache application data. A controller that shows a
 * cached camera state is worse than one that shows nothing: it would tell you a
 * camera is recording when it stopped ten minutes ago. Only the static shell is
 * cached, so the app installs and launches like a native one, and every piece
 * of state still comes live from the server.
 */
/* Bump on every shell change. Fetch is network-first so a live page is never
 * stale, but the cached copy is what an offline phone falls back to — and an
 * offline Controller without the TWAB button would be the wrong thing to keep. */
const SHELL = 'shopcam-shell-v12';
const ASSETS = [
  '/',
  '/static/styles.css',
  '/static/app.js',
  '/static/fonts/cinzel-400.woff2',
  '/static/fonts/cinzel-700.woff2',
  '/static/icons/icon.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/') || event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request).then((hit) => hit || Response.error()))
  );
});
