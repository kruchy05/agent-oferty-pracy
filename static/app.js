// Cała strona to zwykły JS bez bibliotek. Rozmawia z API z tego samego serwera.
// Dane ofert pochodzą z obcych stron, więc wstawiam je TYLKO przez textContent (nigdy innerHTML).

const $ = (id) => document.getElementById(id);
let odpytywanie = null;   // timer sprawdzania postępu
let szukanieId = null;
let kluczSerwera = false; // czy serwer ma swój klucz (lokalnie) - wtedy pole klucza jest opcjonalne

// --- klucz API = "konto" ---
// sessionStorage: znika po zamknięciu karty. localStorage: zostaje na urządzeniu (gdy ktoś zaznaczy "zapamiętaj").
// Przeglądarka może zablokować oba (tryb prywatny) - wtedy klucz żyje tylko w polu formularza.

function magazyn(rodzaj) {
  try { return rodzaj === "local" ? window.localStorage : window.sessionStorage; } catch { return null; }
}

function wczytajKlucz() {
  for (const rodzaj of ["local", "session"]) {
    try {
      const k = magazyn(rodzaj)?.getItem("klucz");
      if (k) { $("klucz").value = k; $("zapamietaj").checked = rodzaj === "local"; return; }
    } catch { /* brak dostępu do pamięci przeglądarki */ }
  }
}

function zapiszKlucz() {
  const k = $("klucz").value.trim();
  for (const rodzaj of ["local", "session"]) {
    try { magazyn(rodzaj)?.removeItem("klucz"); } catch {}
  }
  if (!k) return;
  try { magazyn($("zapamietaj").checked ? "local" : "session")?.setItem("klucz", k); } catch {}
}

function naglowkiKlucza() {
  const k = $("klucz").value.trim();
  return k ? { "X-Api-Key": k } : {};
}

// --- pomocnicze ---

function pokaz(tekst, rodzaj) {
  const k = $("komunikat");
  k.textContent = tekst;
  k.className = "komunikat " + (rodzaj || "");
}

// FastAPI zwraca błąd jako {detail: "tekst"} albo przy 422 jako listę błędów pól
function opisBledu(dane, status) {
  const d = dane && dane.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((b) => `${(b.loc || []).slice(-1)[0]}: ${b.msg}`).join("; ");
  return `Błąd serwera (${status})`;
}

async function api(adres, opcje = {}) {
  // klucz dokładam do każdego zapytania - po nim serwer wie, czyje oferty pokazać
  const r = await fetch(adres, { ...opcje, headers: { ...naglowkiKlucza(), ...(opcje.headers || {}) } });
  const dane = await r.json().catch(() => null);
  if (!r.ok) throw new Error(opisBledu(dane, r.status));
  return dane;
}

function el(tag, tekst, klasa) {
  const e = document.createElement(tag);
  if (tekst !== undefined && tekst !== null) e.textContent = tekst;
  if (klasa) e.className = klasa;
  return e;
}

function bezpiecznyLink(url) {
  try {
    const u = new URL(url);
    return (u.protocol === "https:" || u.protocol === "http:") ? u.href : null;
  } catch { return null; }
}

function lista(elementy) {
  if (!elementy || !elementy.length) return el("span", "-", "firma");
  const ul = el("ul");
  elementy.forEach((x) => ul.appendChild(el("li", x)));
  return ul;
}

// --- start strony ---

async function start() {
  try {
    const [kategorie, info] = await Promise.all([api("/kategorie"), api("/info")]);
    const s = $("kategoria");
    for (const [klucz, nazwa] of Object.entries(kategorie)) s.appendChild(new Option(nazwa, klucz));
    $("budzet").max = info.budzet_max_usd;
    kluczSerwera = info.klucz_serwera;
    if (info.klucz_serwera) {
      $("klucz-opis").textContent = "(opcjonalnie - serwer ma swój klucz)";
    } else {
      $("klucz").required = true;
    }
  } catch (e) {
    pokaz("Nie udało się połączyć z serwerem: " + e.message, "blad");
  }
  wczytajKlucz();
  // service worker zawsze - potrzebny do instalacji jako aplikacja (PWA), nie tylko do powiadomień
  if ("serviceWorker" in navigator) rejestracja().catch(() => {});
  await odswiezWszystko();
}

async function odswiezWszystko() {
  await wczytajSzukania();
  // link z powiadomienia: /?szukanie=12 - od razu pokazuję oferty z tego szukania
  const z = new URLSearchParams(location.search).get("szukanie");
  if (z && [...$("f-szukanie").options].some((o) => o.value === z)) $("f-szukanie").value = z;
  await wczytajOferty();
  await wczytajAutomat();
  await stanPowiadomien();
  await wczytajProfil();
}

// zmiana klucza = zmiana "konta": zapamiętuję go i pokazuję oferty tej osoby
$("klucz").addEventListener("change", () => { zapiszKlucz(); $("f-szukanie").value = ""; odswiezWszystko(); });
$("zapamietaj").addEventListener("change", zapiszKlucz);

// --- szukanie ---

function kryteriaZFormularza() {
  return {
    kategoria: $("kategoria").value,
    stanowisko: $("stanowisko").value.trim(),
    kwalifikacje: $("kwalifikacje").value.trim(),
    doswiadczenie: $("doswiadczenie").value,
    miasto: $("miasto").value.trim(),
    zdalnie: $("zdalnie").checked,
    budzet_usd: Number($("budzet").value),
  };
}

$("formularz").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  pokaz("");
  const kryteria = kryteriaZFormularza();
  const naglowki = { "Content-Type": "application/json" };
  zapiszKlucz();

  $("szukaj-btn").disabled = true;
  $("szukaj-btn").textContent = "Sprawdzam klucz…";
  try {
    const odp = await api("/szukaj", { method: "POST", headers: naglowki, body: JSON.stringify(kryteria) });
    szukanieId = odp.id;
    $("postep").style.display = "block";
    $("postep-tytul").textContent = `Szukam: ${kryteria.stanowisko}`;
    $("szukaj-btn").textContent = "Szukam…";
    sprawdzPostep();
    odpytywanie = setInterval(sprawdzPostep, 3000);
  } catch (e) {
    pokaz(e.message, "blad");
    $("szukaj-btn").disabled = false;
    $("szukaj-btn").textContent = "Szukaj ofert";
  }
});

async function sprawdzPostep() {
  let s;
  try { s = await api(`/szukaj/${szukanieId}`); }
  catch (e) { return; }   // chwilowy błąd sieci - spróbuję przy następnym tyknięciu

  $("p-runda").textContent = s.runda;
  $("p-oferty").textContent = s.liczba_ofert;
  $("p-koszt").textContent = s.koszt_usd.toFixed(3) + " $";
  $("p-budzet").textContent = s.budzet_usd ? `z ${s.budzet_usd} $ budżetu` : "koszt";
  $("p-strony").textContent = s.pobrania;
  const procent = s.budzet_usd ? Math.min(100, (s.koszt_usd / s.budzet_usd) * 100) : 0;
  $("pasek-koszt").style.width = procent + "%";

  if (s.status === "trwa") return;

  clearInterval(odpytywanie);
  $("szukaj-btn").disabled = false;
  $("szukaj-btn").textContent = "Szukaj ofert";
  const opisy = {
    gotowe: `Gotowe: ${s.liczba_ofert} nowych ofert za ${s.koszt_usd.toFixed(3)} $.`,
    budzet: `Skończył się budżet - zapisałem ${s.liczba_ofert} ofert. Zwiększ budżet, jeśli chcesz więcej.`,
    blad: `Błąd: ${s.blad || "nieznany"}. Zapisałem to, co udało się znaleźć (${s.liczba_ofert}).`,
    przerwane: "Szukanie zostało przerwane (serwer był wyłączony).",
  };
  $("postep-tytul").textContent = s.status === "gotowe" ? "Gotowe" : "Koniec szukania";
  $("p-opis").textContent = opisy[s.status] || s.status;
  pokaz(opisy[s.status] || s.status, s.status === "gotowe" ? "ok" : "blad");

  // pokazuję od razu wyniki tego szukania
  await wczytajSzukania();
  dodajSzukanieDoFiltra(szukanieId);
  $("f-szukanie").value = String(szukanieId);
  await wczytajOferty();
}

// --- tabela ofert ---

function filtry() {
  const p = new URLSearchParams();
  if ($("f-pasuje").value) p.set("pasuje", $("f-pasuje").value);
  if ($("f-cv").value) p.set("aplikowano", $("f-cv").value);
  if ($("f-szukanie").value) p.set("szukanie_id", $("f-szukanie").value);
  return p;
}

function dodajSzukanieDoFiltra(id, opis) {
  const s = $("f-szukanie");
  if (![...s.options].some((o) => o.value === String(id))) s.appendChild(new Option(opis || `nr ${id}`, String(id)));
}

async function wczytajSzukania() {
  // moje ostatnie szukania do filtra "Z szukania"
  const s = $("f-szukanie");
  const wybrane = s.value;
  s.replaceChildren(new Option("wszystkich", ""));
  if (!$("klucz").value.trim() && !kluczSerwera) return;
  try {
    for (const sz of await api("/szukania")) {
      const kiedy = new Date(sz.utworzono).toLocaleDateString("pl-PL");
      dodajSzukanieDoFiltra(sz.id, `${kiedy}: ${sz.stanowisko || "?"}${sz.miasto ? ", " + sz.miasto : ""}`);
    }
  } catch { /* brak klucza albo bazy - tabela pokaże komunikat */ }
  s.value = [...s.options].some((o) => o.value === wybrane) ? wybrane : "";
}

async function wczytajOferty() {
  const p = filtry();
  const tbody = $("oferty");
  if (!$("klucz").value.trim() && !kluczSerwera) {
    tbody.replaceChildren(wierszPusty("Wpisz swój klucz API, żeby zobaczyć swoje oferty."));
    return;
  }
  let oferty;
  try {
    oferty = await api("/oferty?" + p);
  } catch (e) {
    tbody.replaceChildren(wierszPusty("Nie udało się wczytać ofert: " + e.message));
    return;
  }
  if (!oferty.length) {
    tbody.replaceChildren(wierszPusty("Brak ofert. Uruchom szukanie albo zmień filtry."));
    return;
  }
  tbody.replaceChildren(...oferty.map(wiersz));
}

function wierszPusty(tekst) {
  const tr = el("tr");
  const td = el("td", tekst, "pusto");
  td.colSpan = 7;
  tr.appendChild(td);
  return tr;
}

const ETYKIETY = [null, "Płaca", "Dopasowanie", "Spełniam", "Brakuje mi", "Dodano", null];

function wiersz(o) {
  const tr = el("tr");

  const tdStan = el("td", null, "oferta");
  const link = bezpiecznyLink(o.link);
  const tytul = link ? el("a", o.stanowisko) : el("span", o.stanowisko);
  if (link) { tytul.href = link; tytul.target = "_blank"; tytul.rel = "noopener noreferrer"; }
  tdStan.append(tytul, el("div", [o.firma, o.miasto, o.poziom].filter(Boolean).join(" · "), "firma"));
  tr.appendChild(tdStan);

  tr.appendChild(el("td", o.wynagrodzenie || "-", "placa"));

  const tdPas = el("td");
  const pas = o.pasuje || "DO SPRAWDZENIA";
  tdPas.appendChild(el("span", pas.toLowerCase(), "znacznik " + pas.split(" ")[0]));
  tr.appendChild(tdPas);

  const tdSp = el("td", null, "wymagania"); tdSp.appendChild(lista(o.spelnione_wymagania)); tr.appendChild(tdSp);
  const tdBr = el("td", null, "wymagania"); tdBr.appendChild(lista(o.brakujace_wymagania)); tr.appendChild(tdBr);
  tr.appendChild(el("td", o.data_publikacji || "-", "waska"));

  const tdCv = el("td", null, "waska cv");
  const btn = el("button", null, "maly");
  btn.type = "button";
  ustawPrzyciskCv(btn, o.aplikowano);
  btn.addEventListener("click", () => przelaczCv(o.id, btn));
  tdCv.appendChild(btn);
  tr.appendChild(tdCv);
  // podpisy kolumn - widać je tylko na telefonie, gdzie wiersz jest kartą
  [...tr.children].forEach((td, i) => { if (ETYKIETY[i]) td.dataset.etykieta = ETYKIETY[i]; });
  return tr;
}

function ustawPrzyciskCv(btn, aplikowano) {
  btn.dataset.wyslane = aplikowano ? "1" : "";
  btn.textContent = aplikowano ? "✓ CV wysłane" : "Wysłałem CV";
  btn.className = "maly " + (aplikowano ? "wyslane" : "drugi");
  btn.title = aplikowano ? `Zaznaczone ${new Date(aplikowano).toLocaleString("pl-PL")} - kliknij, żeby cofnąć` : "";
}

async function przelaczCv(id, btn) {
  btn.disabled = true;
  try {
    const o = await api(`/oferty/${id}/aplikuj`, { method: btn.dataset.wyslane ? "DELETE" : "POST" });
    ustawPrzyciskCv(btn, o.aplikowano);
  } catch (e) {
    pokaz(e.message, "blad");
  } finally {
    btn.disabled = false;
  }
}

["f-pasuje", "f-cv", "f-szukanie"].forEach((id) => $(id).addEventListener("change", wczytajOferty));

// .ods pobieram przez fetch, bo zwykły link nie wyśle nagłówka z kluczem
$("pobierz-ods").addEventListener("click", async () => {
  const btn = $("pobierz-ods");
  btn.disabled = true;
  try {
    const r = await fetch("/oferty.ods?" + filtry(), { headers: naglowkiKlucza() });
    if (!r.ok) throw new Error(opisBledu(await r.json().catch(() => null), r.status));
    const adres = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = adres;
    a.download = `oferty_${new Date().toISOString().slice(0, 10)}.ods`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(adres), 1000);
  } catch (e) {
    pokaz("Nie udało się pobrać arkusza: " + e.message, "blad");
  } finally {
    btn.disabled = false;
  }
});
start();


// --- automatyczne szukanie ---

function maKlucz() {
  return Boolean($("klucz").value.trim()) || kluczSerwera;
}

async function wczytajAutomat() {
  $("a-wylacz").hidden = true;
  $("a-wlacz").textContent = "Włącz automat";
  if (!maKlucz()) { $("a-stan").textContent = "Wpisz klucz API, żeby włączyć automat."; return; }
  let a = null;
  try { a = await api("/automat"); } catch { /* np. brak bazy - pokażę jako wyłączony */ }
  if (!a) {
    $("a-stan").textContent = "Wyłączone. Automat szuka z kryteriami z formularza powyżej i daje znać powiadomieniem.";
    return;
  }
  $("a-dni").value = a.co_ile_dni;
  $("a-godzina").value = a.godzina;
  $("a-wylacz").hidden = false;
  $("a-wlacz").textContent = "Zapisz zmiany";
  const kiedy = (d) => new Date(d).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" });
  const tekst = a.wlaczony
    ? `Włączone: „${a.kryteria.stanowisko}”, co ${a.co_ile_dni} dni o ${a.godzina}:00. Następne: ${a.nastepne ? kiedy(a.nastepne) : "-"}.`
    : "Wstrzymane.";
  $("a-stan").textContent = tekst + (a.ostatni_wynik ? ` Ostatnio: ${a.ostatni_wynik}.` : "");
}

$("a-wlacz").addEventListener("click", async () => {
  if (!$("a-zgoda").checked) { pokaz("Zaznacz zgodę na zapisanie klucza - bez niego serwer nie poszuka sam.", "blad"); return; }
  if (!$("formularz").reportValidity()) return;   // te same kryteria co w formularzu
  zapiszKlucz();
  try {
    await api("/automat", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kryteria: kryteriaZFormularza(), co_ile_dni: Number($("a-dni").value),
                             godzina: Number($("a-godzina").value), zgoda_na_zapis_klucza: true }),
    });
    pokaz("Automat włączony. Włącz też powiadomienia, żeby wiedzieć o nowych ofertach.", "ok");
  } catch (e) { pokaz(e.message, "blad"); }
  await wczytajAutomat();
});

$("a-wylacz").addEventListener("click", async () => {
  try { await api("/automat", { method: "DELETE" }); pokaz("Automat wyłączony, Twój klucz usunięty z serwera.", "ok"); }
  catch (e) { pokaz(e.message, "blad"); }
  $("a-zgoda").checked = false;
  await wczytajAutomat();
});

// --- powiadomienia push (PWA) ---

const pushDostepny = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

function kluczNaBajty(b64) {
  // klucz VAPID przychodzi w base64url, a przeglądarka chce bajty
  const czyste = (b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(czyste), (z) => z.charCodeAt(0));
}

async function rejestracja() {
  return navigator.serviceWorker.register("/sw.js");
}

async function stanPowiadomien() {
  $("p-test").hidden = true;
  if (!pushDostepny) {
    $("p-wlacz").disabled = true;
    $("p-stan").textContent = "Ta przeglądarka nie obsługuje powiadomień. Na iPhonie dodaj najpierw stronę do ekranu głównego.";
    return;
  }
  if (Notification.permission === "denied") {
    $("p-stan").textContent = "Powiadomienia są zablokowane w ustawieniach przeglądarki dla tej strony.";
    return;
  }
  try {
    const sub = await (await rejestracja()).pushManager.getSubscription();
    if (sub && maKlucz()) {
      // przypisuję to urządzenie do obecnego klucza (np. po zmianie klucza)
      await api("/push/subskrypcja", { method: "POST", headers: { "Content-Type": "application/json" },
                                       body: JSON.stringify(sub.toJSON()) });
      $("p-stan").textContent = "Włączone na tym urządzeniu.";
      $("p-wlacz").textContent = "Wyłącz na tym urządzeniu";
      $("p-test").hidden = false;
      return;
    }
  } catch { /* np. brak klucza - poniżej domyślny stan */ }
  $("p-wlacz").textContent = "Włącz na tym urządzeniu";
  $("p-stan").textContent = "";
}

$("p-wlacz").addEventListener("click", async () => {
  if (!maKlucz()) { pokaz("Wpisz najpierw klucz API.", "blad"); return; }
  $("p-wlacz").disabled = true;
  try {
    const reg = await rejestracja();
    const obecna = await reg.pushManager.getSubscription();
    if (obecna) {   // wyłączanie
      await api("/push/subskrypcja", { method: "DELETE", headers: { "Content-Type": "application/json" },
                                       body: JSON.stringify(obecna.toJSON()) }).catch(() => {});
      await obecna.unsubscribe();
    } else {
      if (await Notification.requestPermission() !== "granted") throw new Error("Nie zezwolono na powiadomienia.");
      const { klucz_publiczny } = await api("/push/klucz");
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: kluczNaBajty(klucz_publiczny) });
      await api("/push/subskrypcja", { method: "POST", headers: { "Content-Type": "application/json" },
                                       body: JSON.stringify(sub.toJSON()) });
    }
  } catch (e) { pokaz(e.message, "blad"); }
  $("p-wlacz").disabled = false;
  await stanPowiadomien();
});

$("p-test").addEventListener("click", async () => {
  try { await api("/push/test", { method: "POST" }); pokaz("Wysłano próbne powiadomienie.", "ok"); }
  catch (e) { pokaz(e.message, "blad"); }
});


// --- profil z CV ---

function wypelnijZProfilu(p, nadpisz) {
  // nadpisz = świeżo wczytane CV; przy starcie strony nie ruszam tego, co ktoś już wpisał
  if (nadpisz || !$("kwalifikacje").value.trim()) $("kwalifikacje").value = p.kwalifikacje;
  if (nadpisz) {
    $("doswiadczenie").value = p.doswiadczenie;
    $("kategoria").value = p.kategoria;
  }
  if (!$("stanowisko").value.trim() && p.stanowiska.length) $("stanowisko").value = p.stanowiska.slice(0, 2).join(" / ");
  if (!$("miasto").value.trim() && p.miasto) $("miasto").value = p.miasto;
  $("cv-usun").hidden = false;
}

async function wczytajProfil() {
  $("cv-usun").hidden = true;
  if (!maKlucz()) return;
  try {
    const p = await api("/cv");
    if (p) {
      wypelnijZProfilu(p, false);
      $("cv-stan").textContent = `Profil z CV: ${p.podsumowanie}`;
    }
  } catch { /* brak bazy albo klucza - formularz zostaje pusty */ }
}

$("cv-btn").addEventListener("click", () => {
  if (!maKlucz()) { pokaz("Wpisz najpierw klucz API - CV czyta Claude na Twój koszt (ok. 1 grosz).", "blad"); return; }
  $("cv-plik").click();
});

$("cv-plik").addEventListener("change", async () => {
  const plik = $("cv-plik").files[0];
  $("cv-plik").value = "";   // ten sam plik drugi raz też ma wywołać "change"
  if (!plik) return;
  if (plik.size > 5 * 1024 * 1024) { pokaz("Plik jest za duży (maks. 5 MB).", "blad"); return; }
  zapiszKlucz();
  $("cv-btn").disabled = true;
  $("cv-stan").textContent = "Czytam CV…";
  try {
    const { profil, koszt_usd } = await api("/cv", { method: "POST", headers: { "Content-Type": "application/pdf" }, body: plik });
    wypelnijZProfilu(profil, true);
    $("cv-stan").textContent = `Z CV (${koszt_usd.toFixed(4)} $): ${profil.podsumowanie} Proponowane: ${profil.stanowiska.join(", ")}.`;
    pokaz("Uzupełniłem formularz z CV - sprawdź i popraw, zanim zaczniesz szukać.", "ok");
  } catch (e) {
    $("cv-stan").textContent = "";
    pokaz("CV: " + e.message, "blad");
  }
  $("cv-btn").disabled = false;
});

$("cv-usun").addEventListener("click", async () => {
  try { await api("/cv", { method: "DELETE" }); } catch (e) { pokaz(e.message, "blad"); return; }
  $("cv-usun").hidden = true;
  $("cv-stan").textContent = "Profil z CV usunięty z serwera.";
});
