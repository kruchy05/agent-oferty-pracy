# Moje narzędzie pobierz_strone i sprawdzanie, czy oferta jeszcze żyje.
# Czytanie strony nie ma opłaty za wyszukiwanie, płacę tylko za tokeny tekstu (ok. 10x taniej).
# Funkcje nie trzymają żadnego stanu - lista portali i limity przychodzą z zewnątrz (z Przebiegu).

import html
import re
from urllib.parse import urljoin, urlparse

import requests

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
    if jsonld:
        czesci.append("DANE STRUKTURALNE (JSON-LD):\n" + "\n".join(j.strip() for j in jsonld)[:3000])
    czesci.append("TEKST STRONY:\n" + tekst)
    wynik = "\n\n".join(czesci)[:max_znakow]
    if linki:
        wynik += "\n\nLINKI NA STRONIE:\n" + "\n".join(linki[:60])
    return wynik


def pobierz_strone(url, portale, max_znakow):
    """Zwraca (tekst, czy_blad, czy_aktywna). Błąd nie wywala agenta - model dostaje go jako is_error."""
    if not dozwolony_adres(url, portale):
        return "Odmowa: ten adres nie należy do dozwolonych portali.", True, False
    try:
        odp = requests.get(url, headers=NAGLOWKI_HTTP, timeout=15)
    except requests.RequestException as e:
        return f"Nie udało się pobrać strony ({type(e).__name__}).", True, False
    if odp.status_code in (404, 410):
        return "Strona nie istnieje - oferta usunięta. Pomiń ją.", True, False
    if odp.status_code >= 400:
        return f"Błąd HTTP {odp.status_code} - portal nie pozwala pobrać strony. Spróbuj innego źródła.", True, False

    tekst = html_na_tekst(odp.text, odp.url, portale, max_znakow)
    if wyglada_na_wygasla(tekst.lower()):
        return "[UWAGA: na stronie jest komunikat, że oferta wygasła lub jest archiwalna]\n\n" + tekst, False, False
    return tekst, False, True


def sprawdz_link(url, pobrane_aktywne):
    """Zwraca: "aktywna", "wygasła" albo "nie sprawdzono"."""
    if url in pobrane_aktywne:
        return "aktywna"   # agent już ją czytał i była żywa
    if not str(url).startswith("http"):
        return "wygasła"
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
