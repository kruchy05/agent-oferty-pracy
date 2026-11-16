# API agenta (FastAPI). Uruchomienie:  python -m uvicorn app.api:app --reload
# Strona:  http://127.0.0.1:8000/   Dokumentacja z przyciskami do testowania:  http://127.0.0.1:8000/docs
#
# Klucz API użytkownika przychodzi w nagłówku X-Api-Key (nie w adresie - adresy lądują w logach).
# Klucz jest też "kontem": z niego liczę identyfikator (HMAC, tozsamosc.py) i każdy widzi tylko swoje oferty.
# Samego klucza nie zapisuję: żyje tylko w pamięci, dopóki trwa szukanie.

import datetime
import io
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Literal, Optional

import anthropic
import psycopg
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, HttpUrl

from . import baza, cv, harmonogram, powiadomienia, zadania
from .agent import Przebieg
from .config import KATALOG_PROJEKTU, ustawienia
from .eksport import zapisz_ods
from .modele import KryteriaSzukania, ProfilCV
from .portale import KATEGORIE
from .tozsamosc import id_uzytkownika, zaszyfruj


def url_bazy():
    if ustawienia.database_url is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Brak DATABASE_URL w .env - API potrzebuje bazy.")
    return ustawienia.database_url.get_secret_value()


@asynccontextmanager
async def lifespan(app):
    # przy starcie tworzę brakujące tabele. Jak baza nie działa, serwer i tak wstaje -
    # endpointy oddadzą wtedy 503, zamiast całość wywalać się na starcie
    try:
        # moje stare oferty (sprzed rozdzielenia użytkowników) dostaje właściciel klucza z .env
        wlasciciel = id_uzytkownika(ustawienia.anthropic_api_key.get_secret_value()) if ustawienia.anthropic_api_key else None
        baza.przygotuj_baze(url_bazy(), wlasciciel)
    except Exception as e:
        print(f"[UWAGA] Baza niedostępna przy starcie: {type(e).__name__}: {e}  (docker start moj-postgres)")
    # harmonogram automatycznego szukania - tylko gdy jest baza (z niej bierze, komu przyszła pora)
    if ustawienia.harmonogram_wlaczony and ustawienia.database_url:
        harmonogram.uruchom(url_bazy())
    yield
    harmonogram.zatrzymaj()


app = FastAPI(title="Agent ofert pracy", version="0.6.0", lifespan=lifespan)

# CSP: przeglądarka uruchomi tylko skrypty z tego serwera (żadnych wstrzykniętych <script> ani inline).
# Ważne, bo strona może pamiętać klucz API w przeglądarce, a dane ofert pochodzą z obcych stron
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


@app.middleware("http")
async def naglowki_bezpieczenstwa(request: Request, call_next):
    odpowiedz = await call_next(request)
    if not request.url.path.startswith(("/docs", "/redoc")):   # Swagger ładuje skrypty z CDN - tam bez CSP
        odpowiedz.headers.setdefault("Content-Security-Policy", CSP)
    odpowiedz.headers.setdefault("X-Content-Type-Options", "nosniff")
    odpowiedz.headers.setdefault("Referrer-Policy", "no-referrer")   # linki do ofert nie zdradzają adresu strony
    return odpowiedz


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


class MojeSzukanie(BaseModel):
    id: int
    utworzono: datetime.datetime
    status: str
    stanowisko: Optional[str]
    miasto: Optional[str]
    liczba_ofert: Optional[int]
    koszt_usd: Optional[float]


# ---------- klucz API ----------

def nowy_klient(klucz):
    # osobna funkcja, żeby w testach podmienić ją na udawanego klienta
    return anthropic.Anthropic(api_key=klucz, max_retries=5)


def klucz_uzytkownika(x_api_key):
    """Klucz z nagłówka (sprawdzam tylko format) albo - lokalnie - mój z .env."""
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


@dataclass
class Uzytkownik:
    klucz: str
    id: str


def kto(x_api_key: Annotated[Optional[str], Header(description="Twój klucz Anthropic API (sk-ant-...)")] = None):
    """Każdy endpoint z danymi zaczyna od tego: klucz -> identyfikator użytkownika."""
    klucz = klucz_uzytkownika(x_api_key)
    return Uzytkownik(klucz=klucz, id=id_uzytkownika(klucz))


Ja = Annotated[Uzytkownik, Depends(kto)]


# ---------- szukanie ----------

@app.post("/szukaj", status_code=status.HTTP_202_ACCEPTED, response_model=Przyjete,
          summary="Uruchom szukanie w tle (płatne - Twój klucz API)")
def szukaj(kryteria: KryteriaSzukania, zadania_w_tle: BackgroundTasks, ja: Ja):
    url = url_bazy()
    if baza.szukania_w_ciagu_doby(url, ja.id) >= ustawienia.api_limit_dzienny:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Limit {ustawienia.api_limit_dzienny} szukań na dobę - spróbuj jutro.")
    zeton, powod = zadania.zajmij_miejsce(ustawienia.api_maks_szukan, ja.id)
    if zeton is None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Twoje poprzednie szukanie jeszcze trwa." if powod == zadania.ZAJETY_UZYTKOWNIK
                            else f"Trwa już {ustawienia.api_maks_szukan} szukań naraz. Spróbuj za kilka minut.")
    try:
        klient = nowy_klient(ja.klucz)
        sprawdz_klucz(klient)
        widziane = baza.widziane_linki(url, ja.id)
        szukanie_id = baza.nowe_szukanie(url, kryteria, ja.id)
    except BaseException:
        zadania.zwolnij(zeton)
        raise

    przebieg = Przebieg(klient, kryteria, widziane=widziane,
                        na_postep=lambda p: baza.aktualizuj_szukanie(url, szukanie_id, p))
    zadania.dodaj(zeton, szukanie_id, ja.id, przebieg)
    # funkcja zwykła (def), więc Starlette odpala ją w osobnym wątku - odpowiedź 202 wraca od razu
    zadania_w_tle.add_task(zadania.wykonaj, url, szukanie_id, ja.id, przebieg)
    return Przyjete(id=szukanie_id, status="trwa", adres_statusu=f"/szukaj/{szukanie_id}")


@app.get("/szukaj/{szukanie_id}", response_model=StatusSzukania, summary="Postęp albo wynik szukania")
def status_szukania(szukanie_id: int, ja: Ja):
    p = zadania.trwajace(szukanie_id, ja.id)
    if p is not None:
        l = p.licznik
        return StatusSzukania(id=szukanie_id, status="trwa" if p.status in ("nowy", "trwa") else p.status,
                              runda=p.runda, liczba_ofert=len(p.wyniki()), koszt_usd=round(l.usd, 4),
                              budzet_usd=l.budzet_usd, wyszukiwania=l.wyszukiwania, pobrania=l.pobrania,
                              blad=p.blad, na_zywo=True)
    w = baza.jedno_szukanie(url_bazy(), ja.id, szukanie_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nie ma szukania o id {szukanie_id}.")
    # "trwa" w bazie, a nie ma go w pamięci = serwer został wyłączony w trakcie
    stan = "przerwane" if w["status"] == "trwa" else w["status"]
    return StatusSzukania(id=w["id"], status=stan, runda=w["runda"] or 0, liczba_ofert=w["liczba_ofert"] or 0,
                          koszt_usd=float(w["koszt_usd"] or 0), budzet_usd=float(w["budzet_usd"] or 0),
                          wyszukiwania=w["wyszukiwania"] or 0, pobrania=w["pobrania"] or 0,
                          blad=w["blad"], na_zywo=False)


@app.get("/szukania", response_model=list[MojeSzukanie], summary="Moje ostatnie szukania")
def szukania(ja: Ja):
    return baza.moje_szukania(url_bazy(), ja.id)


# ---------- oferty ----------

def _filtry_ofert(pasuje: Optional[Literal["PASUJE", "NIE PASUJE", "DO SPRAWDZENIA"]] = None,
                  szukanie_id: Optional[int] = None,
                  aplikowano: Optional[bool] = Query(None, description="true = tylko te, gdzie wysłałem CV"),
                  limit: int = Query(100, ge=1, le=500)):
    # te same filtry dla tabeli (JSON) i dla pliku .ods
    return dict(pasuje=pasuje, szukanie_id=szukanie_id, aplikowano=aplikowano, limit=limit)


@app.get("/oferty.ods", summary="Oferty jako arkusz .ods (te same filtry co /oferty)")
def oferty_ods(filtry: Annotated[dict, Depends(_filtry_ofert)], ja: Ja):
    wiersze = baza.lista_ofert(url_bazy(), ja.id, **filtry)
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
def oferty(filtry: Annotated[dict, Depends(_filtry_ofert)], ja: Ja):
    return baza.lista_ofert(url_bazy(), ja.id, **filtry)


@app.get("/oferty/{oferta_id}", response_model=OfertaZBazy, summary="Jedna oferta")
def oferta(oferta_id: int, ja: Ja):
    w = baza.jedna_oferta(url_bazy(), ja.id, oferta_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nie ma oferty o id {oferta_id}.")
    return w


@app.post("/oferty/{oferta_id}/aplikuj", response_model=OfertaZBazy, summary="Zaznacz: wysłałem CV (ręcznie)")
def aplikuj(oferta_id: int, ja: Ja):
    # półautomat: tu tylko ZAPISUJĘ, że wysłałem CV. Agent nigdy sam niczego nie wysyła
    w = baza.oznacz_aplikowano(url_bazy(), ja.id, oferta_id, True)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nie ma oferty o id {oferta_id}.")
    return w


@app.delete("/oferty/{oferta_id}/aplikuj", response_model=OfertaZBazy, summary="Cofnij zaznaczenie 'wysłałem CV'")
def cofnij_aplikuj(oferta_id: int, ja: Ja):
    w = baza.oznacz_aplikowano(url_bazy(), ja.id, oferta_id, False)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Nie ma oferty o id {oferta_id}.")
    return w


# ---------- automatyczne szukanie ----------

class UstawieniaAutomatu(BaseModel):
    kryteria: KryteriaSzukania
    co_ile_dni: int = Field(2, ge=1, le=14)
    godzina: int = Field(7, ge=0, le=23, description="godzina startu w strefie serwera (domyślnie Europe/Warsaw)")
    # bez wyraźnej zgody nie zapisuję klucza - a bez klucza serwer nie poszuka sam
    zgoda_na_zapis_klucza: Literal[True] = Field(description="Zgoda na zapisanie Twojego klucza API (zaszyfrowanego)")


class StanAutomatu(BaseModel):
    kryteria: KryteriaSzukania
    co_ile_dni: int
    godzina: int
    wlaczony: bool
    ostatnio: Optional[datetime.datetime]
    ostatni_wynik: Optional[str]
    nastepne: Optional[datetime.datetime]


def _stan_automatu(w):
    if w is None:
        return None
    from zoneinfo import ZoneInfo
    strefa = ZoneInfo(ustawienia.strefa_czasowa)
    nastepne = None
    if w["wlaczony"] and w["ostatnio"]:
        dzien = w["ostatnio"].astimezone(strefa).date() + datetime.timedelta(days=w["co_ile_dni"])
        nastepne = datetime.datetime.combine(dzien, datetime.time(w["godzina"]), tzinfo=strefa)
    return StanAutomatu(**w, nastepne=nastepne)


@app.get("/automat", response_model=Optional[StanAutomatu], summary="Moje automatyczne szukanie (null = wyłączone)")
def automat(ja: Ja):
    return _stan_automatu(baza.automat(url_bazy(), ja.id))


@app.put("/automat", response_model=StanAutomatu, summary="Włącz albo zmień automatyczne szukanie (zapisuje klucz)")
def wlacz_automat(dane: UstawieniaAutomatu, ja: Ja):
    url = url_bazy()
    sprawdz_klucz(nowy_klient(ja.klucz))   # nie zapiszę klucza, który nie działa
    baza.zapisz_automat(url, ja.id, dane.kryteria.model_dump(), dane.co_ile_dni, dane.godzina, zaszyfruj(ja.klucz))
    return _stan_automatu(baza.automat(url, ja.id))


@app.delete("/automat", status_code=status.HTTP_204_NO_CONTENT, summary="Wyłącz automat i usuń mój klucz z serwera")
def wylacz_automat(ja: Ja):
    baza.usun_automat(url_bazy(), ja.id)


# ---------- powiadomienia push ----------

class KluczePush(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class Subskrypcja(BaseModel):
    # dokładnie to, co daje przeglądarka w PushSubscription.toJSON()
    endpoint: HttpUrl
    keys: KluczePush


@app.get("/push/klucz", summary="Publiczny klucz VAPID - przeglądarka potrzebuje go do subskrypcji")
def klucz_push():
    return {"klucz_publiczny": powiadomienia.klucze_vapid()["publiczny"]}


@app.post("/push/subskrypcja", status_code=status.HTTP_201_CREATED, summary="Zapisz tę przeglądarkę do powiadomień")
def zapisz_subskrypcje(sub: Subskrypcja, ja: Ja):
    if sub.endpoint.scheme != "https":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Adres usługi push musi być https.")
    baza.zapisz_subskrypcje(url_bazy(), ja.id, sub.model_dump(mode="json"))
    return {"ok": True}


@app.delete("/push/subskrypcja", status_code=status.HTTP_204_NO_CONTENT, summary="Wypisz tę przeglądarkę")
def usun_subskrypcje(sub: Subskrypcja, ja: Ja):
    baza.usun_subskrypcje(url_bazy(), str(sub.endpoint), ja.id)


@app.post("/push/test", summary="Wyślij próbne powiadomienie na moje urządzenia")
def test_push(ja: Ja):
    ile = powiadomienia.powiadom(url_bazy(), ja.id, "Powiadomienia działają", "Tak będą wyglądać info o nowych ofertach.")
    if not ile:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nie mam żadnego Twojego urządzenia z włączonymi powiadomieniami.")
    return {"wyslano": ile}


# ---------- profil z CV ----------

class WynikCV(BaseModel):
    profil: ProfilCV
    koszt_usd: float


@app.post("/cv", response_model=WynikCV, summary="Wczytaj CV (PDF w treści zapytania) - płatne, ok. 0,01 $",
          openapi_extra={"requestBody": {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
async def wczytaj_cv(request: Request, ja: Ja):
    # PDF przychodzi jako surowe bajty (Content-Type: application/pdf) - bez dodatkowej biblioteki do formularzy.
    # Czytam kawałkami i przerywam po 5 MB, żeby nikt nie zapchał pamięci serwera wielkim plikiem
    dane = bytearray()
    async for kawalek in request.stream():
        dane += kawalek
        if len(dane) > cv.MAKS_ROZMIAR_CV:
            raise HTTPException(413, "Plik jest za duży (maks. 5 MB).")   # liczba - nazwa stałej różni się między wersjami Starlette
    url = url_bazy()
    klient = nowy_klient(ja.klucz)
    try:
        # rozmowa z Claude trwa kilka sekund - w osobnym wątku, żeby nie blokować innych zapytań
        profil, koszt = await run_in_threadpool(cv.analizuj_cv, klient, bytes(dane))
    except cv.BladCV as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except anthropic.AuthenticationError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Klucz API jest nieprawidłowy albo został wyłączony.")
    except anthropic.APIError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Anthropic API: {type(e).__name__}")
    baza.zapisz_profil(url, ja.id, profil.model_dump())   # samo CV nie jest nigdzie zapisywane
    return WynikCV(profil=profil, koszt_usd=round(koszt, 4))


@app.get("/cv", response_model=Optional[ProfilCV], summary="Mój zapisany profil z CV (null = brak)")
def moj_profil(ja: Ja):
    return baza.profil(url_bazy(), ja.id)


@app.delete("/cv", status_code=status.HTTP_204_NO_CONTENT, summary="Usuń mój profil z CV")
def usun_profil(ja: Ja):
    baza.usun_profil(url_bazy(), ja.id)


@app.get("/kategorie", summary="Lista kategorii do formularza")
def kategorie():
    return KATEGORIE


@app.get("/info", summary="Ustawienia potrzebne stronie")
def info():
    # strona po tym wie, czy pole na klucz jest wymagane. Samego klucza oczywiście nie zwracam
    return {"klucz_serwera": bool(ustawienia.api_klucz_serwera and ustawienia.anthropic_api_key),
            "model": ustawienia.claude_model, "budzet_max_usd": ustawienia.budzet_max_usd,
            "limit_dzienny": ustawienia.api_limit_dzienny}


# ---------- strona ----------

STRONA = KATALOG_PROJEKTU / "static" / "index.html"
app.mount("/static", StaticFiles(directory=KATALOG_PROJEKTU / "static"), name="static")


@app.get("/", include_in_schema=False)
def strona():
    return FileResponse(STRONA, media_type="text/html; charset=utf-8")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # service worker musi leżeć w katalogu głównym, żeby obejmował całą stronę (scope "/")
    return FileResponse(KATALOG_PROJEKTU / "static" / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(KATALOG_PROJEKTU / "static" / "manifest.webmanifest", media_type="application/manifest+json")
