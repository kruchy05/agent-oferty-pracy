# API agenta (FastAPI). Uruchomienie:  python -m uvicorn app.api:app --reload
# Strona:  http://127.0.0.1:8000/   Dokumentacja z przyciskami do testowania:  http://127.0.0.1:8000/docs
#
# Klucz API użytkownika przychodzi w nagłówku X-Api-Key (nie w adresie - adresy lądują w logach).
# Nie zapisuję go nigdzie: żyje tylko w pamięci, dopóki trwa szukanie.

import datetime
import io
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Optional

import anthropic
import psycopg
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import baza, zadania
from .agent import Przebieg
from .config import KATALOG_PROJEKTU, ustawienia
from .eksport import zapisz_ods
from .modele import KryteriaSzukania
from .portale import KATEGORIE


def url_bazy():
    if ustawienia.database_url is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Brak DATABASE_URL w .env - API potrzebuje bazy.")
    return ustawienia.database_url.get_secret_value()


@asynccontextmanager
async def lifespan(app):
    # przy starcie tworzę brakujące tabele. Jak baza nie działa, serwer i tak wstaje -
    # endpointy oddadzą wtedy 503, zamiast całość wywalać się na starcie
    try:
        baza.przygotuj_baze(url_bazy())
    except Exception as e:
        print(f"[UWAGA] Baza niedostępna przy starcie: {type(e).__name__}: {e}  (docker start moj-postgres)")
    yield


app = FastAPI(title="Agent ofert pracy", version="0.5.0", lifespan=lifespan)


@app.exception_handler(psycopg.OperationalError)
async def baza_niedostepna(request: Request, exc: psycopg.OperationalError):
    # np. wyłączony kontener z Postgresem - zamiast 500 jasna informacja
    return JSONResponse(status_code=503, content={"detail": "Baza danych niedostępna. Czy działa kontener z Postgresem?"})


# ---------- modele odpowiedzi ----------

class OfertaZBazy(BaseModel):
    id: int
    stanowisko: str
    firma: Optional[str]
    miasto: Optional[str]
    wynagrodzenie: Optional[str]
    poziom: Optional[str]
    link: str
    pasuje: Optional[str]
    wymagania: Optional[list[str]]
    spelnione_wymagania: Optional[list[str]]
    brakujace_wymagania: Optional[list[str]]
    data_publikacji: Optional[datetime.date]   # data z ogłoszenia
    data_dodania: Optional[datetime.datetime]  # kiedy trafiła do mojej bazy
    status: Optional[str]
    aplikowano: Optional[datetime.datetime]    # kiedy ręcznie wysłałem CV
    szukanie_id: Optional[int]


class StatusSzukania(BaseModel):
    id: int
    status: str            # trwa / gotowe / budzet / blad
    runda: int
    liczba_ofert: int
    koszt_usd: float
    budzet_usd: Optional[float]
    wyszukiwania: int
    pobrania: int
    blad: Optional[str]
    na_zywo: bool          # True = dane prosto z trwającego szukania, False = z bazy


class Przyjete(BaseModel):
    id: int
    status: str
    adres_statusu: str


# ---------- klucz API ----------

def nowy_klient(klucz):
    # osobna funkcja, żeby w testach podmienić ją na udawanego klienta
    return anthropic.Anthropic(api_key=klucz, max_retries=5)


def klucz_uzytkownika(x_api_key):
    if x_api_key:
        klucz = x_api_key.strip()
        # tylko sprawdzenie formatu - czy klucz działa, sprawdzam niżej w Models API
        if not klucz.startswith("sk-ant-") or len(klucz) > 300:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Klucz API ma zły format (powinien zaczynać się od sk-ant-).")
        return klucz
    if ustawienia.api_klucz_serwera and ustawienia.anthropic_api_key:
        return ustawienia.anthropic_api_key.get_secret_value()
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Podaj swój klucz Anthropic API w nagłówku X-Api-Key.")


def sprawdz_klucz(klient):
    """Darmowe zapytanie do Models API - zły klucz wychodzi od razu, a nie po minucie w tle."""
    try:
        klient.models.retrieve(ustawienia.claude_model)
    except anthropic.AuthenticationError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Klucz API jest nieprawidłowy albo został wyłączony.")
    except anthropic.PermissionDeniedError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Ten klucz nie ma dostępu do modelu {ustawienia.claude_model}.")
    except anthropic.APIConnectionError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Brak połączenia z Anthropic API.")
    except anthropic.APIStatusError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Anthropic API odpowiedziało błędem {e.status_code}.")


# ---------- szukanie ----------

@app.post("/szukaj", status_code=status.HTTP_202_ACCEPTED, response_model=Przyjete,
          summary="Uruchom szukanie w tle (płatne - Twój klucz API)")
def szukaj(kryteria: KryteriaSzukania, zadania_w_tle: BackgroundTasks,
           x_api_key: Annotated[Optional[str], Header(description="Twój klucz Anthropic API (sk-ant-...)")] = None):
    url = url_bazy()
    klucz = klucz_uzytkownika(x_api_key)

    zeton = zadania.zajmij_miejsce(ustawienia.api_maks_szukan)
    if zeton is None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Trwa już {ustawienia.api_maks_szukan} szukań naraz. Spróbuj za kilka minut.")
    try:
        klient = nowy_klient(klucz)
        sprawdz_klucz(klient)
        widziane = baza.widziane_linki(url)
        szukanie_id = baza.nowe_szukanie(url, kryteria)
    except BaseException:
        zadania.zwolnij(zeton)
        raise

    przebieg = Przebieg(klient, kryteria, widziane=widziane,
                        na_postep=lambda p: baza.aktualizuj_szukanie(url, szukanie_id, p))
    zadania.dodaj(zeton, szukanie_id, przebieg)
    # funkcja zwykła (def), więc Starlette odpala ją w osobnym wątku - odpowiedź 202 wraca od razu
    zadania_w_tle.add_task(zadania.wykonaj, url, szukanie_id, przebieg)
    return Przyjete(id=szukanie_id, status="trwa", adres_statusu=f"/szukaj/{szukanie_id}")


@app.get("/szukaj/{szukanie_id}", response_model=StatusSzukania, summary="Postęp albo wynik szukania")
def status_szukania(szukanie_id: int):
    p = zadania.trwajace(szukanie_id)
    if p is not None:
        l = p.licznik
        return StatusSzukania(id=szukanie_id, status="trwa" if p.status in ("nowy", "trwa") else p.status,
                              runda=p.runda, liczba_ofert=len(p.wyniki()), koszt_usd=round(l.usd, 4),
                              budzet_usd=l.budzet_usd, wyszukiwania=l.wyszukiwania, pobrania=l.pobrania,
                              blad=p.blad, na_zywo=True)
    w = baza.jedno_szukanie(url_bazy(), szukanie_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nie ma szukania o id {szukanie_id}.")
    # "trwa" w bazie, a nie ma go w pamięci = serwer został wyłączony w trakcie
    stan = "przerwane" if w["status"] == "trwa" else w["status"]
    return StatusSzukania(id=w["id"], status=stan, runda=w["runda"] or 0, liczba_ofert=w["liczba_ofert"] or 0,
                          koszt_usd=float(w["koszt_usd"] or 0), budzet_usd=float(w["budzet_usd"] or 0),
                          wyszukiwania=w["wyszukiwania"] or 0, pobrania=w["pobrania"] or 0,
                          blad=w["blad"], na_zywo=False)


# ---------- oferty ----------

def _filtry_ofert(pasuje: Optional[Literal["PASUJE", "NIE PASUJE", "DO SPRAWDZENIA"]] = None,
                  szukanie_id: Optional[int] = None,
                  aplikowano: Optional[bool] = Query(None, description="true = tylko te, gdzie wysłałem CV"),
                  limit: int = Query(100, ge=1, le=500)):
    # te same filtry dla tabeli (JSON) i dla pliku .ods
    return dict(pasuje=pasuje, szukanie_id=szukanie_id, aplikowano=aplikowano, limit=limit)


@app.get("/oferty.ods", summary="Oferty jako arkusz .ods (te same filtry co /oferty)")
def oferty_ods(filtry: Annotated[dict, Depends(_filtry_ofert)]):
    wiersze = baza.lista_ofert(url_bazy(), **filtry)
    # w arkuszu kolumna "Data dodania" to data z ogłoszenia, tak jak w wersji z konsoli
    do_arkusza = [{**w, "data_dodania": w["data_publikacji"],
                   "status": "CV wysłane" if w["aplikowano"] else w["status"]} for w in wiersze]
    plik = io.BytesIO()   # zapisuję do pamięci, bez pliku na dysku serwera
    zapisz_ods(do_arkusza, plik, [("Liczba ofert", len(wiersze)),
                                  ("Wygenerowano", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))])
    nazwa = f"oferty_{datetime.date.today().isoformat()}.ods"
    return Response(plik.getvalue(), media_type="application/vnd.oasis.opendocument.spreadsheet",
                    headers={"Content-Disposition": f'attachment; filename="{nazwa}"'})


@app.get("/oferty", response_model=list[OfertaZBazy], summary="Oferty z bazy (darmowe)")
def oferty(filtry: Annotated[dict, Depends(_filtry_ofert)]):
    return baza.lista_ofert(url_bazy(), **filtry)


@app.get("/oferty/{oferta_id}", response_model=OfertaZBazy, summary="Jedna oferta")
def oferta(oferta_id: int):
    w = baza.jedna_oferta(url_bazy(), oferta_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nie ma oferty o id {oferta_id}.")
    return w


@app.post("/oferty/{oferta_id}/aplikuj", response_model=OfertaZBazy, summary="Zaznacz: wysłałem CV (ręcznie)")
def aplikuj(oferta_id: int):
    # półautomat: tu tylko ZAPISUJĘ, że wysłałem CV. Agent nigdy sam niczego nie wysyła
    w = baza.oznacz_aplikowano(url_bazy(), oferta_id, True)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nie ma oferty o id {oferta_id}.")
    return w


@app.delete("/oferty/{oferta_id}/aplikuj", response_model=OfertaZBazy, summary="Cofnij zaznaczenie 'wysłałem CV'")
def cofnij_aplikuj(oferta_id: int):
    w = baza.oznacz_aplikowano(url_bazy(), oferta_id, False)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nie ma oferty o id {oferta_id}.")
    return w


@app.get("/kategorie", summary="Lista kategorii do formularza")
def kategorie():
    return KATEGORIE


@app.get("/info", summary="Ustawienia potrzebne stronie")
def info():
    # strona po tym wie, czy pole na klucz jest wymagane. Samego klucza oczywiście nie zwracam
    return {"klucz_serwera": bool(ustawienia.api_klucz_serwera and ustawienia.anthropic_api_key),
            "model": ustawienia.claude_model, "budzet_max_usd": ustawienia.budzet_max_usd}


# ---------- strona ----------

STRONA = KATALOG_PROJEKTU / "static" / "index.html"


@app.get("/", include_in_schema=False)
def strona():
    return FileResponse(STRONA, media_type="text/html; charset=utf-8")
