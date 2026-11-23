# Ustawienia serwera / programu. Liczby i klucz można nadpisać w pliku .env albo zmiennymi środowiskowymi
# (zmienne środowiskowe wygrywają z .env - tak działa pydantic-settings).
# Kryteria wyszukiwania (stanowisko, kwalifikacje...) NIE są tutaj - przychodzą z formularza albo z konsoli.

from pathlib import Path
from typing import Literal, Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# folder projektu (ten nad app/) - od niego liczę ścieżki, żeby nie zależały od tego, skąd uruchamiam
KATALOG_PROJEKTU = Path(__file__).resolve().parent.parent
PLIK_WIDZIANE = KATALOG_PROJEKTU / "widziane_oferty.json"
KATALOG_DANYCH = KATALOG_PROJEKTU / "dane"   # sekrety serwera tworzone przy pierwszym starcie (nie idą do gita)


class Ustawienia(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=KATALOG_PROJEKTU / ".env",
        env_file_encoding="utf-8",
        extra="ignore",   # inne zmienne w .env (np. dane bazy) nie wywalają startu
    )

    # klucz tylko do uruchamiania z konsoli - na stronie każdy wkleja swój
    # SecretStr - przy przypadkowym print() pokaże ********** zamiast klucza
    anthropic_api_key: Optional[SecretStr] = None

    # baza Postgres, np. postgresql://postgres:HASLO@localhost:5432/postgres - hasło jest w nim, więc SecretStr.
    # brak = wyniki tylko do plików, a widziane linki w widziane_oferty.json (jak wcześniej)
    database_url: Optional[SecretStr] = None

    claude_model: str = "claude-haiku-5-5"   # haiku jest dużo tańszy, sonnet dokładniejszy
    min_ofert: int = 10                      # szukam, aż mam tyle dobrych ofert
    liczba_ofert: int = 20                   # więcej niż tyle nie oddaję w wynikach
    maks_wiek_dni: int = 60                  # tylko oferty z ostatnich 2 miesięcy

    # kontrola kosztów
    budzet_max_usd: float = 1.0              # twardy sufit - nawet jak ktoś wpisze więcej w formularzu
    max_rund: int = 3
    max_prob_w_rundzie: int = 10
    max_wyszukiwan: int = 2                  # wyszukiwanie kosztuje 0,01 $, więc szukam mało, a więcej czytam
    max_pobran_w_rundzie: int = 6
    max_stron_startowych: int = 4            # listy ofert pobierane przez Pythona przed pytaniem modelu (0 = wyłącz)
    max_znakow_strony: int = 12_000          # ok. 3-4 tys. tokenów na stronę, żeby nie przebić progu 100k

    # wyszukiwarka z dynamic filtering (web_search_20260318) - model filtruje wyniki kodem, mniej tokenów.
    # "auto" = sprawdź w Models API, czy model to obsługuje; "tak" / "nie" = wymuś
    web_search_dynamiczne: Literal["auto", "tak", "nie"] = "auto"

    # API (FastAPI)
    api_maks_szukan: int = 2                 # ile płatnych szukań może iść naraz - kolejne dostaną 429
    # False (domyślnie, bezpiecznie) = serwer NIGDY nie używa swojego klucza; każdy podaje własny.
    # True tylko lokalnie dla wygody: przy pustym polu API weźmie MÓJ klucz z .env.
    api_klucz_serwera: bool = False
    api_limit_dzienny: int = 10              # ile szukań na dobę może zrobić jedna osoba (jeden klucz)

    # tajny klucz serwera - z niego liczę identyfikator użytkownika z klucza API (HMAC).
    # Brak = generuję go sam do dane/sekret.key. Zmiana albo utrata = wszyscy "tracą" swoje oferty!
    sekret_serwera: Optional[SecretStr] = None

    # automatyczne szukanie (co X dni o danej godzinie) i powiadomienia push z PWA
    harmonogram_wlaczony: bool = True        # wątek, który sprawdza, czy komuś nie przyszła pora na szukanie
    harmonogram_co_sekund: int = 60
    strefa_czasowa: str = "Europe/Warsaw"    # w tej strefie liczę "o 7:00"
    # klucze VAPID - serwer podpisuje nimi powiadomienia. Brak = generuję sam do dane/vapid.json
    vapid_prywatny: Optional[SecretStr] = None
    vapid_publiczny: Optional[str] = None
    # kontakt do mnie dla usług push (Google, Apple, Mozilla) - na serwerze wpisać prawdziwy mail
    vapid_kontakt: str = "mailto:agent-ofert@example.com"

    # obecne modele nie używają temperatury - ustaw tylko dla starszego modelu
    temperatura_szukanie: Optional[float] = None
    temperatura_porzadkowanie: Optional[float] = None


ustawienia = Ustawienia()
