# Uruchomienie agenta z konsoli:  python szukaj.py
# Ustawienia i klucz API bierze z pliku .env (wzór: .env.example) albo ze zmiennych środowiskowych.

import datetime
import os
import sys

import anthropic

from app.agent import DOBRE, ODRZUCONE, uruchom_agenta, wczytaj_widziane, zapisz_widziane
from app.config import ustawienia
from app.eksport import oferty_do_slownikow, zapisz_json, zapisz_ods
from app.koszty import LICZNIK, BudzetWyczerpany


def main(klient=None):
    if klient is None:
        if ustawienia.anthropic_api_key is None:
            print("Brak klucza API. Wpisz ANTHROPIC_API_KEY do pliku .env (wzór w .env.example).")
            sys.exit(1)
        # max_retries = SDK samo ponawia przy 429/529
        klient = anthropic.Anthropic(api_key=ustawienia.anthropic_api_key.get_secret_value(), max_retries=5)

    print("Agent szuka ofert IT dla juniora (Wrocław / Legnica / zdalnie)")
    print(f"Model: {ustawienia.claude_model} | min. {ustawienia.min_ofert} ofert z ostatnich "
          f"{ustawienia.maks_wiek_dni} dni | budżet: {ustawienia.budzet_usd} $")

    try:
        uruchom_agenta(klient)
    except BudzetWyczerpany as e:
        print(f"\n[Koniec budżetu: {e}] - zapisuję to, co mam")
    except Exception as e:
        # raz mi wszystko przepadło przez błąd w połowie, więc teraz zapisuję to, co już mam
        print(f"\n[Przerwano z powodu błędu: {type(e).__name__}: {e}] - zapisuję to, co mam")

    widziane = wczytaj_widziane()
    zapisz_widziane(widziane | {o.link for o, _ in DOBRE + ODRZUCONE})

    wiersze = oferty_do_slownikow(DOBRE[:ustawienia.liczba_ofert])
    if wiersze:
        teraz = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        zapisz_ods(wiersze, f"oferty_{teraz}.ods")
        zapisz_json(wiersze, f"oferty_{teraz}.json")
        print(f"\nZapisano: oferty_{teraz}.ods i oferty_{teraz}.json")

    print("\n===== PODSUMOWANIE =====")
    print(f"Liczba ofert: {len(wiersze)}")
    print(f"Liczba prób (zapytań do API): {LICZNIK['zapytania']}")
    print(f"Koszt: {LICZNIK['usd']:.4f} $ (limit {ustawienia.budzet_usd} $)")
    if len(wiersze) < ustawienia.min_ofert:
        print(f"UWAGA: nie udało się zebrać {ustawienia.min_ofert} ofert (zwiększ MAX_RUND albo BUDZET_USD w .env).")

    if wiersze:
        try:
            os.startfile(os.path.abspath(f"oferty_{teraz}.ods"))   # tylko Windows
        except AttributeError:
            pass


if __name__ == "__main__":
    main()
