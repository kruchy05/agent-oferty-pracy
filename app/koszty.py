# Liczenie kosztów z pola "usage" i pilnowanie budżetu.

from .config import ustawienia

# ceny za 1 mln tokenów (dokumentacja Anthropic, listopad 2026)
# haiku liczy całe zapytanie drożej, jeśli prompt ma ponad 100 tys. tokenów
CENY = {
    "claude-haiku-5-5": {"wejscie": 0.10, "wyjscie": 0.50,
                         "wejscie_dlugie": 0.50, "wyjscie_dlugie": 2.50, "prog": 100_000},
    "claude-sonnet-5-5": {"wejscie": 2.00, "wyjscie": 10.00},
}
CENA_WYSZUKIWANIA = 0.01


class BudzetWyczerpany(Exception):
    pass


LICZNIK = {"wejscie": 0, "wyjscie": 0, "wyszukiwania": 0, "zapytania": 0, "pobrania": 0, "usd": 0.0}


def cena_modelu():
    # nieznany model = liczę jak sonnet, czyli drożej, na wszelki wypadek
    return CENY.get(ustawienia.claude_model, CENY["claude-sonnet-5-5"])


def licz_koszt(usage):
    # "usage" z każdej odpowiedzi: input = co model przeczytał, output = co napisał
    wejscie = ((usage.input_tokens or 0)
               + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
               + (getattr(usage, "cache_read_input_tokens", 0) or 0))
    wyjscie = usage.output_tokens or 0
    narzedzia = getattr(usage, "server_tool_use", None)
    wyszukiwania = (getattr(narzedzia, "web_search_requests", 0) or 0) if narzedzia else 0

    cena = cena_modelu()
    if "prog" in cena and wejscie > cena["prog"]:
        print(f"  [UWAGA: zapytanie miało {wejscie} tokenów - powyżej progu, liczone drożej]")
        cena_we, cena_wy = cena["wejscie_dlugie"], cena["wyjscie_dlugie"]
    else:
        cena_we, cena_wy = cena["wejscie"], cena["wyjscie"]

    LICZNIK["wejscie"] += wejscie
    LICZNIK["wyjscie"] += wyjscie
    LICZNIK["wyszukiwania"] += wyszukiwania
    LICZNIK["zapytania"] += 1
    LICZNIK["usd"] += (wejscie * cena_we + wyjscie * cena_wy) / 1_000_000 + wyszukiwania * CENA_WYSZUKIWANIA


def pilnuj_budzetu():
    # sprawdzam przed każdym wywołaniem API. kosztu wywołania nie znam z góry, więc limit
    # może zostać przekroczony najwyżej o jedno wywołanie (zwykle ok. 0,01 $)
    if LICZNIK["usd"] >= ustawienia.budzet_usd:
        raise BudzetWyczerpany(f"wydano {LICZNIK['usd']:.4f} $ z limitu {ustawienia.budzet_usd} $")


def podsumowanie(liczba_ofert):
    razem = LICZNIK["wejscie"] + LICZNIK["wyjscie"]
    return [
        ("Liczba ofert", liczba_ofert),
        ("Liczba prób (zapytań do API)", LICZNIK["zapytania"]),
        ("Koszt (USD)", f"{LICZNIK['usd']:.4f} $"),
        ("Limit budżetu (USD)", f"{ustawienia.budzet_usd:.2f} $"),
        ("Koszt na jedną ofertę (USD)", f"{LICZNIK['usd'] / liczba_ofert:.4f} $" if liczba_ofert else "-"),
        ("Tokeny wejściowe", LICZNIK["wejscie"]),
        ("Tokeny wyjściowe", LICZNIK["wyjscie"]),
        ("Tokeny RAZEM", razem),
        ("Wyszukiwania w sieci", LICZNIK["wyszukiwania"]),
        ("Pobrane strony", LICZNIK["pobrania"]),
        ("Model", ustawienia.claude_model),
    ]
