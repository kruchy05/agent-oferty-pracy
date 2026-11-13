# Czytanie stron: dane strukturalne (JSON-LD, __NEXT_DATA__), linki, wygasłe oferty, blokady.
# Kształty danych wzięte z prawdziwych stron justjoin.it i pracuj.pl.

import datetime
import json

from app.narzedzia import (_link_oferty, dozwolony_adres, html_na_tekst, oferty_z_next_data, pobierz_strone,
                           sprawdz_link, wyglada_na_wygasla)
from app.portale import czy_blokuje_pobieranie, grupy_portali

MENU = "".join(f"<div>Pozycja menu {i}</div>" for i in range(400))   # długi śmieciowy tekst strony


def strona_jsonld(dane, tytul="Oferty"):
    return f'<html><head><title>{tytul}</title><script type="application/ld+json">{json.dumps(dane)}</script></head><body>{MENU}</body></html>'


def strona_next(dane):
    return (f'<html><head><title>Praca</title><script id="__NEXT_DATA__" type="application/json">{json.dumps(dane)}</script>'
            f'</head><body>{MENU}<a href="https://www.pracuj.pl/praca/magazynier-legnica,oferta,1001">x</a></body></html>')


# ---------- JSON-LD (justjoin.it) ----------

def test_lista_ofert_justjoin_cala_i_bez_obcych_linkow():
    elementy = [{"@type": "ListItem", "position": i + 1, "url": f"https://justjoin.it/job-offer/firma-{i}-python", "name": f"Oferta {i}"}
                for i in range(50)]
    elementy.append({"@type": "ListItem", "url": "https://evil.example.com/x", "name": "Podstęp"})
    dane = {"@context": "https://schema.org", "@type": "CollectionPage",
            "mainEntity": {"@type": "ItemList", "itemListElement": elementy}}
    tekst = html_na_tekst(strona_jsonld(dane), "https://justjoin.it/job-offers/wroclaw/python", ["justjoin.it"], 12000)

    assert "LISTA OFERT ZE STRONY (50" in tekst
    assert tekst.count("https://justjoin.it/job-offer/") == 50        # wcześniej obcinało do ok. 14
    assert "evil.example.com" not in tekst and '"@context"' not in tekst
    assert len(tekst.split("TEKST STRONY:\n")[1]) <= 2500           # menu przycięte, skoro są dane


def jobposting(wazna_do):
    return {"@type": "JobPosting", "title": "Junior Python Developer", "datePosted": "2026-11-02", "validThrough": wazna_do,
            "employmentType": ["CONTRACTOR"], "hiringOrganization": {"@type": "Organization", "name": "Link Group"},
            "jobLocation": [{"@type": "Place", "address": {"addressLocality": "Wrocław"}}],
            "baseSalary": {"currency": "PLN", "value": {"minValue": 7000, "maxValue": 9000, "unitText": "MONTH"}},
            "description": "<p>Szukamy <b>juniora</b>. Wymagania: Python, Git, SQL.</p>"}


def test_jobposting_firma_miasto_placa_bez_htmla():
    za_miesiac = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    tekst = html_na_tekst(strona_jsonld(jobposting(za_miesiac)), "https://justjoin.it/job-offer/x", ["justjoin.it"], 12000)
    assert "firma: Link Group | miasto: Wrocław | płaca: 7000-9000 PLN / MONTH" in tekst
    assert "Python, Git, SQL" in tekst and "<b>" not in tekst
    assert "CONTRACTOR" in tekst and '["CONTRACTOR"]' not in tekst
    assert not wyglada_na_wygasla(tekst.lower())


def test_jobposting_po_terminie_to_wygasla():
    wczoraj = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    tekst = html_na_tekst(strona_jsonld(jobposting(wczoraj)), "https://justjoin.it/job-offer/x", ["justjoin.it"], 12000)
    assert wyglada_na_wygasla(tekst.lower())


def test_placa_tylko_z_gorna_granica_bez_myslnika():
    oferta = jobposting("2099-01-01")
    oferta["baseSalary"] = {"currency": "PLN", "value": {"maxValue": 130, "unitText": "HOUR"}}
    tekst = html_na_tekst(strona_jsonld(oferta), "https://justjoin.it/job-offer/x", ["justjoin.it"], 12000)
    assert "płaca: 130 PLN / HOUR" in tekst and "-130" not in tekst


# ---------- __NEXT_DATA__ (pracuj.pl, justjoin.it) ----------

def oferty_pracuj(z_linkiem=True):
    oferty = []
    for i, (tytul, firma) in enumerate([("Magazynier (m/k/x*)", "Raben Logistics"), ("Pracownik magazynu", "Manuli"),
                                        ("Magazynier (K/M/X)", "3W S.A."), ("Operator produkcji", "Manpower")]):
        o = {"jobTitle": tytul, "companyName": firma, "lastPublicated": f"2026-11-0{i + 1}",
             "positionLevels": ["pracownik fizyczny"],
             # profil firmy jest WYŻEJ niż link do oferty - wcześniej agent brał go zamiast oferty
             "companyProfileAbsoluteUri": f"https://pracodawcy.pracuj.pl/company/{2000 + i}"}
        if z_linkiem:
            o["offers"] = [{"offerAbsoluteUri": f"https://www.pracuj.pl/praca/x-{i},oferta,{1000 + i}", "displayWorkplace": "Legnica"}]
        oferty.append(o)
    return {"props": {"pageProps": {"data": {"jobOffers": {"groupedOffers": oferty}}}}}


def test_pracuj_link_miasto_data_poziom_z_zagniezdzonych_pol():
    tekst = html_na_tekst(strona_next(oferty_pracuj()), "https://www.pracuj.pl/praca/magazynier;kw/legnica;wp", ["pracuj.pl"], 12000)
    assert tekst.count(",oferta,") == 4
    assert "pracodawcy.pracuj.pl" not in tekst            # profil firmy to nie oferta
    assert "| Legnica |" in tekst and "2026-11-01" in tekst and "pracownik fizyczny" in tekst
    assert "LINKI NA STRONIE" not in tekst                 # są linki w danych -> tekst strony przycięty


def test_pracuj_bez_linkow_nic_nie_ucina():
    # tak wyglądał błąd: 43 oferty bez linków, a tekst strony ucięty -> model nie miał żadnego adresu
    tekst = html_na_tekst(strona_next(oferty_pracuj(z_linkiem=False)), "https://www.pracuj.pl/praca/magazynier;kw/legnica;wp",
                          ["pracuj.pl"], 12000)
    assert "LINKI NA STRONIE" in tekst and "magazynier-legnica,oferta,1001" in tekst


def test_link_oferty_wybiera_oferte_a_nie_profil_firmy():
    oferta = {"companyProfileAbsoluteUri": "https://pracodawcy.pracuj.pl/company/1",
              "companyLogoUri": "https://www.pracuj.pl/logo/firma/1.png",
              "offers": [{"offerAbsoluteUri": "https://www.pracuj.pl/praca/kierownik,oferta,5"}]}
    adres = "https://www.pracuj.pl/praca/x;kw"
    assert _link_oferty(oferta, adres, ["pracuj.pl"]).endswith(",oferta,5")
    assert _link_oferty({"companyProfileAbsoluteUri": "https://pracodawcy.pracuj.pl/company/1"}, adres, ["pracuj.pl"]) == ""


def test_justjoin_slug_na_link_i_bez_duplikatow():
    dane = {"props": {"pageProps": {"offers": [
        {"slug": "firma-x-junior-python", "title": "Junior Python Developer", "companyName": "Firma X", "city": "Wrocław",
         "publishedAt": "2026-11-05T10:00:00Z"},
        {"slug": "https://evil.example.com/kradziez", "title": "Podstęp", "companyName": "Zła firma"},
        {"slug": "firma-x-junior-python", "title": "Junior Python Developer", "companyName": "Firma X"}]}}}
    tekst, z_linkiem = oferty_z_next_data(strona_next(dane), "https://justjoin.it/job-offers/wroclaw/python", ["justjoin.it"])
    assert "https://justjoin.it/job-offer/firma-x-junior-python" in tekst
    assert "evil.example.com" not in tekst and tekst.count("Junior Python Developer") == 1
    assert z_linkiem == 1


def test_zwykla_strona_bez_danych_zostaje_jak_byla():
    tekst = html_na_tekst("<html><title>X</title><body><p>Oferta pracy magazynier</p><a href='/praca/a/b'>a</a></body></html>",
                          "https://pracuj.pl/x", ["pracuj.pl"], 12000)
    assert "LINKI NA STRONIE" in tekst and "magazynier" in tekst
    assert oferty_z_next_data("<html>zwykła strona</html>", "https://pracuj.pl/x", ["pracuj.pl"]) == ("", 0)


# ---------- adresy, blokady, pobieranie ----------

def test_dozwolone_tylko_portale_z_listy():
    assert dozwolony_adres("https://www.pracuj.pl/praca/x", ["pracuj.pl"])
    assert not dozwolony_adres("https://pracuj.pl.evil.com/x", ["pracuj.pl"])
    assert not dozwolony_adres("javascript:alert(1)", ["pracuj.pl"])


def test_portale_blokujace_nie_zuzywaja_pobran():
    assert czy_blokuje_pobieranie("pl.indeed.com") and czy_blokuje_pobieranie("www.olx.pl")
    tekst, blad, _, pobrano = pobierz_strone("https://pl.indeed.com/praca/xyz", ["pl.indeed.com"], 12000)
    assert blad and not pobrano
    assert sprawdz_link("https://www.olx.pl/oferta/praca/x", set()) == "nie sprawdzono"


def test_obcy_adres_odmowa_bez_laczenia():
    tekst, blad, _, pobrano = pobierz_strone("https://evil.example.com/x", ["pracuj.pl"], 12000)
    assert blad and not pobrano and tekst.startswith("Odmowa")


def test_grupy_portali_kazda_ma_portal_do_pobrania():
    # wcześniej jedna runda dostała sam OLX: 2 płatne wyszukiwania i zero przeczytanych stron
    grupy = grupy_portali(["pracuj.pl", "rocketjobs.pl", "olx.pl", "pl.indeed.com", "linkedin.com"], 3)
    assert len(grupy) == 3
    assert all(any(not czy_blokuje_pobieranie(p) for p in g) for g in grupy)


def test_pobieranie_z_lokalnego_portalu(portal):
    tekst, blad, aktywna, pobrano = pobierz_strone(portal + "/oferta/firma-a/junior-python", ["127.0.0.1"], 12000)
    assert not blad and aktywna and pobrano and "Junior Python Developer" in tekst
    lista, *_ = pobierz_strone(portal + "/oferty/wroclaw/junior", ["127.0.0.1"], 12000)
    linki = lista.split("LINKI NA STRONIE")[1]
    assert "/oferta/firma-a/junior-python" in linki and "evil.example.com" not in linki   # obce linki odpadają
    _, _, aktywna, _ = pobierz_strone(portal + "/oferta/firma-c/archiwum", ["127.0.0.1"], 12000)
    assert not aktywna
