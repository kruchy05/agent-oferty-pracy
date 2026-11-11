# Liczenie kosztów z pola "usage" i pilnowanie budżetu.
# Każde wyszukiwanie ma WŁASNY licznik - wcześniej był jeden globalny i drugie wyszukiwanie
# w tym samym procesie (np. z FastAPI) startowało z "zużytym" budżetem.

# ceny za 1 mln tokenów (dokumentacja Anthropic, listopad 2026)
# haiku liczy całe zapytanie drożej, jeśli prompt ma ponad 100 tys. tokenów
CENY = {
    "claude-haiku-5-5": {"wejscie": 0.10, "wyjscie": 0.50,
                         "wejscie_dlugie": 0.50, "wyjscie_dlugie": 2.50, "prog": 100_000},
    "claude-sonnet-5-5": {"wejscie": 2.00, "wyjscie": 10.00, "mnoznik_odczytu_cache": 0.05},
}
CENA_WYSZUKIWANIA = 0.01

# cache promptu (dokumentacja: prompt caching): zapis na 5 min = 1,25x ceny wejścia,
# odczyt = 0,1x (sonnet/opus 5.5 mają 0,05x - stąd "mnoznik_odczytu_cache" w CENY)
MNOZNIK_ZAPISU_CACHE = 1.25
MNOZNIK_ODCZYTU_CACHE = 0.10


class BudzetWyczerpany(Exception):
    pass


class Licznik:
    def __init__(self, model, budzet_usd):
        self.model = model
        self.budzet_usd = budzet_usd
        self.wejscie = 0          # wszystkie tokeny wejściowe (zwykłe + cache)
        self.zapis_cache = 0
        self.odczyt_cache = 0
        self.wyjscie = 0
        self.wyszukiwania = 0
        self.zapytania = 0
        self.pobrania = 0
        self.usd = 0.0

    def cena(self):
        # nieznany model = liczę jak sonnet, czyli drożej, na wszelki wypadek
        return CENY.get(self.model, CENY["claude-sonnet-5-5"])

    def licz(self, usage):
        # input_tokens to tylko część NIE z cache - całość = input + zapis do cache + odczyt z cache
        zwykle = usage.input_tokens or 0
        zapis = getattr(usage, "cache_creation_input_tokens", 0) or 0
        odczyt = getattr(usage, "cache_read_input_tokens", 0) or 0
        wyjscie = usage.output_tokens or 0
        narzedzia = getattr(usage, "server_tool_use", None)
        wyszukiwania = (getattr(narzedzia, "web_search_requests", 0) or 0) if narzedzia else 0
        wejscie = zwykle + zapis + odczyt

        cena = self.cena()
        if "prog" in cena and wejscie > cena["prog"]:
            print(f"  [UWAGA: zapytanie miało {wejscie} tokenów - powyżej progu, liczone drożej]")
            cena_we, cena_wy = cena["wejscie_dlugie"], cena["wyjscie_dlugie"]
        else:
            cena_we, cena_wy = cena["wejscie"], cena["wyjscie"]
        mnoznik_odczytu = cena.get("mnoznik_odczytu_cache", MNOZNIK_ODCZYTU_CACHE)

        self.wejscie += wejscie
        self.zapis_cache += zapis
        self.odczyt_cache += odczyt
        self.wyjscie += wyjscie
        self.wyszukiwania += wyszukiwania
        self.zapytania += 1
        self.usd += (zwykle * cena_we
                     + zapis * cena_we * MNOZNIK_ZAPISU_CACHE
                     + odczyt * cena_we * mnoznik_odczytu
                     + wyjscie * cena_wy) / 1_000_000 + wyszukiwania * CENA_WYSZUKIWANIA

    def dolicz_szacunek(self, max_tokens_wyjscia):
        # gdy odpowiedzi nie da się odczytać, nie znam usage - doliczam koszt "na zapas"
        self.zapytania += 1
        self.usd += max_tokens_wyjscia * self.cena()["wyjscie"] / 1_000_000

    def pilnuj(self):
        # sprawdzam przed każdym wywołaniem API. kosztu wywołania nie znam z góry, więc limit
        # może zostać przekroczony najwyżej o jedno wywołanie (zwykle ok. 0,01 $)
        if self.usd >= self.budzet_usd:
            raise BudzetWyczerpany(f"wydano {self.usd:.4f} $ z limitu {self.budzet_usd} $")

    def podsumowanie(self, liczba_ofert):
        return [
            ("Liczba ofert", liczba_ofert),
            ("Liczba prób (zapytań do API)", self.zapytania),
            ("Koszt (USD)", f"{self.usd:.4f} $"),
            ("Limit budżetu (USD)", f"{self.budzet_usd:.2f} $"),
            ("Koszt na jedną ofertę (USD)", f"{self.usd / liczba_ofert:.4f} $" if liczba_ofert else "-"),
            ("Tokeny wejściowe (razem)", self.wejscie),
            ("  w tym odczyt z cache", self.odczyt_cache),
            ("  w tym zapis do cache", self.zapis_cache),
            ("Tokeny wyjściowe", self.wyjscie),
            ("Wyszukiwania w sieci", self.wyszukiwania),
            ("Pobrane strony", self.pobrania),
            ("Model", self.model),
        ]
