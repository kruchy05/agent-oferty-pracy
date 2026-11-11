# Modele danych (Pydantic).
#   KryteriaSzukania - to, co użytkownik wpisuje w formularzu (albo podaje w konsoli)
#   Oferta / ListaOfert - to, co agent oddaje (structured outputs z messages.parse)

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .portale import KATEGORIE

Doswiadczenie = Literal["bez_doswiadczenia", "do_2_lat", "2_5_lat", "5_plus"]

# jakie poziomy ofert pasują do doświadczenia kandydata
DOZWOLONE_POZIOMY = {
    "bez_doswiadczenia": {"staż", "junior"},
    "do_2_lat": {"staż", "junior"},
    "2_5_lat": {"junior", "mid"},
    "5_plus": {"mid", "senior"},
}

OPIS_DOSWIADCZENIA = {
    "bez_doswiadczenia": "bez doświadczenia zawodowego",
    "do_2_lat": "do 2 lat doświadczenia",
    "2_5_lat": "2-5 lat doświadczenia",
    "5_plus": "ponad 5 lat doświadczenia",
}


class KryteriaSzukania(BaseModel):
    # limity długości, bo każdy znak z formularza to tokeny w KAŻDYM wywołaniu API
    kategoria: Literal[tuple(KATEGORIE)] = "it"
    stanowisko: str = Field(min_length=2, max_length=200, description="Np. 'magazynier', 'junior Python'")
    kwalifikacje: str = Field(default="", max_length=1000, description="Umiejętności, uprawnienia, języki, certyfikaty")
    doswiadczenie: Doswiadczenie = "bez_doswiadczenia"
    miasto: str = Field(default="", max_length=100, description="Puste = cała Polska")
    zdalnie: bool = True
    budzet_usd: float = Field(default=0.15, gt=0, le=1.0)   # twardy limit na jedno wyszukiwanie


class Oferta(BaseModel):
    link: str = Field(description="Pełny adres konkretnej oferty (nie listy ofert)")
    stanowisko: str
    firma: str
    miasto: str = Field(description='Miasto albo "zdalnie"')
    wynagrodzenie: Optional[str] = Field(default=None, description="Np. '6000-8000 zł brutto (UoP)'; null, jeśli brak")
    poziom: Literal["staż", "junior", "mid", "senior", "nieznany"]
    wymagania: list[str] = Field(description="Najważniejsze wymagania, krótko, po polsku")
    spelnione_wymagania: list[str] = Field(description="Wymagania, które kandydat spełnia")
    brakujace_wymagania: list[str] = Field(description="Wymagania, których kandydatowi brakuje")
    data_dodania: Optional[str] = Field(default=None, description="Data publikacji RRRR-MM-DD; null, jeśli nieznana")
    pasuje: Literal["PASUJE", "NIE PASUJE", "DO SPRAWDZENIA"]


class ListaOfert(BaseModel):
    oferty: list[Oferta]
