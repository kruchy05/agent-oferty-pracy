# Ustawienia agenta. Liczby i klucz można nadpisać w pliku .env albo zmiennymi środowiskowymi
# (zmienne środowiskowe wygrywają z .env - tak działa pydantic-settings).

from pathlib import Path
from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# folder projektu (ten nad app/) - od niego liczę ścieżki, żeby nie zależały od tego, skąd uruchamiam
KATALOG_PROJEKTU = Path(__file__).resolve().parent.parent
PLIK_WIDZIANE = KATALOG_PROJEKTU / "widziane_oferty.json"


class Ustawienia(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=KATALOG_PROJEKTU / ".env",
        env_file_encoding="utf-8",
        extra="ignore",   # inne zmienne w .env (np. dane bazy) nie wywalają startu
    )

    # SecretStr - przy przypadkowym print() pokaże ********** zamiast klucza
    anthropic_api_key: Optional[SecretStr] = None

    claude_model: str = "claude-haiku-5-5"   # haiku jest dużo tańszy, sonnet dokładniejszy
    min_ofert: int = 10                      # szukam, aż mam tyle dobrych ofert
    liczba_ofert: int = 20                   # więcej niż tyle nie wrzucam do tabeli
    maks_wiek_dni: int = 60                  # tylko oferty z ostatnich 2 miesięcy

    # kontrola kosztów
    budzet_usd: float = 0.15                 # twardy limit na jedno uruchomienie
    max_rund: int = 3
    max_prob_w_rundzie: int = 10
    max_wyszukiwan: int = 2                  # wyszukiwanie kosztuje 0,01 $, więc szukam mało, a więcej czytam
    max_pobran_w_rundzie: int = 6
    max_znakow_strony: int = 12_000          # ok. 3-4 tys. tokenów na stronę, żeby nie przebić progu 100k

    # obecne modele nie używają temperatury - ustaw tylko dla starszego modelu
    temperatura_szukanie: Optional[float] = None
    temperatura_porzadkowanie: Optional[float] = None


ustawienia = Ustawienia()


# ---------- KRYTERIA WYSZUKIWANIA (w etapie 2 przejdą do formularza) ----------

PROFIL_KANDYDATA = """Początkujący programista, bez doświadczenia komercyjnego.
Zna podstawy Pythona, ma kilka certyfikatów, robi własne projekty z AI (API modeli językowych, agenci).
Chce rozwijać się w programowaniu i inżynierii AI. Angielski komunikatywny."""

LOKALIZACJE = ("Wrocław, Legnica i okolice (stacjonarnie lub hybrydowo) albo praca w 100% zdalna "
               "(także w firmach zagranicznych, po angielsku)")

SLOWA_KLUCZOWE = [
    "junior Python developer",
    "junior AI engineer",
    "AI / ML intern",
    "stażysta programista",
    "praktyki IT",
    "junior data engineer",
]

# nofluffjobs blokuje wyszukiwarkę Claude, więc go tu nie ma
PORTALE = [
    "justjoin.it",
    "theprotocol.it",
    "rocketjobs.pl",
    "pracuj.pl",
    "pl.indeed.com",
    "olx.pl",
    "linkedin.com",
    "remoteok.com",
    "weworkremotely.com",
    "wellfound.com",
]
LICZBA_GRUP = 3   # każda runda szuka na innej grupie portali
