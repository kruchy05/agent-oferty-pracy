# Automatyczne szukanie: wątek w tle co minutę sprawdza w bazie, czy komuś nie przyszła pora
# (np. "co 2 dni o 7:00"), uruchamia szukanie jego zapisanym kluczem i wysyła powiadomienie o nowych ofertach.
# Działa w procesie serwera (uvicorn), więc szukanie odbywa się tylko wtedy, gdy serwer jest włączony.

import threading

import anthropic

from . import baza, powiadomienia, zadania
from .agent import Przebieg
from .config import ustawienia
from .modele import KryteriaSzukania
from .tozsamosc import odszyfruj

_stop = threading.Event()


def nowy_klient(klucz):
    # osobna funkcja, żeby w testach podmienić ją na udawanego Claude
    return anthropic.Anthropic(api_key=klucz, max_retries=5)


def tyknij(url):
    """Jedno sprawdzenie: uruchamia co najwyżej jedno automatyczne szukanie. Zwraca id szukania albo None."""
    if zadania.ile_trwa() >= ustawienia.api_maks_szukan:
        return None   # serwer zajęty - nic nie przejmuję, spróbuję za minutę
    a = baza.przejmij_automat(url, ustawienia.strefa_czasowa)
    if a is None:
        return None
    uzytkownik = a["uzytkownik"]

    klucz = odszyfruj(a["klucz_zaszyfrowany"])
    if klucz is None:
        # np. zmieniony sekret serwera - bez klucza nic nie zrobię, wyłączam i daję znać
        baza.wynik_automatu(url, uzytkownik, "Nie mogę odczytać zapisanego klucza - włącz automat od nowa.", wylacz=True)
        powiadomienia.powiadom(url, uzytkownik, "Automatyczne szukanie wyłączone",
                               "Nie mogę odczytać Twojego klucza API. Włącz automat od nowa na stronie.")
        return None

    zeton, _ = zadania.zajmij_miejsce(ustawienia.api_maks_szukan, uzytkownik)
    if zeton is None:   # ta osoba właśnie szuka ręcznie albo serwer się zapełnił - cofam i próbuję później
        baza.oddaj_automat(url, uzytkownik, a["poprzednio"])
        return None
    try:
        kryteria = KryteriaSzukania(**a["kryteria"])
        szukanie_id = baza.nowe_szukanie(url, kryteria, uzytkownik, automatyczne=True)
        przebieg = Przebieg(nowy_klient(klucz), kryteria, widziane=baza.widziane_linki(url, uzytkownik),
                            na_postep=lambda p: baza.aktualizuj_szukanie(url, szukanie_id, p))
    except BaseException:
        zadania.zwolnij(zeton)
        raise
    zadania.dodaj(zeton, szukanie_id, uzytkownik, przebieg)

    def po_koncu(p, nowe):
        if p.status == "blad" and "AuthenticationError" in (p.blad or ""):
            baza.wynik_automatu(url, uzytkownik, "Klucz API przestał działać - automat wyłączony.", wylacz=True)
            powiadomienia.powiadom(url, uzytkownik, "Automatyczne szukanie wyłączone",
                                   "Twój klucz API przestał działać. Wpisz nowy i włącz automat od nowa.")
            return
        opis = f"{nowe} nowych ofert, koszt {p.licznik.usd:.3f} $" + (f" ({p.status})" if p.status != "gotowe" else "")
        baza.wynik_automatu(url, uzytkownik, opis)
        if nowe:   # bez nowych ofert nie zawracam głowy
            gdzie = f"{kryteria.stanowisko}" + (f", {kryteria.miasto}" if kryteria.miasto else "")
            powiadomienia.powiadom(url, uzytkownik, f"Nowe oferty: {nowe}", gdzie, f"/?szukanie={szukanie_id}")

    # osobny wątek - szukanie trwa minuty, a harmonogram ma dalej sprawdzać innych
    threading.Thread(target=zadania.wykonaj, args=(url, szukanie_id, uzytkownik, przebieg, po_koncu),
                     daemon=True, name=f"automat-{szukanie_id}").start()
    return szukanie_id


def _petla(url):
    while not _stop.wait(ustawienia.harmonogram_co_sekund):
        try:
            while tyknij(url):   # kilka osób na raz - biorę po kolei, dopóki jest miejsce
                pass
        except Exception as e:   # błąd (np. baza chwilowo padła) nie może zabić harmonogramu
            print(f"[harmonogram] {type(e).__name__}: {e}")


def uruchom(url):
    _stop.clear()
    threading.Thread(target=_petla, args=(url,), daemon=True, name="harmonogram").start()


def zatrzymaj():
    _stop.set()
