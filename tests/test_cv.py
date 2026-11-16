# Dopasowanie do CV: PDF -> profil zawodowy (bez danych osobowych) -> formularz.

import base64

import psycopg
import pytest
from fastapi.testclient import TestClient

import app.api as API
from app import cv

from .conftest import UdawanyClaude

JA = {"X-Api-Key": "sk-ant-moj"}
PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF"   # Claude jest udawany, więc treść nie gra roli


@pytest.fixture
def claude(monkeypatch):
    c = UdawanyClaude(zly_klucz="sk-ant-zly")
    monkeypatch.setattr(API, "nowy_klient", c.klient)
    return c


@pytest.fixture
def api(baza_url):
    with TestClient(API.app) as t:
        yield t


def wyslij(api, dane, naglowki=JA):
    return api.post("/cv", content=dane, headers={**naglowki, "Content-Type": "application/pdf"})


def test_cv_wypelnia_profil(api, baza_url, claude):
    r = wyslij(api, PDF)
    assert r.status_code == 200
    wynik = r.json()
    assert wynik["profil"]["kwalifikacje"].startswith("Python") and wynik["profil"]["doswiadczenie"] == "do_2_lat"
    assert 0 < wynik["koszt_usd"] < 0.01

    # do Claude poszedł blok "document" z tym PDF-em i polecenie, żeby pominąć dane osobowe
    z = claude.zapytania[-1]
    dokument = z["messages"][0]["content"][0]
    assert dokument["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(dokument["source"]["data"]) == PDF
    assert "danych osobowych" in z["system"] and "ignoruj" in z["system"]

    assert api.get("/cv", headers=JA).json()["stanowiska"] == ["junior Python developer", "stażysta AI"]
    assert api.get("/cv", headers={"X-Api-Key": "sk-ant-obcy"}).json() is None       # cudzy profil niewidoczny
    with psycopg.connect(baza_url) as c:
        zapisane = str(c.execute("SELECT profil FROM profile").fetchone()[0])
    assert "PDF" not in zapisane                                                       # samo CV nie jest zapisywane
    assert api.delete("/cv", headers=JA).status_code == 204 and api.get("/cv", headers=JA).json() is None


@pytest.mark.parametrize("dane, kod, tekst", [
    (b"to nie jest pdf", 400, "nie jest plik PDF"),
    (b"%PDF-" + b"0" * (cv.MAKS_ROZMIAR_CV + 1), 413, "za duży"),
], ids=["nie-pdf", "za-duzy"])
def test_zle_pliki(api, baza_url, claude, dane, kod, tekst):
    r = wyslij(api, dane)
    assert r.status_code == kod and tekst in r.json()["detail"]
    assert claude.zapytania == []          # nic nie poszło do Claude - zero kosztów


def test_zly_klucz_i_model_bez_pdf(api, baza_url, claude, monkeypatch):
    assert wyslij(api, PDF, {"X-Api-Key": "sk-ant-zly"}).status_code == 401
    bez_pdf = UdawanyClaude(pdf=False)
    monkeypatch.setattr(API, "nowy_klient", bez_pdf.klient)
    r = wyslij(api, PDF)
    assert r.status_code == 400 and "nie czyta plików PDF" in r.json()["detail"] and bez_pdf.zapytania == []
