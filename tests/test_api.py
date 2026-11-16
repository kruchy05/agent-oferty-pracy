# API (FastAPI) i strona. Część testów potrzebuje bazy testowej (fixture baza_url), reszta działa bez niej.

import io
import re
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.api as API
from app.config import ustawienia

from .conftest import UdawanyClaude

KRYTERIA = {"kategoria": "it", "stanowisko": "junior Python", "kwalifikacje": "Python, Git", "zdalnie": True}
JA = {"X-Api-Key": "sk-ant-moj"}          # mój klucz = moje "konto"
OBCY = {"X-Api-Key": "sk-ant-obcy"}       # ktoś inny


@pytest.fixture
def claude(monkeypatch, oferty_testowe):
    c = UdawanyClaude(oferty_testowe, zly_klucz="sk-ant-zly")
    monkeypatch.setattr(API, "nowy_klient", c.klient)   # API dostaje udawanego Claude z kluczem z nagłówka
    return c


@pytest.fixture
def api(request):
    # baza testowa musi być ustawiona PRZED startem aplikacji - przy starcie API tworzy tabele
    if "baza_url" in request.fixturenames:
        request.getfixturevalue("baza_url")
    with TestClient(API.app) as t:
        yield t


def czekaj_na_koniec(api, sid, naglowki=JA, sekundy=20):
    for _ in range(sekundy * 10):
        s = api.get(f"/szukaj/{sid}", headers=naglowki).json()
        if s["status"] != "trwa":
            return s
        time.sleep(0.1)
    raise AssertionError("szukanie nie skończyło się na czas")


# ---------- bez bazy ----------

def test_strona_i_info(api):
    r = api.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "Agent ofert pracy" in r.text
    info = api.get("/info").json()
    assert set(info) == {"klucz_serwera", "model", "budzet_max_usd", "limit_dzienny"}
    assert api.get("/kategorie").json()["logistyka"] == "Logistyka / magazyn"


STATIC = Path(API.__file__).parent.parent / "static"


def test_strona_nie_wstawia_danych_jako_html():
    # dane ofert pochodzą z obcych stron - na stronie wolno je wstawiać tylko jako tekst
    for plik in ("index.html", "app.js"):
        kod = (STATIC / plik).read_text(encoding="utf-8")
        assert not re.search(r"\.(innerHTML|outerHTML)\s*[+]?=|insertAdjacentHTML\(|document\.write\(", kod), plik


def test_csp_i_brak_skryptow_inline(api):
    # CSP blokuje skrypty spoza serwera - działa tylko wtedy, gdy strona nie ma skryptów wpisanych w HTML
    r = api.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert not re.search(r"<script(?![^>]*\bsrc=)", r.text) and not re.search(r"\son\w+=", r.text)
    assert api.get("/static/app.js").status_code == 200
    assert "frame-ancestors 'none'" in api.get("/info").headers["content-security-policy"]


def test_bez_klucza_401(api):
    # bez klucza nie wiadomo, czyje oferty pokazać (a serwer nie ma swojego klucza)
    assert api.get("/oferty").status_code == 401
    assert api.get("/oferty", headers={"X-Api-Key": "zly-format"}).status_code == 400


def test_bez_bazy_503(api):
    r = api.get("/oferty", headers=JA)
    assert r.status_code == 503 and "DATABASE_URL" in r.json()["detail"]


def test_wylaczona_baza_503(api, monkeypatch):
    monkeypatch.setattr(ustawienia, "database_url", SecretStr("postgresql://postgres@127.0.0.1:1/agent_test"))
    r = api.get("/oferty", headers=JA)
    assert r.status_code == 503 and "Postgres" in r.json()["detail"]


# ---------- z bazą testową ----------

def test_pusta_baza_i_404(api, baza_url):
    assert api.get("/oferty", headers=JA).json() == []
    assert api.get("/szukania", headers=JA).json() == []
    assert api.get("/oferty/999", headers=JA).status_code == 404
    assert api.get("/szukaj/999", headers=JA).status_code == 404
    assert api.get("/oferty?pasuje=COKOLWIEK", headers=JA).status_code == 422


@pytest.mark.parametrize("naglowki, kod", [
    ({}, 401),                                   # brak klucza, a serwer nie ma swojego
    ({"X-Api-Key": "abc"}, 400),                 # zły format
    ({"X-Api-Key": "sk-ant-zly"}, 401),          # Anthropic odrzucił klucz
])
def test_klucz_api(api, baza_url, claude, naglowki, kod):
    r = api.post("/szukaj", json=KRYTERIA, headers=naglowki)
    assert r.status_code == kod
    assert "sk-ant-zly" not in r.text                    # klucz nie wraca w odpowiedzi
    assert api.get("/szukaj/1", headers=JA).status_code == 404       # nic nie zostało uruchomione


def test_zle_kryteria_422(api, baza_url, claude):
    assert api.post("/szukaj", json={**KRYTERIA, "stanowisko": "x"}, headers={"X-Api-Key": "sk-ant-dobry"}).status_code == 422
    assert api.post("/szukaj", json={**KRYTERIA, "budzet_usd": 5}, headers={"X-Api-Key": "sk-ant-dobry"}).status_code == 422


def test_pelne_szukanie_przez_api(api, baza_url, claude):
    r = api.post("/szukaj", json=KRYTERIA, headers=JA)
    assert r.status_code == 202 and r.json()["adres_statusu"] == f"/szukaj/{r.json()['id']}"
    s = czekaj_na_koniec(api, r.json()["id"])
    assert s["status"] == "gotowe" and s["liczba_ofert"] == 2 and s["koszt_usd"] > 0 and not s["na_zywo"]
    assert set(claude.klucze) == {"sk-ant-moj"}           # Claude dostał klucz użytkownika

    oferty = api.get("/oferty", headers=JA).json()
    assert {o["firma"] for o in oferty} == {"Firma A", "Firma G"}
    oid = oferty[0]["id"]
    assert api.get(f"/oferty/{oid}", headers=JA).json()["id"] == oid
    assert api.get("/szukania", headers=JA).json()[0]["stanowisko"] == "junior Python"

    # "Wysłałem CV" i cofnięcie
    data = api.post(f"/oferty/{oid}/aplikuj", headers=JA).json()["aplikowano"]
    assert data and api.post(f"/oferty/{oid}/aplikuj", headers=JA).json()["aplikowano"] == data
    assert len(api.get("/oferty?aplikowano=true", headers=JA).json()) == 1
    assert api.delete(f"/oferty/{oid}/aplikuj", headers=JA).json()["aplikowano"] is None
    assert api.post("/oferty/999999/aplikuj", headers=JA).status_code == 404

    # arkusz .ods z tymi samymi filtrami
    r = api.get("/oferty.ods?pasuje=PASUJE", headers=JA)
    with zipfile.ZipFile(io.BytesIO(r.content)) as ods:
        assert ods.namelist()[0] == "mimetype" and "Firma A" in ods.read("content.xml").decode()
    assert "attachment" in r.headers["content-disposition"]


def test_klucz_serwera_tylko_gdy_wlaczony(api, baza_url, claude, monkeypatch):
    monkeypatch.setattr(ustawienia, "anthropic_api_key", SecretStr("sk-ant-serwer"))
    r = api.post("/szukaj", json=KRYTERIA)
    assert r.status_code == 202
    czekaj_na_koniec(api, r.json()["id"], naglowki={})
    assert "sk-ant-serwer" in claude.klucze
    assert len(api.get("/oferty").json()) == 2           # bez nagłówka = oferty właściciela klucza z .env
    monkeypatch.setattr(ustawienia, "api_klucz_serwera", False)
    assert api.post("/szukaj", json=KRYTERIA).status_code == 401


def test_limit_szukan_naraz(api, baza_url, claude, monkeypatch):
    monkeypatch.setattr(ustawienia, "api_maks_szukan", 1)
    wstrzymaj = threading.Event()
    zwykla_obsluga = claude.obsluz

    def wolna_obsluga(req):
        if req.method == "POST":
            wstrzymaj.wait(10)            # szukanie "wisi", dopóki test nie puści
        return zwykla_obsluga(req)

    claude.obsluz = wolna_obsluga
    wyniki = {}
    # TestClient czeka na zadania w tle przed oddaniem odpowiedzi, więc pierwsze szukanie puszczam w wątku
    w = threading.Thread(target=lambda: wyniki.setdefault("a", api.post("/szukaj", json=KRYTERIA, headers={"X-Api-Key": "sk-ant-a"})))
    w.start()
    for _ in range(100):
        if API.zadania.ile_trwa():
            break
        time.sleep(0.05)
    assert api.post("/szukaj", json=KRYTERIA, headers={"X-Api-Key": "sk-ant-b"}).status_code == 429
    assert api.post("/szukaj", json=KRYTERIA, headers={"X-Api-Key": "sk-ant-a"}).json()["detail"].startswith("Twoje")
    assert api.get("/szukaj/1", headers={"X-Api-Key": "sk-ant-b"}).status_code == 404   # nie moje
    trwajace = api.get("/szukaj/1", headers={"X-Api-Key": "sk-ant-a"}).json()
    assert trwajace["status"] == "trwa" and trwajace["na_zywo"]
    wstrzymaj.set()
    w.join(15)
    assert wyniki["a"].status_code == 202 and API.zadania.ile_trwa() == 0


def test_przerwane_szukanie(api, baza_url):
    import psycopg
    with psycopg.connect(baza_url, autocommit=True) as c:
        sid = c.execute("INSERT INTO szukania (status) VALUES ('trwa') RETURNING id").fetchone()[0]
        c.execute("UPDATE szukania SET uzytkownik = %s WHERE id = %s", (API.id_uzytkownika("sk-ant-moj"), sid))
    assert api.get(f"/szukaj/{sid}", headers=JA).json()["status"] == "przerwane"


def test_kazdy_widzi_tylko_swoje(api, baza_url, claude):
    sid = api.post("/szukaj", json=KRYTERIA, headers=JA).json()["id"]
    czekaj_na_koniec(api, sid)
    moja = api.get("/oferty", headers=JA).json()[0]["id"]

    # obcy: pusta lista, a moje id wyglądają jak nieistniejące (404, a nie 403 - nie zdradzam, że są)
    assert api.get("/oferty", headers=OBCY).json() == []
    assert api.get("/szukania", headers=OBCY).json() == []
    assert api.get(f"/oferty/{moja}", headers=OBCY).status_code == 404
    assert api.post(f"/oferty/{moja}/aplikuj", headers=OBCY).status_code == 404
    assert api.get(f"/szukaj/{sid}", headers=OBCY).status_code == 404
    with zipfile.ZipFile(io.BytesIO(api.get("/oferty.ods", headers=OBCY).content)) as ods:
        assert "Firma A" not in ods.read("content.xml").decode()

    # obcy szuka tego samego - dostaje te same oferty jako SWOJE (link może być u dwóch osób)
    czekaj_na_koniec(api, api.post("/szukaj", json=KRYTERIA, headers=OBCY).json()["id"], naglowki=OBCY)
    assert len(api.get("/oferty", headers=OBCY).json()) == 2 and len(api.get("/oferty", headers=JA).json()) == 2
    assert api.get(f"/oferty/{moja}", headers=JA).json()["aplikowano"] is None


def test_limit_dzienny(api, baza_url, claude, monkeypatch):
    monkeypatch.setattr(ustawienia, "api_limit_dzienny", 1)
    czekaj_na_koniec(api, api.post("/szukaj", json=KRYTERIA, headers=JA).json()["id"])
    r = api.post("/szukaj", json=KRYTERIA, headers=JA)
    assert r.status_code == 429 and "na dobę" in r.json()["detail"]
    assert api.post("/szukaj", json=KRYTERIA, headers=OBCY).status_code == 202    # limit jest na osobę
