/* Service worker mínimo: cachea la app para que abra offline.
   NUNCA cachea data/garmin.enc (los datos siempre se piden frescos). */
const CACHE = "coach-v1";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks =>
    Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  // datos cifrados y llamadas externas: siempre a la red (nunca cache)
  if (url.pathname.endsWith("garmin.enc") || url.origin !== self.location.origin) {
    return; // deja pasar a la red normal
  }
  // app shell: cache-first
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
