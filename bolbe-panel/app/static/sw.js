// Ana ekrana eklenebilmesi için en küçük service worker. Veriyi önbelleğe almaz;
// panel her zaman güncel veriyi sunucudan okur.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
