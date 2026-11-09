# Model danych oferty. Zamiast wycinać JSON z tekstu, mówię API, jaki kształt mają mieć dane,
# i dostaję gotowe obiekty (structured outputs + Pydantic).

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Oferta(BaseModel):
    link: str = Field(description="Pełny adres konkretnej oferty (nie listy ofert)")
    stanowisko: str
    firma: str
    miasto: str = Field(description='Miasto albo "zdalnie"')
    wynagrodzenie: Optional[str] = Field(default=None, description="Np. '6000-8000 zł brutto (UoP)'; null, jeśli brak")
    poziom: Literal["staż", "junior", "mid", "senior", "nieznany"]
    wymagania: list[str] = Field(description="Najważniejsze wymagania, krótko, po polsku")
    data_dodania: Optional[str] = Field(default=None, description="Data publikacji RRRR-MM-DD; null, jeśli nieznana")
    pasuje: Literal["PASUJE", "NIE PASUJE", "DO SPRAWDZENIA"]


class ListaOfert(BaseModel):
    oferty: list[Oferta]
