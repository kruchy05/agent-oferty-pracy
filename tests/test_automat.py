# Automatyczne szukanie (co X dni), zaszyfrowane klucze i powiadomienia push.

import base64
import datetime
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.api as API
from app import baza, harmonogram, powiadomienia, tozsamosc
from app.config import ustawienia

from .conftest import UdawanyClaude

KRYTERIA = {"kategoria": "it", "stanowisko": "junior Python", "kwalifikacje": "Python, Git", "zdalnie": True}
JA = {"X-Api-Key": "sk-ant-moj"}


# ---------- szyfrowanie ----------

def test_klucz_zaszyfrowany_i_tylko_tym_sekretem(monkeypatch):
    szyfr = tozsamosc.zaszyfruj("sk-ant-tajny")
    assert b"sk-ant-tajny" not in szyfr
    assert tozsamosc.odszyfruj(szyfr) == "sk-ant-tajny"
    monkeypatch.setattr(ustawienia, "sekret_serwera", SecretStr("inny-sekret"))
    assert tozsamosc.odszyfruj(szyfr) is None              # inny serwer / zmieniony sekret = nie odczyta
    assert tozsamosc.odszyfruj(b"smieci") is None


# ---------- powiadomienia: prawdziwe szyfrowanie Web Push ----------

def _b64(dane):
    return base64.urlsafe_b64encode(dane).rstrip(b"=").decode()


class _UslugaPush(BaseHTTPRequestHandler):
    """Udaje usługę push przeglądarki (np. FCM): zapamiętuje, co dostała, i odpowiada kodem z adresu."""
    odebrane = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        tresc = self.rfile.read(int(self.headers["Content-Length"]))
        self.odebrane.append((self.path, dict(self.headers), tresc))
        self.send_response(410 if "wygasla" in self.path else 201)
        self.end_headers()


@pytest.fixture
def usluga_push():
    _UslugaPush.odebrane = []
    serwer = ThreadingHTTPServer(("127.0.0.1", 0), _UslugaPush)
    threading.Thread(target=serwer.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{serwer.server_address[1]}"
    serwer.shutdown()


def przegladarka(adres):
    """Klucze, jakie generuje przeglądarka przy subskrypcji (p256dh + auth)."""
    prywatny = ec.generate_private_key(ec.SECP256R1())
    publiczny = prywatny.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return prywatny, auth, {"endpoint": adres, "keys": {"p256dh": _b64(publiczny), "auth": _b64(auth)}}


def test_powiadomienie_szyfrowane_i_podpisane(usluga_push):
    import http_ece
    from py_vapid import Vapid
    prywatny, auth, subskrypcja = przegladarka(usluga_push + "/push/abc")

    assert powiadomienia.wyslij(subskrypcja, {"tytul": "Nowe oferty: 3", "tresc": "magazynier, Legnica"}) is True
    _, naglowki, tresc = _UslugaPush.odebrane[0]

    # tylko przeglądarka z kluczem prywatnym odczyta treść - usługa push widzi same zaszyfrowane bajty
    assert b"magazynier" not in tresc
    odszyfrowane = json.loads(http_ece.decrypt(tresc, private_key=prywatny, auth_secret=auth, version="aes128gcm"))
    assert odszyfrowane == {"tytul": "Nowe oferty: 3", "tresc": "magazynier, Legnica"}
    # podpis VAPID pasuje do mojego klucza publicznego
    naglowki = {k.lower(): v for k, v in naglowki.items()}
    assert naglowki["content-encoding"] == "aes128gcm" and int(naglowki["ttl"]) == 24 * 3600
    pola = dict(p.split("=", 1) for p in naglowki["authorization"].removeprefix("vapid ").split(","))
    assert pola["k"] == powiadomienia.klucze_vapid()["publiczny"]
    assert Vapid.verify(naglowki["authorization"])   # podpis JWT pasuje do klucza z nagłówka


def test_wygasla_subskrypcja_jest_sprzatana(usluga_push, baza_url):
    baza.przygotuj_baze(baza_url)
    _, _, dobra = przegladarka(usluga_push + "/push/dziala")
    _, _, martwa = przegladarka(usluga_push + "/push/wygasla")
    baza.zapisz_subskrypcje(baza_url, "ja", dobra)
    baza.zapisz_subskrypcje(baza_url, "ja", martwa)
    assert powiadomienia.powiadom(baza_url, "ja", "Test", "x") == 1
    assert [s["endpoint"] for s in baza.subskrypcje(baza_url, "ja")] == [dobra["endpoint"]]


def test_klucze_vapid_tworzone_raz(tmp_path, monkeypatch):
    monkeypatch.setattr(ustawienia, "vapid_prywatny", None)
    monkeypatch.setattr(ustawienia, "vapid_publiczny", None)
    monkeypatch.setattr(powiadomienia, "PLIK_VAPID", tmp_path / "vapid.json")
    monkeypatch.setattr(powiadomienia, "KATALOG_DANYCH", tmp_path)
    monkeypatch.setattr(powiadomienia, "_klucze", None)
    k1 = powiadomienia.klucze_vapid()
    monkeypatch.setattr(powiadomienia, "_klucze", None)   # "restart serwera"
    assert powiadomienia.klucze_vapid() == k1 and len(base64.urlsafe_b64decode(k1["publiczny"] + "==")) == 65


# ---------- API automatu ----------

@pytest.fixture
def claude(monkeypatch, oferty_testowe):
    c = UdawanyClaude(oferty_testowe, zly_klucz="sk-ant-zly")
    monkeypatch.setattr(API, "nowy_klient", c.klient)
    monkeypatch.setattr(harmonogram, "nowy_klient", c.klient)
    return c


@pytest.fixture
def api(baza_url):
    with TestClient(API.app) as t:
        yield t


def test_automat_wlacz_pokaz_wylacz(api, baza_url, claude):
    assert api.get("/automat", headers=JA).json() is None
    bez_zgody = {"kryteria": KRYTERIA, "co_ile_dni": 2, "godzina": 7}
    assert api.put("/automat", json=bez_zgody, headers=JA).status_code == 422          # bez zgody nie zapisuję klucza
    assert api.put("/automat", json={**bez_zgody, "zgoda_na_zapis_klucza": True},
                   headers={"X-Api-Key": "sk-ant-zly"}).status_code == 401               # niedziałającego też nie

    r = api.put("/automat", json={**bez_zgody, "zgoda_na_zapis_klucza": True}, headers=JA)
    assert r.status_code == 200 and "sk-ant" not in r.text
    a = r.json()
    assert a["wlaczony"] and a["kryteria"]["stanowisko"] == "junior Python"
    # pierwsze szukanie za 2 dni o 7:00 (nie od razu - przed chwilą szukałem ręcznie)
    nastepne = datetime.datetime.fromisoformat(a["nastepne"])
    assert nastepne.hour == 7 and (nastepne.date() - datetime.date.today()).days in (1, 2, 3)

    with psycopg.connect(baza_url) as c:
        zapisany = bytes(c.execute("SELECT klucz_zaszyfrowany FROM automaty").fetchone()[0])
    assert b"sk-ant-moj" not in zapisany and tozsamosc.odszyfruj(zapisany) == "sk-ant-moj"

    assert api.get("/automat", headers={"X-Api-Key": "sk-ant-obcy"}).json() is None
    assert api.delete("/automat", headers=JA).status_code == 204
    with psycopg.connect(baza_url) as c:
        assert c.execute("SELECT count(*) FROM automaty").fetchone()[0] == 0       # klucz usunięty z serwera


def test_subskrypcje_przez_api(api, baza_url, monkeypatch):
    assert len(api.get("/push/klucz").json()["klucz_publiczny"]) > 80
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "x" * 87, "auth": "y" * 22}}
    assert api.post("/push/subskrypcja", json={**sub, "endpoint": "http://zly.example/1"}, headers=JA).status_code == 400
    assert api.post("/push/test", headers=JA).status_code == 404                     # nic jeszcze nie zapisane
    assert api.post("/push/subskrypcja", json=sub, headers=JA).status_code == 201
    wyslane = []
    monkeypatch.setattr(powiadomienia, "wyslij", lambda s, w: wyslane.append((s["endpoint"], w)) or True)
    assert api.post("/push/test", headers=JA).json() == {"wyslano": 1}
    assert wyslane[0][0] == sub["endpoint"]
    assert api.request("DELETE", "/push/subskrypcja", json=sub, headers=JA).status_code == 204
    assert api.post("/push/test", headers=JA).status_code == 404


# ---------- harmonogram ----------

def ustaw_automat(baza_url, klucz="sk-ant-moj", dni_temu=3, godzina=0, co_ile_dni=2):
    baza.przygotuj_baze(baza_url)
    ja = tozsamosc.id_uzytkownika(klucz)
    baza.zapisz_automat(baza_url, ja, KRYTERIA, co_ile_dni, godzina, tozsamosc.zaszyfruj(klucz))
    with psycopg.connect(baza_url, autocommit=True) as c:
        c.execute("UPDATE automaty SET ostatnio = NOW() - make_interval(days => %s) WHERE uzytkownik = %s", (dni_temu, ja))
    return ja


def czekaj_az_skonczy(sekundy=20):
    for _ in range(sekundy * 10):
        if API.zadania.ile_trwa() == 0:
            return
        time.sleep(0.1)
    raise AssertionError("automat nie skończył")


@pytest.fixture
def push(monkeypatch):
    wyslane = []
    monkeypatch.setattr(powiadomienia, "powiadom", lambda url, kto, tytul, tresc, adres="/": wyslane.append((kto, tytul, tresc, adres)) or 1)
    return wyslane


def test_harmonogram_szuka_i_powiadamia(baza_url, claude, push):
    ja = ustaw_automat(baza_url)
    sid = harmonogram.tyknij(baza_url)
    assert sid is not None
    assert harmonogram.tyknij(baza_url) is None            # ten sam automat nie rusza drugi raz
    czekaj_az_skonczy()

    assert set(claude.klucze) == {"sk-ant-moj"}             # szukał zapisanym (odszyfrowanym) kluczem
    with psycopg.connect(baza_url) as c:
        assert c.execute("SELECT automatyczne, status FROM szukania WHERE id = %s", (sid,)).fetchone() == (True, "gotowe")
    assert {o["firma"] for o in baza.lista_ofert(baza_url, ja)} == {"Firma A", "Firma G"}
    assert push == [(ja, "Nowe oferty: 2", "junior Python", f"/?szukanie={sid}")]
    assert baza.automat(baza_url, ja)["ostatni_wynik"].startswith("2 nowych ofert")

    # następnym razem te same oferty to już nic nowego - bez powiadomienia
    with psycopg.connect(baza_url, autocommit=True) as c:
        c.execute("UPDATE automaty SET ostatnio = NOW() - INTERVAL '3 days'")
    harmonogram.tyknij(baza_url)
    czekaj_az_skonczy()
    assert len(push) == 1


@pytest.mark.parametrize("dni_temu, co_ile_dni, godzina, rusza", [
    (2, 2, 0, True),      # minęły 2 dni kalendarzowe, godzina startu minęła
    (1, 2, 0, False),     # dopiero 1 dzień
    (0, 1, 0, False),     # dziś już był
    (5, 2, 23, None),     # pora dnia jeszcze nie przyszła (chyba że test leci po 23:00)
])
def test_pora_automatu(baza_url, claude, push, dni_temu, co_ile_dni, godzina, rusza):
    ustaw_automat(baza_url, dni_temu=dni_temu, co_ile_dni=co_ile_dni, godzina=godzina)
    teraz = datetime.datetime.now(__import__("zoneinfo").ZoneInfo(ustawienia.strefa_czasowa))
    if rusza is None:
        rusza = teraz.hour >= 23
    assert (harmonogram.tyknij(baza_url) is not None) == rusza
    czekaj_az_skonczy()


def test_zajety_serwer_nie_zjada_kolejki(baza_url, claude, push, monkeypatch):
    ja = ustaw_automat(baza_url)
    zeton, _ = API.zadania.zajmij_miejsce(5, ja)                 # ta osoba właśnie szuka ręcznie
    przed = baza.automat(baza_url, ja)["ostatnio"]
    assert harmonogram.tyknij(baza_url) is None
    assert baza.automat(baza_url, ja)["ostatnio"] == przed      # "ostatnio" cofnięte - ruszy później
    API.zadania.zwolnij(zeton)
    assert harmonogram.tyknij(baza_url) is not None
    czekaj_az_skonczy()


def test_niedzialajacy_klucz_wylacza_automat(baza_url, claude, push):
    ja = ustaw_automat(baza_url, klucz="sk-ant-zly")
    harmonogram.tyknij(baza_url)
    czekaj_az_skonczy()
    a = baza.automat(baza_url, ja)
    assert not a["wlaczony"] and "przestał działać" in a["ostatni_wynik"]
    assert push[0][1] == "Automatyczne szukanie wyłączone"
    with psycopg.connect(baza_url, autocommit=True) as c:
        c.execute("UPDATE automaty SET ostatnio = NOW() - INTERVAL '9 days'")
    assert harmonogram.tyknij(baza_url) is None                  # wyłączony nie rusza


def test_zmieniony_sekret_wylacza_automat(baza_url, claude, push, monkeypatch):
    ja = ustaw_automat(baza_url)
    monkeypatch.setattr(ustawienia, "sekret_serwera", SecretStr("nowy-sekret"))
    assert harmonogram.tyknij(baza_url) is None
    with psycopg.connect(baza_url) as c:
        assert c.execute("SELECT wlaczony FROM automaty").fetchone() == (False,)
    assert push and "Włącz automat od nowa" in push[0][2]
