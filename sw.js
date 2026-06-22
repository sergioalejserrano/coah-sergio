/* Service Worker — Coach Vegabikes
   ESTRATEGIA: network-first para el shell (siempre intenta la versión nueva),
   cache como fallback offline. Los datos cifrados nunca se cachean. */
const CACHE = "coach-v3";  // ← cambiar aquí para forzar invalidación
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  // Borra todos los caches viejos
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);

  // 1) Datos cifrados y llamadas externas → siempre red, nunca cache
  if (url.pathname.endsWith("garmin.enc") ||
      url.pathname.endsWith("meta.json") ||
      url.origin !== self.location.origin) {
    return;
  }

  // 2) App shell → network-first: intenta red, cae a cache si offline
  e.respondWith(
    fetch(e.request)
      .then(response => {
        // Actualiza el cache con la versión nueva
        if (response && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(e.request))  // offline: sirve desde cache
  );
});
