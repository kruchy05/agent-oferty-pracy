# Walidacja kryteriów z formularza, adresy stron startowych i liczenie kosztów.

import types

import pytest
from pydantic import ValidationError

from app.agent import klucz_duplikatu
from app.koszty import BudzetWyczerpany, Licznik
from app.modele import KryteriaSzukania, Oferta
from app.portale import portale_dla
from app.strony_startowe import adresy_startowe, lista_miast, slug


# ---------- kryteria ----------

@pytest.mark.parametrize("dane", [
    dict(stanowisko="x"),                                   # za krótkie
    dict(stanowisko="kelner", kwalifikacje="a" * 1001),     # za długie kwalifikacje
    dict(stanowisko="kelner", budzet_usd=2),                # budżet > 1 $
    dict(stanowisko="kelner", budzet_usd=0),
    dict(stanowisko="kelner", kategoria="kosmonauta"),
    dict(stanowisko="kelner", doswiadczenie="100_lat"),
])
def test_zle_kryteria_odrzucone(dane):
    with pytest.raises(ValidationError):
        KryteriaSzukania(**dane)


def test_dobre_kryteria_z_domyslnymi():
    k = KryteriaSzukania(stanowisko="magazynier", kategoria="logistyka")
    assert k.zdalnie is True and k.budzet_usd == 0.15


# ---------- strony startowe ----------

def test_slug_i_miasta():
    assert slug("Wrocław") == "wroclaw" and slug("Nowa Sól") == "nowa-sol" and slug("Łódź") == "lodz"
    assert lista_miast("Legnica / Wrocław") == ["Legnica", "Wrocław"]


def test_adresy_startowe_w_formatach_z_prawdziwych_portali():
    k = KryteriaSzukania(kategoria="it", stanowisko="junior Python developer / junior AI engineer",
                         kwalifikacje="Python", miasto="Legnica / Wrocław", zdalnie=True)
    portale = portale_dla("it", True)
    adresy = [a for g in range(3) for a in adresy_startowe(k, portale[g::3], 4)]
    assert "https://www.pracuj.pl/praca/junior%20python%20developer;kw/wroclaw;wp" in adresy
    assert "https://justjoin.it/job-offers/wroclaw/python" in adresy
    assert "https://theprotocol.it/filtry/junior;p/wroclaw;wp" in adresy
    assert "https://remoteok.com/remote-junior+python-jobs" in adresy


def test_adresy_startowe_dla_magazyniera_i_limit():
    k = KryteriaSzukania(kategoria="logistyka", stanowisko="magazynier", miasto="Legnica", zdalnie=False)
    assert adresy_startowe(k, portale_dla("logistyka", False), 4) == ["https://www.pracuj.pl/praca/magazynier;kw/legnica;wp"]
    assert adresy_startowe(k, ["olx.pl", "pl.indeed.com"], 4) == []        # te portale nie mają wzoru adresu
    assert len(adresy_startowe(KryteriaSzukania(stanowisko="junior Python", miasto="Wrocław"),
                               portale_dla("it", True), 2)) == 2


# ---------- koszty ----------

def zuzycie(**k):
    return types.SimpleNamespace(**{"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
                                    "cache_read_input_tokens": 0, "server_tool_use": None, **k})


def test_koszt_z_cache_haiku():
    # 1000 zwykłe * 1 + 20000 zapis * 1,25 + 50000 odczyt * 0,1 = 31000 "zwykłych" tokenów -> 0,0031 $ + wyjście 0,0005 $
    l = Licznik("claude-haiku-5-5", 1)
    l.licz(zuzycie(input_tokens=1000, cache_creation_input_tokens=20000, cache_read_input_tokens=50000, output_tokens=1000))
    assert l.usd == pytest.approx(0.0036)
    bez_cache = Licznik("claude-haiku-5-5", 1)
    bez_cache.licz(zuzycie(input_tokens=71000, output_tokens=1000))
    assert bez_cache.usd > 2 * l.usd


def test_sonnet_odczyt_cache_po_005():
    l = Licznik("claude-sonnet-5-5", 1)
    l.licz(zuzycie(cache_read_input_tokens=100_000))
    assert l.usd == pytest.approx(0.01)


def test_budzet_pilnowany():
    l = Licznik("claude-haiku-5-5", 0.001)
    l.licz(zuzycie(input_tokens=20_000))
    with pytest.raises(BudzetWyczerpany):
        l.pilnuj()


# ---------- duplikaty ----------

def oferta(stanowisko, firma):
    return Oferta(link="x", stanowisko=stanowisko, firma=firma, miasto="-", poziom="nieznany", wymagania=[],
                  spelnione_wymagania=[], brakujace_wymagania=[], pasuje="PASUJE")


@pytest.mark.parametrize("a, b", [
    (("Operator, Production (Warehouse worker)", "Gates Corporation"), ("Production Operator (Warehouse worker)", "Gates Corporation")),
    (("Magazynier (K/M/X)", "3W S.A."), ("Magazynier", "3W")),
    (("Magazynier", "STIGAL Automation Sp. z o.o."), ("magazynier", "Stigal Automation")),
])
def test_ta_sama_oferta_pod_innym_opisem(a, b):
    assert klucz_duplikatu(oferta(*a)) == klucz_duplikatu(oferta(*b))


def test_rozne_oferty_nie_sa_duplikatami():
    assert klucz_duplikatu(oferta("Magazynier", "Raben")) != klucz_duplikatu(oferta("Magazynier", "GEODIS"))
    assert klucz_duplikatu(oferta("Magazynier", "Raben")) != klucz_duplikatu(oferta("Kierownik magazynu", "Raben"))
