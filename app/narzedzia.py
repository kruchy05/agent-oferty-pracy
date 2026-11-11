# Moje narzędzie pobierz_strone i sprawdzanie, czy oferta jeszcze żyje.
# Czytanie strony nie ma opłaty za wyszukiwanie, płacę tylko za tokeny tekstu (ok. 10x taniej).
# Funkcje nie trzymają żadnego stanu - lista portali i limity przychodzą z zewnątrz (z Przebiegu).

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


def oferty_z_next_data(surowy_html, adres, portale):
    """Strony w Next.js (np. justjoin.it) trzymają listę ofert w skrypcie __NEXT_DATA__, a nie w HTML-u.
    Szukam w tym JSON-ie obiektów, które wyglądają jak oferty, i robię z nich krótką listę."""
    m = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', surowy_html, re.S | re.I)
    if not m:
        return ""
    try:
        dane = json.loads(m.group(1))
    except ValueError:
        return ""

    host = (urlparse(adres).hostname or "").lower()
    wiersze, widziane = [], set()

    def przejdz(obiekt, glebokosc=0):
        if glebokosc > 15 or len(wiersze) >= 60:
            return
        if isinstance(obiekt, dict):
            tytul = obiekt.get("title") or obiekt.get("jobTitle")
            firma = obiekt.get("companyName") or obiekt.get("company")
            if isinstance(firma, dict):
                firma = firma.get("name")
            slug = obiekt.get("slug") or obiekt.get("url") or obiekt.get("offerUrl")
            if isinstance(tytul, str) and (firma or slug):
                link = ""
                if isinstance(slug, str):
                    if slug.startswith("http"):
                        link = slug
                    elif host.endswith("justjoin.it"):
                        link = f"https://justjoin.it/job-offer/{slug.strip('/')}"   # tak wyglądają linki do ofert
                    else:
                        link = urljoin(adres, slug)
                    if not dozwolony_adres(link, portale):
                        link = ""
                klucz = link or f"{tytul}|{firma}"
                if klucz not in widziane:
                    widziane.add(klucz)
                    miasto = obiekt.get("city") or obiekt.get("workplaceType") or obiekt.get("location")
                    placa = obiekt.get("salary") or obiekt.get("employmentTypes")
                    data = obiekt.get("publishedAt") or obiekt.get("createdAt") or obiekt.get("datePosted")
                    poziom = obiekt.get("experienceLevel") or obiekt.get("seniority")
                    wiersze.append(" | ".join([_tekst(tytul), _tekst(firma), _tekst(miasto), _tekst(poziom, 30),
                                               _tekst(placa, 120), _tekst(data, 25), link or "-"]))
            for wartosc in obiekt.values():
                przejdz(wartosc, glebokosc + 1)
        elif isinstance(obiekt, list):
            for wartosc in obiekt:
                przejdz(wartosc, glebokosc + 1)

    przejdz(dane)
    if not wiersze:
        return ""
    return ("OFERTY Z DANYCH STRONY (tytuł | firma | miasto | poziom | płaca | data | link):\n"
            + "\n".join(wiersze))


def html_na_tekst(surowy_html, adres, portale, max_znakow):
    # wyciągam to, co przydatne: tytuł, JSON-LD (portale często trzymają tam datę i płacę), tekst i linki
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
    next_data = oferty_z_next_data(surowy_html, adres, portale)
    if next_data:
        czesci.append(next_data[:max_znakow // 2])   # max połowa limitu, żeby został też tekst strony
    if jsonld:
        czesci.append("DANE STRUKTURALNE (JSON-LD):\n" + "\n".join(j.strip() for j in jsonld)[:3000])
    czesci.append("TEKST STRONY:\n" + tekst)
    wynik = "\n\n".join(czesci)[:max_znakow]
    if linki:
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
