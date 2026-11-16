# Kto jest kim - bez kont i haseł. Użytkownika rozpoznaję po jego kluczu Anthropic API:
# identyfikator = HMAC-SHA256(sekret serwera, klucz API).
#
# Dlaczego HMAC, a nie zwykły hash (np. sha256 klucza):
# - samego klucza API nie ma w bazie, a z identyfikatora nie da się go odtworzyć
# - bez sekretu serwera nikt (np. po wycieku bazy) nie sprawdzi, czy dany klucz pasuje do danych w bazie
# Minus tego podejścia: po zmianie klucza API ktoś zaczyna od pustej listy ofert.

import base64
import hashlib
import hmac
import secrets
import threading

from cryptography.fernet import Fernet, InvalidToken

from .config import KATALOG_DANYCH, ustawienia

PLIK_SEKRETU = KATALOG_DANYCH / "sekret.key"
_blokada = threading.Lock()
_sekret = None


def sekret_serwera():
    """Sekret z .env (SEKRET_SERWERA) albo z pliku dane/sekret.key - tworzony przy pierwszym użyciu."""
    global _sekret
    if ustawienia.sekret_serwera is not None:
        return ustawienia.sekret_serwera.get_secret_value().encode()
    with _blokada:
        if _sekret is None:
            if not PLIK_SEKRETU.exists():
                KATALOG_DANYCH.mkdir(exist_ok=True)
                PLIK_SEKRETU.write_text(secrets.token_hex(32), encoding="utf-8")
                print(f"[info] Utworzyłem nowy sekret serwera: {PLIK_SEKRETU} - zrób kopię, bez niego oferty się 'zgubią'")
            _sekret = PLIK_SEKRETU.read_text(encoding="utf-8").strip().encode()
        return _sekret


def id_uzytkownika(klucz_api):
    """32 znaki hex - na stałe przypisane do klucza API (dopóki sekret serwera się nie zmieni)."""
    return hmac.new(sekret_serwera(), klucz_api.strip().encode(), hashlib.sha256).hexdigest()[:32]


# ---------- szyfrowanie kluczy API (tylko dla automatycznego szukania) ----------
# Żeby serwer mógł sam szukać co 2 dni, musi mieć klucz API użytkownika. Trzymam go zaszyfrowanego
# (Fernet = AES + podpis HMAC, z biblioteki cryptography), a klucz do szyfrowania wyprowadzam z sekretu serwera.
# Zapisuję go tylko wtedy, gdy ktoś sam włączy automat i zaznaczy zgodę. Wyłączenie automatu = usunięcie klucza.


def _fernet():
    klucz = hmac.new(sekret_serwera(), b"szyfrowanie-kluczy-api", hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(klucz))


def zaszyfruj(tekst):
    return _fernet().encrypt(tekst.encode())


def odszyfruj(dane):
    """None, gdy się nie da (np. zmieniony sekret serwera) - wtedy automat trzeba włączyć od nowa."""
    try:
        return _fernet().decrypt(bytes(dane)).decode()
    except (InvalidToken, ValueError, TypeError):
        return None
