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


def czekaj_na_koniec(api, sid, sekundy=20):
    for _ in range(sekundy * 10):
        s = api.get(f"/szukaj/{sid}").json()
        if s["status"] != "trwa":
            return s
        time.sleep(0.1)
    raise AssertionError("szukanie nie skończyło się na czas")


# ---------- bez bazy ----------

def test_strona_i_info(api):
    r = api.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "Agent ofert pracy" in r.text
    info = api.get("/info").json()
    assert set(info) == {"klucz_serwera", "model", "budzet_max_usd"}
    assert api.get("/kategorie").json()["logistyka"] == "Logistyka / magazyn"


def test_strona_nie_wstawia_danych_jako_html():
    # dane ofert pochodzą z obcych stron - na stronie wolno je wstawiać tylko jako tekst
    html = (Path(API.__file__).parent.parent / "static" / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"\.(innerHTML|outerHTML)\s*[+]?=|insertAdjacentHTML\(|document\.write\(", html)


def test_bez_bazy_503(api):
    r = api.get("/oferty")
    assert r.status_code == 503 and "DATABASE_URL" in r.json()["detail"]


def test_wylaczona_baza_503(api, monkeypatch):
    monkeypatch.setattr(ustawienia, "database_url", SecretStr("postgresql://postgres@127.0.0.1:1/agent_test"))
    r = api.get("/oferty")
    assert r.status_code == 503 and "Postgres" in r.json()["detail"]


# ---------- z bazą testową ----------

def test_pusta_baza_i_404(api, baza_url):
    assert api.get("/oferty").json() == []
    assert api.get("/oferty/999").status_code == 404
    assert api.get("/szukaj/999").status_code == 404
    assert api.get("/oferty?pasuje=COKOLWIEK").status_code == 422


@pytest.mark.parametrize("naglowki, kod", [
    ({}, 401),                                   # brak klucza, a serwer nie ma swojego
    ({"X-Api-Key": "abc"}, 400),                 # zły format
    ({"X-Api-Key": "sk-ant-zly"}, 401),          # Anthropic odrzucił klucz
])
def test_klucz_api(api, baza_url, claude, naglowki, kod):
    r = api.post("/szukaj", json=KRYTERIA, headers=naglowki)
    assert r.status_code == kod
    assert "sk-ant-zly" not in r.text                    # klucz nie wraca w odpowiedzi
    assert api.get("/szukaj/1").status_code == 404       # nic nie zostało uruchomione


def test_zle_kryteria_422(api, baza_url, claude):
    assert api.post("/szukaj", json={**KRYTERIA, "stanowisko": "x"}, headers={"X-Api-Key": "sk-ant-dobry"}).status_code == 422
    assert api.post("/szukaj", json={**KRYTERIA, "budzet_usd": 5}, headers={"X-Api-Key": "sk-ant-dobry"}).status_code == 422


def test_pelne_szukanie_przez_api(api, baza_url, claude):
    r = api.post("/szukaj", json=KRYTERIA, headers={"X-Api-Key": "sk-ant-moj"})
    assert r.status_code == 202 and r.json()["adres_statusu"] == f"/szukaj/{r.json()['id']}"
    s = czekaj_na_koniec(api, r.json()["id"])
    assert s["status"] == "gotowe" and s["liczba_ofert"] == 2 and s["koszt_usd"] > 0 and not s["na_zywo"]
    assert set(claude.klucze) == {"sk-ant-moj"}           # Claude dostał klucz użytkownika

    oferty = api.get("/oferty").json()
    assert {o["firma"] for o in oferty} == {"Firma A", "Firma G"}
    oid = oferty[0]["id"]
    assert api.get(f"/oferty/{oid}").json()["id"] == oid

    # "Wysłałem CV" i cofnięcie
    data = api.post(f"/oferty/{oid}/aplikuj").json()["aplikowano"]
    assert data and api.post(f"/oferty/{oid}/aplikuj").json()["aplikowano"] == data
    assert len(api.get("/oferty?aplikowano=true").json()) == 1
    assert api.delete(f"/oferty/{oid}/aplikuj").json()["aplikowano"] is None
    assert api.post("/oferty/999999/aplikuj").status_code == 404

    # arkusz .ods z tymi samymi filtrami
    r = api.get("/oferty.ods?pasuje=PASUJE")
    with zipfile.ZipFile(io.BytesIO(r.content)) as ods:
        assert ods.namelist()[0] == "mimetype" and "Firma A" in ods.read("content.xml").decode()
    assert "attachment" in r.headers["content-disposition"]


def test_klucz_serwera_tylko_gdy_wlaczony(api, baza_url, claude, monkeypatch):
    monkeypatch.setattr(ustawienia, "anthropic_api_key", SecretStr("sk-ant-serwer"))
    r = api.post("/szukaj", json=KRYTERIA)
    assert r.status_code == 202
    czekaj_na_koniec(api, r.json()["id"])
    assert "sk-ant-serwer" in claude.klucze
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
    trwajace = api.get("/szukaj/1").json()
    assert trwajace["status"] == "trwa" and trwajace["na_zywo"]
    wstrzymaj.set()
    w.join(15)
    assert wyniki["a"].status_code == 202 and API.zadania.ile_trwa() == 0


def test_przerwane_szukanie(api, baza_url):
    import psycopg
    with psycopg.connect(baza_url, autocommit=True) as c:
        sid = c.execute("INSERT INTO szukania (status) VALUES ('trwa') RETURNING id").fetchone()[0]
    assert api.get(f"/szukaj/{sid}").json()["status"] == "przerwane"
