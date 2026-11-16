# PostgreSQL. Te testy działają tylko z osobną bazą testową:
#   $env:TEST_DATABASE_URL = "postgresql://postgres:HASLO@localhost:5432/agent_testy"
# Bez tej zmiennej pytest je pomija.

import datetime

import psycopg

from app import baza
from app.agent import Przebieg
from app.modele import KryteriaSzukania

from .conftest import SWIEZA, UdawanyClaude

JA, OBCY = "uzytkownik-ja", "uzytkownik-obcy"


def test_przygotuj_baze_nie_psuje_starej_tabeli(baza_url):
    # moja pierwsza tabela "oferty" (sprzed agenta) - nowe kolumny mają dojść, stare wiersze zostać
    with psycopg.connect(baza_url, autocommit=True) as c:
        c.execute("""CREATE TABLE oferty (id SERIAL PRIMARY KEY, stanowisko TEXT NOT NULL, firma TEXT, miasto TEXT,
            wynagrodzenie TEXT, poziom TEXT CHECK (poziom IN ('staż','junior','mid','senior','nieznany')),
            link TEXT UNIQUE NOT NULL, pasuje TEXT CHECK (pasuje IN ('PASUJE','NIE PASUJE','DO SPRAWDZENIA')),
            data_dodania TIMESTAMP DEFAULT NOW())""")
        c.execute("INSERT INTO oferty (stanowisko, link) VALUES ('Stara oferta', 'https://stara/1')")
    baza.przygotuj_baze(baza_url, wlasciciel=JA)
    baza.przygotuj_baze(baza_url, wlasciciel=OBCY)   # drugi raz bez błędu, a stare wiersze zostają u pierwszego
    with psycopg.connect(baza_url) as c:
        kolumny = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'oferty'")}
        stara = c.execute("SELECT stanowisko, uzytkownik FROM oferty WHERE link = 'https://stara/1'").fetchone()
        # ten sam link u innej osoby jest teraz dozwolony (wcześniej link był UNIQUE w całej tabeli)
        c.execute("INSERT INTO oferty (stanowisko, link, uzytkownik) VALUES ('Stara oferta', 'https://stara/1', %s)", (OBCY,))
    assert {"wymagania", "spelnione_wymagania", "brakujace_wymagania", "data_publikacji", "aplikowano", "szukanie_id",
            "uzytkownik"} <= kolumny
    assert stara == ("Stara oferta", JA)          # moje stare oferty przypisane do mnie
    assert [o["stanowisko"] for o in baza.lista_ofert(baza_url, OBCY)] == ["Stara oferta"]


def test_zapis_wynikow_bez_duplikatow(baza_url, oferty_testowe):
    baza.przygotuj_baze(baza_url)
    kryteria = KryteriaSzukania(stanowisko="junior Python", kwalifikacje="Python, Git")
    sid = baza.nowe_szukanie(baza_url, kryteria, JA)
    p = Przebieg(UdawanyClaude(oferty_testowe).klient(), kryteria)
    p.uruchom()
    nowe, juz_byly = baza.zapisz_wyniki(baza_url, p, sid, JA)
    baza.aktualizuj_szukanie(baza_url, sid, p)
    assert (nowe, juz_byly) == (2, 0)
    assert baza.zapisz_wyniki(baza_url, p, sid, JA) == (0, 2)      # ON CONFLICT (uzytkownik, link) DO NOTHING
    assert baza.zapisz_wyniki(baza_url, p, None, OBCY) == (2, 0)   # u kogoś innego to nowe oferty

    a = next(o for o in baza.lista_ofert(baza_url, JA) if o["firma"] == "Firma A")
    assert a["wymagania"] == ["Python", "Git", "Docker"] and a["brakujace_wymagania"] == ["Docker"]   # lista -> TEXT[]
    assert a["data_publikacji"] == datetime.date.fromisoformat(SWIEZA) and a["data_dodania"] is not None

    s = baza.jedno_szukanie(baza_url, JA, sid)
    assert s["status"] == "gotowe" and s["liczba_ofert"] == 2 and s["zakonczono"] is not None
    assert baza.jedno_szukanie(baza_url, OBCY, sid) is None
    assert baza.szukania_w_ciagu_doby(baza_url, JA) == 1 and baza.szukania_w_ciagu_doby(baza_url, OBCY) == 0

    # widziane = dobre + odrzucone, żeby agent nie płacił drugi raz za te same linki
    assert len(baza.widziane_linki(baza_url, JA)) == 5
    assert len(baza.widziane_linki(baza_url, "ktos-nowy")) == 0


def test_filtry_i_wyslane_cv(baza_url, oferty_testowe):
    baza.przygotuj_baze(baza_url)
    kryteria = KryteriaSzukania(stanowisko="junior Python")
    sid = baza.nowe_szukanie(baza_url, kryteria, JA)
    p = Przebieg(UdawanyClaude(oferty_testowe).klient(), kryteria)
    p.uruchom()
    baza.zapisz_wyniki(baza_url, p, sid, JA)

    oid = baza.lista_ofert(baza_url, JA)[0]["id"]
    assert baza.oznacz_aplikowano(baza_url, OBCY, oid) is None                     # cudzej nie zmienię
    pierwsza = baza.oznacz_aplikowano(baza_url, JA, oid)["aplikowano"]
    assert pierwsza is not None
    assert baza.oznacz_aplikowano(baza_url, JA, oid)["aplikowano"] == pierwsza       # drugi klik nie zmienia daty
    assert [o["id"] for o in baza.lista_ofert(baza_url, JA, aplikowano=True)] == [oid]
    assert baza.oznacz_aplikowano(baza_url, JA, oid, False)["aplikowano"] is None
    assert baza.oznacz_aplikowano(baza_url, JA, 999_999) is None
    assert baza.lista_ofert(baza_url, JA, pasuje="NIE PASUJE") == []
    assert len(baza.lista_ofert(baza_url, JA, szukanie_id=sid, limit=1)) == 1
    assert baza.jedna_oferta(baza_url, OBCY, oid) is None
