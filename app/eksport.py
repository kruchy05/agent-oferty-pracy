# Zapis wyników do .json i .ods (LibreOffice Calc).
# Plik .ods to tak naprawdę zip z kilkoma plikami XML, więc składam go ręcznie bez bibliotek.

import json
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

def oferty_do_slownikow(pary):
    # model_dump() zamienia obiekt Pydantic na zwykły słownik - gotowy do JSON-a i do tabeli
    return [{**o.model_dump(), "status": status} for o, status in pary]


def zapisz_json(wiersze, sciezka):
    with open(sciezka, "w", encoding="utf-8") as f:
        json.dump(wiersze, f, ensure_ascii=False, indent=2)


KOLUMNY = ["link", "stanowisko", "firma", "miasto", "wynagrodzenie", "poziom", "wymagania",
           "spelnione_wymagania", "brakujace_wymagania", "data_dodania", "pasuje", "status"]
NAGLOWKI = ["Link", "Stanowisko", "Firma", "Miasto", "Wynagrodzenie", "Poziom", "Wymagania",
            "Spełniam", "Brakuje mi", "Data dodania", "Pasuje", "Status"]
SZEROKOSCI_CM = [3.2, 7.0, 5.0, 3.8, 5.0, 2.6, 9.0, 7.0, 7.0, 3.2, 3.8, 3.4]

NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
      'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
      'xmlns:xlink="http://www.w3.org/1999/xlink" '
      'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" '
      'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
      'office:version="1.2"')

RAMKA = 'fo:border="0.5pt solid #d0d4da"'
# ce1 nagłówek, ce2 tekst, ce3 link, ce4 pogrubiony, ce5 liczba
STYLE_ODS = (
    '<style:style style:name="wiersz" style:family="table-row">'
    '<style:table-row-properties style:use-optimal-row-height="true"/></style:style>'
    f'<style:style style:name="ce1" style:family="table-cell"><style:table-cell-properties '
    f'fo:background-color="#2b3a55" {RAMKA} style:vertical-align="middle"/>'
    '<style:text-properties fo:color="#ffffff" fo:font-weight="bold"/></style:style>'
    f'<style:style style:name="ce2" style:family="table-cell"><style:table-cell-properties '
    f'{RAMKA} fo:wrap-option="wrap" style:vertical-align="top"/></style:style>'
    f'<style:style style:name="ce3" style:family="table-cell"><style:table-cell-properties '
    f'{RAMKA} style:vertical-align="top"/></style:style>'
    f'<style:style style:name="ce4" style:family="table-cell"><style:table-cell-properties {RAMKA}/>'
    '<style:text-properties fo:font-weight="bold"/></style:style>'
    f'<style:style style:name="ce5" style:family="table-cell"><style:table-cell-properties {RAMKA}/>'
    '<style:paragraph-properties fo:text-align="end"/></style:style>'
)


def _txt(wartosc):
    if isinstance(wartosc, list):
        wartosc = "; ".join(str(w) for w in wartosc)
    s = str(wartosc if wartosc not in (None, "", []) else "brak danych")
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s))


def _komorka(wartosc, styl, link=None):
    if isinstance(wartosc, int):
        return (f'<table:table-cell table:style-name="{styl}" office:value-type="float" '
                f'office:value="{wartosc}"><text:p>{wartosc}</text:p></table:table-cell>')
    tekst = _txt(wartosc)
    if link:
        tekst = f'<text:a xlink:type="simple" xlink:href={quoteattr(link)}>{tekst}</text:a>'
    return (f'<table:table-cell table:style-name="{styl}" office:value-type="string">'
            f'<text:p>{tekst}</text:p></table:table-cell>')


def _wiersz(komorki):
    return '<table:table-row table:style-name="wiersz">' + "".join(komorki) + "</table:table-row>"


def _tabela(nazwa, szerokosci, wiersze, prefiks):
    kolumny = "".join(f'<table:table-column table:style-name="{prefiks}{i}"/>'
                      for i in range(len(szerokosci)))
    return f'<table:table table:name="{nazwa}">{kolumny}{"".join(wiersze)}</table:table>'


def _style_kolumn(prefiks, szerokosci):
    return "".join(
        f'<style:style style:name="{prefiks}{i}" style:family="table-column">'
        f'<style:table-column-properties style:column-width="{w}cm"/></style:style>'
        for i, w in enumerate(szerokosci))


def zapisz_ods(wiersze_ofert, sciezka, statystyki):
    """statystyki = lista (nazwa, wartość) do arkusza "Koszty" - np. licznik.podsumowanie(...)"""
    wiersze = [_wiersz(_komorka(n, "ce1") for n in NAGLOWKI)]
    for w in wiersze_ofert:
        link = str(w.get("link", "")).strip()
        komorki = [_komorka("Otwórz ofertę", "ce3", link if link.startswith("http") else None)]
        komorki += [_komorka(w.get(k), "ce2") for k in KOLUMNY[1:]]
        wiersze.append(_wiersz(komorki))
    tab_oferty = _tabela("Oferty", SZEROKOSCI_CM, wiersze, "coA")

    wiersze2 = [_wiersz([_komorka("Statystyka", "ce1"), _komorka("Wartość", "ce1")])]
    for nazwa, wartosc in statystyki:
        wiersze2.append(_wiersz([_komorka(nazwa, "ce4"),
                                 _komorka(wartosc, "ce5" if isinstance(wartosc, int) else "ce2")]))
    tab_koszty = _tabela("Koszty", [7.5, 4.5], wiersze2, "coB")

    ostatni = len(wiersze_ofert) + 1
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {NS}>'
        '<office:automatic-styles>'
        + _style_kolumn("coA", SZEROKOSCI_CM) + _style_kolumn("coB", [7.5, 4.5]) + STYLE_ODS +
        '</office:automatic-styles><office:body><office:spreadsheet>'
        + tab_oferty + tab_koszty +
        '<table:database-ranges><table:database-range table:name="__Anonymous_Sheet_DB__0" '
        f'table:target-range-address="Oferty.A1:Oferty.{"ABCDEFGHIJKL"[len(KOLUMNY) - 1]}{ostatni}" '
        'table:display-filter-buttons="true"/></table:database-ranges>'
        '</office:spreadsheet></office:body></office:document-content>')

    # zamrożony pierwszy wiersz, żeby nagłówki były widoczne przy przewijaniu
    settings = (
        f'<?xml version="1.0" encoding="UTF-8"?><office:document-settings {NS}><office:settings>'
        '<config:config-item-set config:name="ooo:view-settings">'
        '<config:config-item-map-indexed config:name="Views"><config:config-item-map-entry>'
        '<config:config-item config:name="ViewId" config:type="string">view1</config:config-item>'
        '<config:config-item-map-named config:name="Tables">'
        '<config:config-item-map-entry config:name="Oferty">'
        '<config:config-item config:name="HorizontalSplitMode" config:type="short">0</config:config-item>'
        '<config:config-item config:name="VerticalSplitMode" config:type="short">2</config:config-item>'
        '<config:config-item config:name="VerticalSplitPosition" config:type="int">1</config:config-item>'
        '<config:config-item config:name="ActiveSplitRange" config:type="short">2</config:config-item>'
        '<config:config-item config:name="PositionBottom" config:type="int">1</config:config-item>'
        '</config:config-item-map-entry></config:config-item-map-named>'
        '<config:config-item config:name="ActiveTable" config:type="string">Oferty</config:config-item>'
        '</config:config-item-map-entry></config:config-item-map-indexed>'
        '</config:config-item-set></office:settings></office:document-settings>')

    manifest = (
        f'<?xml version="1.0" encoding="UTF-8"?><manifest:manifest {NS}>'
        '<manifest:file-entry manifest:full-path="/" manifest:version="1.2" '
        'manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="settings.xml" manifest:media-type="text/xml"/>'
        '</manifest:manifest>')

    with zipfile.ZipFile(sciezka, "w") as z:
        # "mimetype" musi być pierwszy i nieskompresowany, inaczej LibreOffice nie rozpozna pliku
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("settings.xml", settings, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("META-INF/manifest.xml", manifest, compress_type=zipfile.ZIP_DEFLATED)
