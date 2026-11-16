// Service worker - działa w tle nawet przy zamkniętej stronie i odbiera powiadomienia push.
// Wiadomość z serwera: {"tytul": "...", "tresc": "...", "adres": "/?szukanie=12"}

self.addEventListener("push", (zdarzenie) => {
  let dane = {};
  try { dane = zdarzenie.data ? zdarzenie.data.json() : {}; } catch { dane = { tresc: zdarzenie.data?.text() }; }
  zdarzenie.waitUntil(self.registration.showNotification(dane.tytul || "Agent ofert pracy", {
    body: dane.tresc || "",
    icon: "/static/ikona-192.png",
    badge: "/static/ikona-192.png",
    data: { adres: dane.adres || "/" },
    tag: "nowe-oferty",        // nowe powiadomienie zastępuje stare, zamiast się piętrzyć
  }));
});

self.addEventListener("notificationclick", (zdarzenie) => {
  zdarzenie.notification.close();
  // tylko adresy z tej strony - nawet gdyby ktoś podrzucił w wiadomości obcy link
  const adres = new URL(zdarzenie.notification.data?.adres || "/", self.location.origin);
  const cel = adres.origin === self.location.origin ? adres.href : self.location.origin + "/";
  zdarzenie.waitUntil((async () => {
    const okna = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const okno of okna) {
      if (okno.url.startsWith(self.location.origin)) { await okno.navigate(cel); return okno.focus(); }
    }
    return clients.openWindow(cel);
  })());
});
