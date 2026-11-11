# Uruchomienie agenta z konsoli.
#   python szukaj.py                                   -> moje domyślne kryteria (junior IT, Legnica/Wrocław)
#   python szukaj.py --kategoria logistyka --stanowisko magazynier --miasto Legnica \
#                    --kwalifikacje "prawo jazdy B, wózek widłowy UDT" --doswiadczenie do_2_lat
#   python szukaj.py --pomoc-kategorie                 -> lista kategorii
# Klucz API bierze z .env albo ze zmiennej środowiskowej ANTHROPIC_API_KEY.

import argparse
import datetime
import json
import os
import sys

import anthropic
from pydantic import ValidationError

from app.agent import Przebieg
from app.config import PLIK_WIDZIANE, ustawienia
from app.eksport import oferty_do_slownikow, zapisz_json, zapisz_ods
from app.modele import KryteriaSzukania
from app.portale import KATEGORIE

# moje kryteria - używane, gdy nie podam żadnych argumentów
MOJE_KRYTERIA = {
    "kategoria": "it",
    "stanowisko": "junior Python developer / junior AI engineer / staż IT",
    "kwalifikacje": ("podstawy Pythona (OOP, wyjątki, pliki, JSON, requests), SQL, Git i GitHub, "
                     "Claude API (tool use, structured outputs), Pydantic, FastAPI, Docker - podstawy, "
                     "kilka certyfikatów, angielski komunikatywny"),
    "doswiadczenie": "bez_doswiadczenia",
    "miasto": "Legnica / Wrocław",
    "zdalnie": True,
    "budzet_usd": 0.15,
}


def wczytaj_widziane():
    try:
        with open(PLIK_WIDZIANE, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def zapisz_widziane(linki):
    with open(PLIK_WIDZIANE, "w", encoding="utf-8") as f:
        json.dump(sorted(linki), f, ensure_ascii=False, indent=1)


def kryteria_z_argumentow():
    p = argparse.ArgumentParser(description="Agent AI do szukania ofert pracy")
    p.add_argument("--kategoria", choices=list(KATEGORIE))
    p.add_argument("--stanowisko")
    p.add_argument("--kwalifikacje")
    p.add_argument("--doswiadczenie", choices=["bez_doswiadczenia", "do_2_lat", "2_5_lat", "5_plus"])
    p.add_argument("--miasto", help='np. "Legnica" albo "" dla całej Polski')
    p.add_argument("--bez-zdalnej", action="store_true", help="nie szukaj pracy w pełni zdalnej")
    p.add_argument("--budzet", type=float, help="limit kosztów w $ (maks. 1)")
    p.add_argument("--pomoc-kategorie", action="store_true", help="pokaż listę kategorii")
    a = p.parse_args()

    if a.pomoc_kategorie:
        for klucz, nazwa in KATEGORIE.items():
            print(f"  {klucz:<16} {nazwa}")
        sys.exit(0)

    dane = dict(MOJE_KRYTERIA)
    if a.stanowisko:
        # podane inne stanowisko = nie biorę moich domyślnych kwalifikacji, żeby nie mieszać
        dane.update(kategoria="inna", kwalifikacje="", doswiadczenie="bez_doswiadczenia")
    for pole, wartosc in [("kategoria", a.kategoria), ("stanowisko", a.stanowisko), ("kwalifikacje", a.kwalifikacje),
                          ("doswiadczenie", a.doswiadczenie), ("miasto", a.miasto), ("budzet_usd", a.budzet)]:
        if wartosc is not None:
            dane[pole] = wartosc
    if a.bez_zdalnej:
        dane["zdalnie"] = False

    try:
        return KryteriaSzukania(**dane)
    except ValidationError as e:
        print("Błędne kryteria:")
        for blad in e.errors():
            print(f"  {blad['loc'][0]}: {blad['msg']}")
        sys.exit(1)


def main(klient=None):
    kryteria = kryteria_z_argumentow()

    if klient is None:
        if ustawienia.anthropic_api_key is None:
            print("Brak klucza API. Wpisz ANTHROPIC_API_KEY do pliku .env (wzór w .env.example).")
            sys.exit(1)
        # max_retries = SDK samo ponawia przy 429/529
        klient = anthropic.Anthropic(api_key=ustawienia.anthropic_api_key.get_secret_value(), max_retries=5)

    print(f"Agent szuka: {kryteria.stanowisko} ({KATEGORIE[kryteria.kategoria]}), "
          f"{kryteria.miasto or 'cała Polska'}{' + zdalnie' if kryteria.zdalnie else ''}")
    print(f"Model: {ustawienia.claude_model} | min. {ustawienia.min_ofert} ofert z ostatnich "
          f"{ustawienia.maks_wiek_dni} dni | budżet: {kryteria.budzet_usd} $")

    url_bazy = ustawienia.database_url.get_secret_value() if ustawienia.database_url else None
    szukanie_id, na_postep = None, None
    if url_bazy:
        from app import baza   # psycopg potrzebny tylko, gdy używam bazy
        try:
            baza.przygotuj_baze(url_bazy)
            # stary plik dalej się liczy, żeby po przejściu na bazę nie płacić drugi raz za te same oferty
            widziane = baza.widziane_linki(url_bazy) | wczytaj_widziane()
            szukanie_id = baza.nowe_szukanie(url_bazy, kryteria)
        except Exception as e:
            # sprawdzam bazę PRZED płatnym wyszukiwaniem - lepiej przerwać teraz niż zgubić wyniki
            print(f"Nie mogę połączyć się z bazą: {type(e).__name__}: {e}")
            print("Czy kontener działa? Sprawdź: docker ps   (uruchom: docker start moj-postgres)")
            sys.exit(1)
        na_postep = lambda p: baza.aktualizuj_szukanie(url_bazy, szukanie_id, p)
        print(f"Baza: zapis do Postgresa (wyszukiwanie nr {szukanie_id})")
    else:
        widziane = wczytaj_widziane()
        print("Baza: brak DATABASE_URL w .env - zapis tylko do plików")

    przebieg = Przebieg(klient, kryteria, widziane=widziane, na_postep=na_postep).uruchom()

    if url_bazy:
        try:
            nowe, juz_byly = baza.zapisz_wyniki(url_bazy, przebieg, szukanie_id)
            print(f"\nBaza: zapisano {nowe} nowych ofert" + (f" ({juz_byly} już było w bazie)" if juz_byly else ""))
        except Exception as e:
            # baza padła w trakcie - wyniki i tak trafią niżej do .ods / .json, a linki do pliku
            print(f"\n[Nie udało się zapisać do bazy: {type(e).__name__}: {e}] - zapisuję widziane do pliku")
            zapisz_widziane(przebieg.widziane | przebieg.wszystkie_linki())
    else:
        zapisz_widziane(przebieg.widziane | przebieg.wszystkie_linki())

    wiersze = oferty_do_slownikow(przebieg.wyniki())
    licznik = przebieg.licznik
    plik_ods = None
    if wiersze:
        teraz = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        plik_ods = f"oferty_{teraz}.ods"
        zapisz_ods(wiersze, plik_ods, licznik.podsumowanie(len(wiersze)))
        zapisz_json(wiersze, f"oferty_{teraz}.json")
        print(f"\nZapisano: oferty_{teraz}.ods i oferty_{teraz}.json")

    print("\n===== PODSUMOWANIE =====")
    print(f"Status: {przebieg.status}" + (f" ({przebieg.blad})" if przebieg.blad else ""))
    print(f"Liczba ofert: {len(wiersze)}")
    print(f"Liczba prób (zapytań do API): {licznik.zapytania}")
    print(f"Koszt: {licznik.usd:.4f} $ (limit {licznik.budzet_usd} $)")
    if len(wiersze) < ustawienia.min_ofert:
        print(f"UWAGA: nie udało się zebrać {ustawienia.min_ofert} ofert (zwiększ --budzet albo MAX_RUND w .env).")

    if plik_ods:
        try:
            os.startfile(os.path.abspath(plik_ods))   # tylko Windows
        except AttributeError:
            pass
    return przebieg


if __name__ == "__main__":
    main()
