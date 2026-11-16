# Szukania, które właśnie trwają (w pamięci serwera). Strona co kilka sekund pyta o postęp,
# a ja oddaję go prosto z obiektu Przebieg - bez czekania na zapis do bazy.
# Po zakończeniu wszystko jest w bazie, więc z pamięci szukanie znika.
# Klucz API użytkownika żyje tylko w kliencie Anthropic w środku Przebiegu - tu go nie zapisuję.

import threading

from . import baza

_trwajace = {}              # id szukania (albo żeton rezerwacji) -> (uzytkownik, Przebieg albo None)
_blokada = threading.Lock()  # endpointy, zadania w tle i harmonogram działają w różnych wątkach

ZAJETY_SERWER = "serwer"
ZAJETY_UZYTKOWNIK = "uzytkownik"


def zajmij_miejsce(maks_naraz, uzytkownik):
    """Rezerwuje miejsce na płatne szukanie. Zwraca (żeton, None) albo (None, powód).
    Jedna osoba = jedno szukanie naraz, a cały serwer = maks_naraz.
    Rezerwuję od razu, żeby dwa zapytania w tej samej chwili nie przeszły obok limitu."""
    with _blokada:
        if any(u == uzytkownik for u, _ in _trwajace.values()):
            return None, ZAJETY_UZYTKOWNIK
        if len(_trwajace) >= maks_naraz:
            return None, ZAJETY_SERWER
        zeton = object()
        _trwajace[zeton] = (uzytkownik, None)
        return zeton, None


def zwolnij(zeton):
    """Gdy coś poszło nie tak przed startem szukania (np. zły klucz)."""
    with _blokada:
        _trwajace.pop(zeton, None)


def dodaj(zeton, szukanie_id, uzytkownik, przebieg):
    """Zamienia rezerwację na prawdziwe szukanie."""
    with _blokada:
        _trwajace.pop(zeton, None)
        _trwajace[szukanie_id] = (uzytkownik, przebieg)


def trwajace(szukanie_id, uzytkownik):
    """Trwający Przebieg - tylko jeśli należy do tego użytkownika."""
    with _blokada:
        wlasciciel, przebieg = _trwajace.get(szukanie_id, (None, None))
    return przebieg if wlasciciel == uzytkownik else None


def ile_trwa():
    with _blokada:
        return len(_trwajace)


def wykonaj(url_bazy, szukanie_id, uzytkownik, przebieg, po_koncu=None):
    """Uruchamiane w tle (BackgroundTasks albo harmonogram). Przebieg.uruchom() nie rzuca wyjątków.
    po_koncu(przebieg, nowe) - np. powiadomienie o nowych ofertach."""
    nowe = 0
    try:
        przebieg.uruchom()
        try:
            nowe, _ = baza.zapisz_wyniki(url_bazy, przebieg, szukanie_id, uzytkownik)
        except Exception as e:
            # oferty zostają w Przebiegu, ale bez bazy ich nie zobaczę - zapisuję chociaż błąd
            przebieg.status, przebieg.blad = "blad", f"Zapis do bazy: {type(e).__name__}: {e}"
        try:
            baza.aktualizuj_szukanie(url_bazy, szukanie_id, przebieg)
        except Exception as e:
            print(f"  [nie udało się zapisać statusu szukania {szukanie_id}: {type(e).__name__}: {e}]")
        if po_koncu:
            try:
                po_koncu(przebieg, nowe)
            except Exception as e:
                print(f"  [po zakończeniu szukania {szukanie_id}: {type(e).__name__}: {e}]")
    finally:
        with _blokada:
            _trwajace.pop(szukanie_id, None)
        przebieg.klient = None   # klient z kluczem użytkownika nie jest już potrzebny
    return nowe
