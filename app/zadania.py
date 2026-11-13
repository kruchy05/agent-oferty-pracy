# Szukania, które właśnie trwają (w pamięci serwera). Strona co kilka sekund pyta o postęp,
# a ja oddaję go prosto z obiektu Przebieg - bez czekania na zapis do bazy.
# Po zakończeniu wszystko jest w bazie, więc z pamięci szukanie znika.
# Klucz API użytkownika żyje tylko w kliencie Anthropic w środku Przebiegu - nigdzie go nie zapisuję.

import threading

from . import baza

_trwajace = {}              # id szukania -> Przebieg
_blokada = threading.Lock()  # endpointy i zadania w tle działają w różnych wątkach


def zajmij_miejsce(maks_naraz):
    """Rezerwuje miejsce na płatne szukanie i zwraca żeton (albo None, gdy limit zajęty).
    Rezerwuję od razu, żeby dwa zapytania w tej samej chwili nie przeszły obok limitu."""
    with _blokada:
        if len(_trwajace) >= maks_naraz:
            return None
        zeton = object()
        _trwajace[zeton] = None
        return zeton


def zwolnij(zeton):
    """Gdy coś poszło nie tak przed startem szukania (np. zły klucz)."""
    with _blokada:
        _trwajace.pop(zeton, None)


def dodaj(zeton, szukanie_id, przebieg):
    """Zamienia rezerwację na prawdziwe szukanie."""
    with _blokada:
        _trwajace.pop(zeton, None)
        _trwajace[szukanie_id] = przebieg


def trwajace(szukanie_id):
    with _blokada:
        return _trwajace.get(szukanie_id)


def ile_trwa():
    with _blokada:
        return len(_trwajace)


def wykonaj(url_bazy, szukanie_id, przebieg):
    """Uruchamiane w tle przez FastAPI (BackgroundTasks). Przebieg.uruchom() nie rzuca wyjątków."""
    try:
        przebieg.uruchom()
        try:
            baza.zapisz_wyniki(url_bazy, przebieg, szukanie_id)
        except Exception as e:
            # oferty zostają w Przebiegu, ale bez bazy ich nie zobaczę - zapisuję chociaż błąd
            przebieg.status, przebieg.blad = "blad", f"Zapis do bazy: {type(e).__name__}: {e}"
        try:
            baza.aktualizuj_szukanie(url_bazy, szukanie_id, przebieg)
        except Exception as e:
            print(f"  [nie udało się zapisać statusu szukania {szukanie_id}: {type(e).__name__}: {e}]")
    finally:
        with _blokada:
            _trwajace.pop(szukanie_id, None)
        przebieg.klient = None   # klient z kluczem użytkownika nie jest już potrzebny
