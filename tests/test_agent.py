# Cały przebieg agenta (krok 1 -> 2 -> 3) z udawanym Claude i lokalnym portalem.

import anthropic
import httpx2
import pytest

import app.agent as AG
from app.agent import Przebieg
from app.config import ustawienia
from app.modele import KryteriaSzukania

from .conftest import UdawanyClaude, blok_oferty, wiadomosc


def uruchom(claude, kryteria=None, **kw):
    p = Przebieg(claude.klient(), kryteria or KryteriaSzukania(stanowisko="junior Python", kwalifikacje="Python, Git"), **kw)
    p.uruchom()
    return p


def test_krok3_odrzuca_z_powodem(oferty_testowe):
    p = uruchom(UdawanyClaude(oferty_testowe))
    assert p.status == "gotowe"
    statusy = {o.firma: s for o, s in p.wyniki()}
    assert statusy == {"Firma A": "aktywna", "Firma G": "nie sprawdzono"}     # G: portal blokuje skrypty (403)
    assert sorted(powod for _, powod in p.odrzucone) == ["wygasłe", "za stare", "zły poziom"]


def test_spelnione_i_brakujace_wymagania(oferty_testowe):
    p = uruchom(UdawanyClaude(oferty_testowe))
    a = next(o for o, _ in p.wyniki() if o.firma == "Firma A")
    assert a.spelnione_wymagania == ["Python", "Git"] and a.brakujace_wymagania == ["Docker"]


def test_widziane_oferty_pomijane(oferty_testowe, portal):
    p = uruchom(UdawanyClaude(oferty_testowe), widziane=frozenset({portal + "/oferta/firma-a/junior-python"}))
    assert "Firma A" not in {o.firma for o, _ in p.wyniki()}


def test_duplikat_pod_innym_linkiem_pominiety(portal):
    oferty = [blok_oferty(portal + "/oferta/firma-a/junior-python", "Junior Python Developer (K/M)", "Firma A Sp. z o.o."),
              blok_oferty(portal + "/oferta/firma-a/junior-python?ref=2", "Junior Python Developer", "Firma A")]
    p = uruchom(UdawanyClaude(oferty))
    assert len(p.wyniki()) == 1


def test_polecenie_zawiera_kryteria_i_zasady_bezpieczenstwa(oferty_testowe):
    claude = UdawanyClaude(oferty_testowe)
    uruchom(claude, KryteriaSzukania(kategoria="logistyka", stanowisko="magazynier", kwalifikacje="UDT",
                                     miasto="Legnica", zdalnie=False))
    z = claude.szukajace[0]
    tresc = z["messages"][0]["content"]
    assert "Logistyka / magazyn" in tresc and "<kwalifikacje>UDT</kwalifikacje>" in tresc
    assert "bez pracy w pełni zdalnej" in tresc
    assert "BEZPIECZEŃSTWO" in z["system"]
    assert z.get("cache_control") == {"type": "ephemeral"}       # cache promptu w pętli narzędzi


def test_doswiadczenie_5_plus_odrzuca_juniorow(oferty_testowe):
    p = uruchom(UdawanyClaude(oferty_testowe), KryteriaSzukania(stanowisko="programista Python", doswiadczenie="5_plus"))
    assert {o.firma for o, _ in p.wyniki()} == {"Firma E"}


def test_strony_startowe_trafiaja_do_polecenia(oferty_testowe, portal, monkeypatch):
    monkeypatch.setattr(ustawienia, "max_stron_startowych", 4)
    monkeypatch.setattr(AG, "adresy_startowe", lambda k, d, limit: [portal + "/oferty/wroclaw/junior", portal + "/nie/ma"])
    claude = UdawanyClaude(oferty_testowe)
    p = uruchom(claude)
    tresc = claude.szukajace[0]["messages"][0]["content"]
    assert f'<strona url="{portal}/oferty/wroclaw/junior">' in tresc and "/nie/ma" not in tresc
    assert "TYLKO wtedy" in tresc                       # web_search tylko jako rezerwa
    assert p.licznik.wyszukiwania == 0 and p.licznik.pobrania >= 1


def test_budzet_konczy_szukanie(oferty_testowe):
    p = uruchom(UdawanyClaude(oferty_testowe), KryteriaSzukania(stanowisko="junior Python", budzet_usd=0.0005))
    assert p.status == "budzet"


def test_budzet_przyciety_do_limitu_serwera(monkeypatch):
    monkeypatch.setattr(ustawienia, "budzet_max_usd", 0.5)
    p = Przebieg(UdawanyClaude().klient(), KryteriaSzukania(stanowisko="kelner", budzet_usd=1.0))
    assert p.licznik.budzet_usd == 0.5


def test_dwa_przebiegi_nie_dziela_stanu():
    p1 = Przebieg(UdawanyClaude().klient(), KryteriaSzukania(stanowisko="junior Python"))
    p1.portale.remove("justjoin.it")
    p2 = Przebieg(UdawanyClaude().klient(), KryteriaSzukania(stanowisko="junior Python"))
    assert "justjoin.it" in p2.portale and p1.dobre is not p2.dobre


# ---------- wyszukiwarka z dynamic filtering ----------

def typ_wyszukiwarki(claude):
    return claude.szukajace[0]["tools"][0]["type"]


def test_dynamic_filtering_gdy_model_ma_code_execution():
    claude = UdawanyClaude(dynamiczne=True)
    p = uruchom(claude)
    assert p.dynamiczne and typ_wyszukiwarki(claude) == "web_search_20260318"
    assert claude.szukajace[0]["tools"][0].get("response_inclusion") == "excluded"


def test_zwykla_wyszukiwarka_gdy_model_nie_ma_code_execution():
    claude = UdawanyClaude(dynamiczne=False)
    uruchom(claude)
    assert typ_wyszukiwarki(claude) == "web_search_20250305"


def test_ustawienie_nie_wymusza_zwyklej(monkeypatch):
    monkeypatch.setattr(ustawienia, "web_search_dynamiczne", "nie")
    claude = UdawanyClaude(dynamiczne=True)
    uruchom(claude)
    assert typ_wyszukiwarki(claude) == "web_search_20250305"


def test_400_allowed_callers_wraca_do_zwyklej():
    claude = UdawanyClaude(dynamiczne=True)
    zwykla_obsluga = claude.obsluz

    def obsluz(req):
        if req.method == "POST" and b"web_search_20260318" in req.content:
            return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error",
                                   "message": "This model does not support programmatic tool calling. Set allowed_callers to [\"direct\"]."}})
        return zwykla_obsluga(req)

    klient = anthropic.Anthropic(api_key="t", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(obsluz)))
    p = Przebieg(klient, KryteriaSzukania(stanowisko="junior Python"))
    p.uruchom()
    assert p.status == "gotowe" and not p.dynamiczne


def test_blad_api_nie_wywala_programu():
    def obsluz(req):
        if req.method == "GET":
            return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})
        return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})

    klient = anthropic.Anthropic(api_key="t", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(obsluz)))
    p = Przebieg(klient, KryteriaSzukania(stanowisko="junior Python"))
    p.uruchom()                       # uruchom() nigdy nie rzuca wyjątku
    assert p.status == "blad" and p.blad
