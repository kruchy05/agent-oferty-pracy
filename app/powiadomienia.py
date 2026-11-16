# Powiadomienia push (Web Push) - działają w PWA na telefonie i w przeglądarce na komputerze.
# Przeglądarka daje "subskrypcję" (adres usługi push Google/Apple/Mozilla + klucze szyfrowania),
# a serwer wysyła tam zaszyfrowaną wiadomość podpisaną kluczem VAPID. Usługi push są darmowe.
# Moduł jest wymienny: kiedyś mogą tu dojść inne kanały (Telegram, WhatsApp) z tą samą funkcją powiadom().

import base64
import json
import threading

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from . import baza
from .config import KATALOG_DANYCH, ustawienia

PLIK_VAPID = KATALOG_DANYCH / "vapid.json"
_blokada = threading.Lock()
_klucze = None


def _b64url(dane):
    return base64.urlsafe_b64encode(dane).rstrip(b"=").decode()


def nowe_klucze_vapid():
    """Para kluczy EC P-256: prywatny (32 bajty) do podpisu, publiczny (65 bajtów) dla przeglądarki."""
    klucz = ec.generate_private_key(ec.SECP256R1())
    prywatny = klucz.private_numbers().private_value.to_bytes(32, "big")
    publiczny = klucz.public_key().public_bytes(serialization.Encoding.X962,
                                                serialization.PublicFormat.UncompressedPoint)
    return {"prywatny": _b64url(prywatny), "publiczny": _b64url(publiczny)}


def klucze_vapid():
    """Z .env (VAPID_PRYWATNY / VAPID_PUBLICZNY) albo z dane/vapid.json - tworzone przy pierwszym użyciu.
    Zmiana kluczy = wszystkie subskrypcje przestają działać (ludzie muszą włączyć powiadomienia od nowa)."""
    global _klucze
    if ustawienia.vapid_prywatny and ustawienia.vapid_publiczny:
        return {"prywatny": ustawienia.vapid_prywatny.get_secret_value(), "publiczny": ustawienia.vapid_publiczny}
    with _blokada:
        if _klucze is None:
            if not PLIK_VAPID.exists():
                KATALOG_DANYCH.mkdir(exist_ok=True)
                PLIK_VAPID.write_text(json.dumps(nowe_klucze_vapid()), encoding="utf-8")
            _klucze = json.loads(PLIK_VAPID.read_text(encoding="utf-8"))
        return _klucze


def wyslij(subskrypcja, wiadomosc):
    """Wysyła jedno powiadomienie. Zwraca True / False, albo "wygasla", gdy przeglądarka zrezygnowała (404/410)."""
    from pywebpush import WebPushException, webpush   # import tu - bez powiadomień reszta działa i bez tej biblioteki
    try:
        webpush(subscription_info=subskrypcja, data=json.dumps(wiadomosc, ensure_ascii=False),
                vapid_private_key=klucze_vapid()["prywatny"], vapid_claims={"sub": ustawienia.vapid_kontakt},
                ttl=24 * 3600, timeout=10)   # ttl: telefon offline - usługa push trzyma wiadomość do doby
        return True
    except WebPushException as e:
        kod = getattr(e.response, "status_code", None)
        if kod in (404, 410):
            return "wygasla"
        print(f"  [push: błąd {kod}: {str(e)[:200]}]")
        return False


def powiadom(url_bazy, uzytkownik, tytul, tresc, adres="/"):
    """Wysyła powiadomienie na wszystkie urządzenia użytkownika. Zwraca, na ile się udało."""
    wiadomosc = {"tytul": tytul, "tresc": tresc, "adres": adres}
    udane = 0
    for subskrypcja in baza.subskrypcje(url_bazy, uzytkownik):
        wynik = wyslij(subskrypcja, wiadomosc)
        if wynik == "wygasla":
            baza.usun_subskrypcje(url_bazy, subskrypcja["endpoint"])   # sprzątam martwe subskrypcje
        elif wynik:
            udane += 1
    return udane
