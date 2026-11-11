# Moje narzędzie pobierz_strone i sprawdzanie, czy oferta jeszcze żyje.
# Czytanie strony nie ma opłaty za wyszukiwanie, płacę tylko za tokeny tekstu (ok. 10x taniej).
# Funkcje nie trzymają żadnego stanu - lista portali i limity przychodzą z zewnątrz (z Przebiegu).

import datetime
import html
import json
import re
import sys
from urllib.parse import urljoin, urlparse

import requests

from .portale import czy_blokuje_pobieranie

NARZEDZIE_POBIERZ = {
    "name": "pobierz_strone",
    "description": ("Pobiera AKTUALNĄ treść strony z dozwolonego portalu z ofertami pracy: "
                    "tytuł, dane strukturalne oferty (JSON-LD), tekst i listę linków. "
                    "Używaj do czytania listy ofert z filtrami albo szczegółów pojedynczej oferty, "
                    "zamiast kolejnych wyszukiwań."),
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Pełny adres https://..."}},
        "required": ["url"],
    },
}

# udaję zwykłą przeglądarkę, bo część portali odrzuca zapytania ze skryptów
NAGLOWKI_HTTP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}

OZNAKI_WYGASLEJ = [
    "cannot display this page", "has been removed", "has expired", "no longer available",
    "no longer accepting", "job is closed", "position has been filled", "offer expired",
    "offer is archived", "offer has been archived", "this offer is no longer active", "archived offer",
    "oferta wygasła", "ogłoszenie wygasło", "oferta nieaktualna", "ogłoszenie nieaktualne",
    "oferta jest już nieaktualna", "ogłoszenie zostało zakończone", "rekrutacja zakończona",
    "oferta została usunięta", "ogłoszenie zostało usunięte", "nie znaleziono strony",
    "strona nie istnieje", "ta oferta nie jest już dostępna", "oferta archiwalna",
    "ogłoszenie archiwalne", "oferta zarchiwizowana", "ogłoszenie zarchiwizowane",
    "oferta została zarchiwizowana", "oferta nie jest już aktywna", "ogłoszenie nie jest już aktywne",
]


def dozwolony_adres(url, portale):
    # model może pobierać TYLKO strony z portali tego wyszukiwania. dzięki temu podstępny tekst
    # na stronie nie namówi go do wejścia na obcy adres (np. żeby coś tam "wysłać" w linku)
    try:
        czesci = urlparse(url)
    except ValueError:
        return False
    host = (czesci.hostname or "").lower()
    return czesci.scheme in ("http", "https") and any(host == p or host.endswith("." + p) for p in portale)


def wyglada_na_wygasla(tekst_malymi):
    return any(oznaka in tekst_malymi for oznaka in OZNAKI_WYGASLEJ)


def _tekst(wartosc, dlugosc=80):
    if isinstance(wartosc, (dict, list)):
        wartosc = json.dumps(wartosc, ensure_ascii=False)
    return str(wartosc)[:dlugosc] if wartosc not in (None, "", [], {}) else "-"


def _pole(obiekt, fragmenty, glebokosc=2):
    """Pierwsza niepusta wartość z klucza, którego nazwa zawiera któryś fragment (np. 'workplace' w 'displayWorkplace').
    Szukam też głębiej (np. offers[0].displayWorkplace) - portale różnie nazywają i zagnieżdżają pola."""
    if isinstance(obiekt, dict):
        for klucz, wartosc in obiekt.items():
            if not any(f in klucz.lower() for f in fragmenty):
                continue
            if isinstance(wartosc, list) and wartosc and all(isinstance(w, str) for w in wartosc):
                return ", ".join(wartosc)   # np. positionLevels: ["pracownik fizyczny"]
            if isinstance(wartosc, (str, int, float)) and str(wartosc).strip():
                return wartosc
        if glebokosc:
            for wartosc in obiekt.values():
                if isinstance(wartosc, (dict, list)):
                    znalezione = _pole(wartosc, fragmenty, glebokosc - 1)
                    if znalezione:
                        return znalezione
    elif isinstance(obiekt, list) and glebokosc:
        for wartosc in obiekt[:3]:
            znalezione = _pole(wartosc, fragmenty, glebokosc - 1)
            if znalezione:
                return znalezione
    return None


# po tym poznaję adres oferty i adres profilu firmy (żeby agent nie czytał profili zamiast ofert)
WYGLADA_NA_OFERTE = re.compile(r",oferta,|/oferta|/job-offer/|/jobs/view/|/remote-jobs/|/praca/[^/]+")
WYGLADA_NA_FIRME = re.compile(r"pracodawcy\.|/company/|/companies/|/firma/|/employer|/brands?/|\.(png|jpe?g|svg|webp)$")


def _link_oferty(obiekt, adres, portale, glebokosc=2):
    """Link do oferty: najpierw znane pola (slug / url), potem dowolny adres https:// z dozwolonego portalu
    (pracuj.pl trzyma go pod inną nazwą i głębiej - przez to wcześniej oferty nie miały linków)."""
    host = (urlparse(adres).hostname or "").lower()
    slug = obiekt.get("slug") or obiekt.get("url") or obiekt.get("offerUrl")
    if isinstance(slug, str) and slug:
        if slug.startswith("http"):
            link = slug
        elif host.endswith("justjoin.it"):
            link = f"https://justjoin.it/job-offer/{slug.strip('/')}"   # tak wyglądają linki do ofert
        else:
            link = urljoin(adres, slug)
        if dozwolony_adres(link, portale):
            return link

    # zbieram wszystkie adresy z obiektu i wybieram ten, który najbardziej wygląda na ofertę.
    # wcześniej brałem pierwszy z brzegu i przy pracuj.pl trafiał się profil firmy (pracodawcy.pracuj.pl/company/...)
    kandydaci = []

    def zbierz(o, g, klucz=""):
        if isinstance(o, str):
            if o.startswith("http") and dozwolony_adres(o, portale) and urlparse(o).path.count("/") >= 2:
                kandydaci.append((klucz.lower(), o))
        elif isinstance(o, dict):
            for k, w in o.items():
                if isinstance(w, str) or g:
                    zbierz(w, g if isinstance(w, str) else g - 1, k)
        elif isinstance(o, list) and g:
            for w in o[:3]:
                zbierz(w, g - 1, klucz)

    def ocena(kandydat):
        klucz, url = kandydat
        u = url.lower()
        if WYGLADA_NA_FIRME.search(u) or any(f in klucz for f in ("company", "employer", "logo", "image")):
            return -1
        punkty = 0
        if WYGLADA_NA_OFERTE.search(u):
            punkty += 2
        if "offer" in klucz or "oferta" in klucz:
            punkty += 1
        return punkty

    zbierz(obiekt, glebokosc)
    dobre = [k for k in kandydaci if ocena(k) >= 0]
    return max(dobre, key=ocena)[1] if dobre else ""


def oferty_z_next_data(surowy_html, adres, portale):
    """Strony w Next.js (np. pracuj.pl) trzymają listę ofert w skrypcie __NEXT_DATA__, a nie w HTML-u.
    Szukam w tym JSON-ie obiektów, które wyglądają jak oferty, i robię z nich krótką listę.
    Zwraca (tekst, ile_ofert_ma_link)."""
    m = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', surowy_html, re.S | re.I)
    if not m:
        return "", 0
    try:
        dane = json.loads(m.group(1))
    except ValueError:
        return "", 0

    wiersze, widziane = [], set()
    z_linkiem = 0

    def przejdz(obiekt, glebokosc=0):
        nonlocal z_linkiem
        if glebokosc > 15 or len(wiersze) >= 60:
            return
        if isinstance(obiekt, dict):
            tytul = obiekt.get("jobTitle") or obiekt.get("title")
            firma = obiekt.get("companyName") or obiekt.get("company")
            if isinstance(firma, dict):
                firma = firma.get("name")
            link = _link_oferty(obiekt, adres, portale) if isinstance(tytul, str) else ""
            if isinstance(tytul, str) and (firma or link):
                klucz = link or f"{tytul}|{firma}"
                if klucz not in widziane:
                    widziane.add(klucz)
                    z_linkiem += bool(link)
                    miasto = _pole(obiekt, ("city", "workplace", "location", "miasto"))
                    placa = obiekt.get("salary") or obiekt.get("employmentTypes") or _pole(obiekt, ("salary", "wynagrodzenie"))
                    data = _pole(obiekt, ("publicat", "posted", "publishedat", "createdat", "date"), glebokosc=1)
                    poziom = _pole(obiekt, ("experience", "seniority", "positionlevel", "level"), glebokosc=1)
                    wiersze.append(" | ".join([_tekst(tytul), _tekst(firma), _tekst(miasto), _tekst(poziom, 50),
                                               _tekst(placa, 120), _tekst(data, 25), link or "-"]))
            for wartosc in obiekt.values():
                przejdz(wartosc, glebokosc + 1)
        elif isinstance(obiekt, list):
            for wartosc in obiekt:
                przejdz(wartosc, glebokosc + 1)

    przejdz(dane)
    if not wiersze:
        return "", 0
    return ("OFERTY Z DANYCH STRONY (tytuł | firma | miasto | poziom | płaca | data | link):\n"
            + "\n".join(wiersze)), z_linkiem


MAX_TEKSTU_PRZY_DANYCH = 2500   # gdy strona ma dane strukturalne, sam tekst to głównie menu i filtry


def _obiekty_z_typem(dane):
    # JSON-LD bywa zagnieżdżony (np. CollectionPage -> mainEntity -> ItemList) albo w "@graph" - przechodzę wszystko
    if isinstance(dane, dict):
        if "@type" in dane:
            yield dane
        for wartosc in dane.values():
            yield from _obiekty_z_typem(wartosc)
    elif isinstance(dane, list):
        for wartosc in dane:
            yield from _obiekty_z_typem(wartosc)


def _typy(obiekt):
    typ = obiekt.get("@type")
    return set(typ) if isinstance(typ, list) else {typ}


def _opis_oferty_jobposting(o):
    """Najważniejsze pola z JobPosting (schema.org) w jednej linii zamiast całego JSON-a."""
    firma = o.get("hiringOrganization")
    firma = firma.get("name") if isinstance(firma, dict) else firma
    miejsca = o.get("jobLocation") or []
    miejsca = miejsca if isinstance(miejsca, list) else [miejsca]
    miasta = []
    for m in miejsca:
        adres = m.get("address") if isinstance(m, dict) else None
        if isinstance(adres, dict) and adres.get("addressLocality"):
            miasta.append(adres["addressLocality"])
    if o.get("jobLocationType") == "TELECOMMUTE":
        miasta.append("zdalnie")
    placa = o.get("baseSalary")
    if isinstance(placa, dict):
        wartosc = placa.get("value") if isinstance(placa.get("value"), dict) else {"value": placa.get("value")}
        od, do = wartosc.get("minValue"), wartosc.get("maxValue") or wartosc.get("value")
        # justjoin.it potrafi podać samo maxValue (np. 130 PLN/h) - wtedy bez myślnika
        kwota = f"{od}-{do}" if od and do and od != do else str(od or do or "")
        jednostka = f" / {wartosc['unitText']}" if wartosc.get("unitText") else ""
        placa = f"{kwota} {placa.get('currency', '')}{jednostka}".strip() if kwota else None
    umowa = o.get("employmentType")
    if isinstance(umowa, list):
        umowa = ", ".join(str(u) for u in umowa)
    opis = re.sub(r"<[^>]+>", " ", html.unescape(str(o.get("description") or "")))
    opis = re.sub(r"\s+", " ", opis).strip()[:700]

    wazna_do = str(o.get("validThrough") or "")[:10]
    uwaga = ""
    try:
        if wazna_do and datetime.date.fromisoformat(wazna_do) < datetime.date.today():
            uwaga = " [oferta wygasła - termin ważności minął]"   # tekst łapany przez OZNAKI_WYGASLEJ
    except ValueError:
        pass
    return (f"OFERTA: {_tekst(o.get('title'), 150)}{uwaga}\n"
            f"firma: {_tekst(firma, 100)} | miasto: {_tekst(', '.join(miasta), 100)} | płaca: {_tekst(placa, 100)}\n"
            f"dodana: {_tekst(o.get('datePosted'), 10)} | ważna do: {wazna_do or '-'} | "
            f"umowa: {_tekst(umowa, 60)} | doświadczenie: {_tekst(o.get('experienceRequirements'), 120)}\n"
            f"opis: {opis or '-'}")


def opis_jsonld(bloki, portale):
    """Zamienia JSON-LD na krótki tekst: lista ofert (ItemList) i szczegóły ofert (JobPosting).
    Przykład z życia: justjoin.it trzyma 50 ofert listy w ItemList, a wcześniej ucinałem JSON na 3000 znakach."""
    lista, oferty, reszta = [], [], []
    for surowy in bloki:
        try:
            dane = json.loads(surowy)
        except ValueError:
            reszta.append(surowy.strip()[:1000])
            continue
        for obiekt in _obiekty_z_typem(dane):
            typy = _typy(obiekt)
            if "ItemList" in typy:
                for el in obiekt.get("itemListElement") or []:
                    if not isinstance(el, dict):
                        continue
                    przedmiot = el.get("item") if isinstance(el.get("item"), dict) else {}
                    url = el.get("url") or przedmiot.get("url") or (el.get("item") if isinstance(el.get("item"), str) else "")
                    nazwa = el.get("name") or przedmiot.get("name") or przedmiot.get("title") or "-"
                    if url and dozwolony_adres(url, portale) and not any(url in w for w in lista):
                        lista.append(f"- {nazwa} | {url}")
            elif "JobPosting" in typy:
                oferty.append(_opis_oferty_jobposting(obiekt))
    czesci = []
    if lista:
        czesci.append(f"LISTA OFERT ZE STRONY ({len(lista)}, nazwa | link - firma jest zwykle w linku):\n" + "\n".join(lista))
    czesci += oferty
    if not czesci and reszta:
        czesci.append("DANE STRUKTURALNE (JSON-LD):\n" + "\n".join(reszta)[:3000])
    return "\n\n".join(czesci), bool(lista or oferty)


def html_na_tekst(surowy_html, adres, portale, max_znakow):
    # wyciągam to, co przydatne: tytuł, JSON-LD (portale często trzymają tam listę ofert, datę i płacę), tekst i linki
    tytul = re.search(r"<title[^>]*>(.*?)</title>", surowy_html, re.S | re.I)
    jsonld = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', surowy_html, re.S | re.I)

    linki = []
    for href in re.findall(r'<a\s[^>]*href=["\']([^"\'#]+)["\']', surowy_html, re.I):
        pelny = urljoin(adres, html.unescape(href))
        if dozwolony_adres(pelny, portale) and urlparse(pelny).path.count("/") >= 2 and pelny not in linki:
            linki.append(pelny)

    bez_skryptow = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", surowy_html, flags=re.S | re.I)
    bez_skryptow = re.sub(r"<br\s*/?>|</(p|div|li|h\d|tr|section|article)>", "\n", bez_skryptow, flags=re.I)
    tekst = html.unescape(re.sub(r"<[^>]+>", " ", bez_skryptow))
    tekst = re.sub(r"[ \t]+", " ", tekst)
    tekst = re.sub(r"\n\s*\n+", "\n", tekst).strip()

    czesci = [f"TYTUŁ: {html.unescape(tytul.group(1)).strip() if tytul else '-'}"]
    dane_jsonld, sa_oferty = opis_jsonld(jsonld, portale)
    next_data, next_z_linkiem = oferty_z_next_data(surowy_html, adres, portale)
    if dane_jsonld:
        czesci.append(dane_jsonld)
    if next_data:
        czesci.append(next_data[:max_znakow // 2])   # max połowa limitu, żeby został też tekst strony
    # tekst i linki obcinam TYLKO, gdy dane strukturalne naprawdę dają oferty z linkami
    # (pracuj.pl: 43 oferty bez linków + ucięty tekst = model nie miał ani jednego adresu -> 0 ofert)
    sa_dane = sa_oferty or next_z_linkiem >= 3
    # gdy oferty są już w danych strukturalnych, tekst strony to głównie menu - biorę tylko początek
    czesci.append("TEKST STRONY:\n" + (tekst[:MAX_TEKSTU_PRZY_DANYCH] if sa_dane else tekst))
    wynik = "\n\n".join(czesci)[:max_znakow]
    if linki and not sa_dane:
        # przy danych strukturalnych linki do ofert już są w liście - nie dublują tokenów
        wynik += "\n\nLINKI NA STRONIE:\n" + "\n".join(linki[:60])
    return wynik


def pobierz_strone(url, portale, max_znakow):
    """Zwraca (tekst, czy_blad, czy_aktywna, czy_pobrano). Błąd nie wywala agenta - model dostaje go jako is_error.
    czy_pobrano = False, gdy w ogóle nie łączyłem się ze stroną (adres spoza listy, portal blokujący)."""
    if not dozwolony_adres(url, portale):
        return "Odmowa: ten adres nie należy do dozwolonych portali.", True, False, False
    if czy_blokuje_pobieranie(urlparse(url).hostname):
        # nie tracę czasu na zapytanie, które i tak skończy się błędem
        return ("Ten portal nie pozwala pobierać stron. Korzystaj z informacji z wyników wyszukiwania "
                "albo pobierz stronę z innego portalu."), True, False, False
    try:
        odp = requests.get(url, headers=NAGLOWKI_HTTP, timeout=15)
    except requests.RequestException as e:
        return f"Nie udało się pobrać strony ({type(e).__name__}).", True, False, True
    if odp.status_code in (404, 410):
        return "Strona nie istnieje - oferta usunięta. Pomiń ją.", True, False, True
    if odp.status_code >= 400:
        return f"Błąd HTTP {odp.status_code} - portal nie pozwala pobrać strony. Spróbuj innego źródła.", True, False, True

    tekst = html_na_tekst(odp.text, odp.url, portale, max_znakow)
    if wyglada_na_wygasla(tekst.lower()):
        return "[UWAGA: na stronie jest komunikat, że oferta wygasła lub jest archiwalna]\n\n" + tekst, False, False, True
    return tekst, False, True, True


def sprawdz_link(url, pobrane_aktywne):
    """Zwraca: "aktywna", "wygasła" albo "nie sprawdzono"."""
    if url in pobrane_aktywne:
        return "aktywna"   # agent już ją czytał i była żywa
    if not str(url).startswith("http"):
        return "wygasła"
    if czy_blokuje_pobieranie(urlparse(url).hostname):
        return "nie sprawdzono"   # portal i tak by mnie zablokował
    try:
        odp = requests.get(url, headers=NAGLOWKI_HTTP, timeout=15)
    except requests.RequestException:
        return "nie sprawdzono"
    if odp.status_code in (404, 410):
        return "wygasła"
    if odp.status_code >= 400:
        return "nie sprawdzono"   # np. 403 = portal blokuje skrypty

    # przekierowanie z oferty na stronę główną albo listę ofert = oferta zniknęła
    przed, po = urlparse(url).path.strip("/"), urlparse(odp.url).path.strip("/")
    if przed and po.count("/") < przed.count("/") and len(po) < len(przed) / 2:
        return "wygasła"
    return "wygasła" if wyglada_na_wygasla(odp.text[:800_000].lower()) else "aktywna"


# ---------- podgląd: co agent "widzi" na stronie (darmowe, bez API) ----------
#   python -m app.narzedzia https://justjoin.it/job-offers/wroclaw/python

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Użycie: python -m app.narzedzia <adres strony>")
        sys.exit(1)
    adres = sys.argv[1]
    host = (urlparse(adres).hostname or "").lower()
    tekst, blad, aktywna, _ = pobierz_strone(adres, [host], max_znakow=12_000)
    print(f"Błąd: {blad} | aktywna oferta/strona: {aktywna} | {len(tekst)} znaków (ok. {len(tekst) // 4} tokenów)")
    print("-" * 80)
    print(tekst[:4000])
