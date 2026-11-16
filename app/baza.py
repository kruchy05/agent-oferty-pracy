# Zapis do Postgresa (psycopg 3). Każda funkcja otwiera krótkie połączenie - prosto i bezpiecznie
# także wtedy, gdy kilka wyszukiwań działa naraz.
# Wartości zawsze jako parametry %s - nigdy wklejane w SQL (SQL injection).
# Każdy wiersz ma właściciela (uzytkownik = HMAC z klucza API, patrz tozsamosc.py) i każde zapytanie go filtruje.
#
# Tabela "oferty" jest zgodna z tą, którą miałem wcześniej (data_dodania = kiedy oferta trafiła do bazy).
# Nowe kolumny dokładam przez ADD COLUMN IF NOT EXISTS - istniejące dane zostają nietknięte.

import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMAT = [
    # historia wyszukiwań - kryteria, postęp i koszt (przyda się na stronie do paska postępu)
    """CREATE TABLE IF NOT EXISTS szukania (
        id SERIAL PRIMARY KEY,
        utworzono TIMESTAMP DEFAULT NOW(),
        zakonczono TIMESTAMP,
        status TEXT NOT NULL DEFAULT 'trwa',
        kategoria TEXT, stanowisko TEXT, kwalifikacje TEXT, doswiadczenie TEXT,
        miasto TEXT, zdalnie BOOLEAN, budzet_usd NUMERIC(6, 4),
        runda INTEGER DEFAULT 0, liczba_ofert INTEGER DEFAULT 0, koszt_usd NUMERIC(8, 4) DEFAULT 0,
        zapytania INTEGER DEFAULT 0, wyszukiwania INTEGER DEFAULT 0, pobrania INTEGER DEFAULT 0,
        tokeny_wejscie INTEGER DEFAULT 0, tokeny_wyjscie INTEGER DEFAULT 0,
        blad TEXT
    )""",
    # dokładnie taka tabela, jaką miałem wcześniej (tworzona z Pythona, bo PowerShell psuł 'staż')
    """CREATE TABLE IF NOT EXISTS oferty (
        id SERIAL PRIMARY KEY,
        stanowisko TEXT NOT NULL,
        firma TEXT,
        miasto TEXT,
        wynagrodzenie TEXT,
        poziom TEXT CHECK (poziom IN ('staż', 'junior', 'mid', 'senior', 'nieznany')),
        link TEXT UNIQUE NOT NULL,
        pasuje TEXT CHECK (pasuje IN ('PASUJE', 'NIE PASUJE', 'DO SPRAWDZENIA')),
        data_dodania TIMESTAMP DEFAULT NOW()
    )""",
    "ALTER TABLE oferty ADD COLUMN IF NOT EXISTS szukanie_id INTEGER REFERENCES szukania(id) ON DELETE SET NULL",
    "ALTER TABLE oferty ADD COLUMN IF NOT EXISTS wymagania TEXT[]",
    "ALTER TABLE oferty ADD COLUMN IF NOT EXISTS spelnione_wymagania TEXT[]",
    "ALTER TABLE oferty ADD COLUMN IF NOT EXISTS brakujace_wymagania TEXT[]",
    "ALTER TABLE oferty ADD COLUMN IF NOT EXISTS data_publikacji DATE",   # data z ogłoszenia (≠ data_dodania)
    "ALTER TABLE oferty ADD COLUMN IF NOT EXISTS status TEXT",            # aktywna / nie sprawdzono
    "ALTER TABLE oferty ADD COLUMN IF NOT EXISTS aplikowano TIMESTAMP",   # kiedy RĘCZNIE wysłałem CV
    # odrzucone oferty - żeby agent nie płacił drugi raz za te same wygasłe / niepasujące
    """CREATE TABLE IF NOT EXISTS odrzucone (
        link TEXT PRIMARY KEY,
        powod TEXT,
        szukanie_id INTEGER REFERENCES szukania(id) ON DELETE SET NULL,
        kiedy TIMESTAMP DEFAULT NOW()
    )""",
    # --- etap C: każdy użytkownik (klucz API) ma swoje szukania i oferty ---
    "ALTER TABLE szukania ADD COLUMN IF NOT EXISTS uzytkownik TEXT",
    "ALTER TABLE oferty ADD COLUMN IF NOT EXISTS uzytkownik TEXT",
    "ALTER TABLE odrzucone ADD COLUMN IF NOT EXISTS uzytkownik TEXT",
    # link był unikalny w całej tabeli - teraz ta sama oferta może być u dwóch osób, ale u jednej raz.
    # NULLS NOT DISTINCT (Postgres 15+): wiersze bez właściciela też nie mogą się dublować
    "ALTER TABLE oferty DROP CONSTRAINT IF EXISTS oferty_link_key",
    "CREATE UNIQUE INDEX IF NOT EXISTS oferty_uzytkownik_link ON oferty (uzytkownik, link) NULLS NOT DISTINCT",
    "ALTER TABLE odrzucone DROP CONSTRAINT IF EXISTS odrzucone_pkey",
    "CREATE UNIQUE INDEX IF NOT EXISTS odrzucone_uzytkownik_link ON odrzucone (uzytkownik, link) NULLS NOT DISTINCT",
    "CREATE INDEX IF NOT EXISTS szukania_uzytkownik_kiedy ON szukania (uzytkownik, utworzono)",
    # --- etap D: automatyczne szukanie co X dni + powiadomienia push ---
    "ALTER TABLE szukania ADD COLUMN IF NOT EXISTS automatyczne BOOLEAN DEFAULT FALSE",
    """CREATE TABLE IF NOT EXISTS automaty (
        uzytkownik TEXT PRIMARY KEY,
        kryteria JSONB NOT NULL,
        co_ile_dni INTEGER NOT NULL DEFAULT 2 CHECK (co_ile_dni BETWEEN 1 AND 14),
        godzina INTEGER NOT NULL DEFAULT 7 CHECK (godzina BETWEEN 0 AND 23),
        klucz_zaszyfrowany BYTEA NOT NULL,   -- Fernet, patrz tozsamosc.py; kasowany razem z automatem
        wlaczony BOOLEAN NOT NULL DEFAULT TRUE,
        ostatnio TIMESTAMPTZ,
        ostatni_wynik TEXT,
        utworzono TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS subskrypcje_push (
        id SERIAL PRIMARY KEY,
        uzytkownik TEXT NOT NULL,
        endpoint TEXT UNIQUE NOT NULL,       -- adres usługi push przeglądarki (jedna przeglądarka = jedna subskrypcja)
        dane JSONB NOT NULL,                 -- endpoint + klucze szyfrowania od przeglądarki
        utworzono TIMESTAMPTZ DEFAULT NOW()
    )""",
]


def polacz(url):
    # autocommit: każde zapytanie zapisuje się od razu (postęp widać w bazie w trakcie wyszukiwania)
    return psycopg.connect(url, autocommit=True, connect_timeout=5)


def przygotuj_baze(url, wlasciciel=None):
    """Tworzy brakujące tabele i kolumny. Można wołać przy każdym starcie - nic nie psuje.
    wlasciciel = id użytkownika, który dostaje stare wiersze bez właściciela (moje oferty sprzed etapu C)."""
    with polacz(url) as conn:
        with conn.transaction():
            for polecenie in SCHEMAT:
                conn.execute(polecenie)
            if wlasciciel:
                for tabela in ("szukania", "oferty", "odrzucone"):
                    # nazwa tabeli z mojej stałej listy, nie od użytkownika
                    conn.execute(f"UPDATE {tabela} SET uzytkownik = %s WHERE uzytkownik IS NULL", (wlasciciel,))


def widziane_linki(url, uzytkownik):
    """Linki, które ten użytkownik już ma (dobre i odrzucone) - za nie agent nie płaci drugi raz."""
    with polacz(url) as conn:
        linki = conn.execute("SELECT link FROM oferty WHERE uzytkownik = %s "
                             "UNION SELECT link FROM odrzucone WHERE uzytkownik = %s", (uzytkownik, uzytkownik)).fetchall()
    return {wiersz[0] for wiersz in linki}


def nowe_szukanie(url, kryteria, uzytkownik, automatyczne=False):
    with polacz(url) as conn:
        wiersz = conn.execute(
            """INSERT INTO szukania (uzytkownik, kategoria, stanowisko, kwalifikacje, doswiadczenie, miasto, zdalnie,
                                     budzet_usd, automatyczne)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (uzytkownik, kryteria.kategoria, kryteria.stanowisko, kryteria.kwalifikacje, kryteria.doswiadczenie,
             kryteria.miasto, kryteria.zdalnie, kryteria.budzet_usd, automatyczne),
        ).fetchone()
    return wiersz[0]


def szukania_w_ciagu_doby(url, uzytkownik):
    """Do dziennego limitu szukań na osobę."""
    with polacz(url) as conn:
        return conn.execute("SELECT count(*) FROM szukania WHERE uzytkownik = %s AND utworzono > NOW() - INTERVAL '1 day'",
                            (uzytkownik,)).fetchone()[0]


def aktualizuj_szukanie(url, szukanie_id, przebieg):
    """Postęp wyszukiwania - wołane po każdej rundzie i na końcu."""
    l = przebieg.licznik
    koniec = przebieg.status in ("gotowe", "budzet", "blad")
    with polacz(url) as conn:
        conn.execute(
            """UPDATE szukania SET status = %s, runda = %s, liczba_ofert = %s, koszt_usd = %s,
                      zapytania = %s, wyszukiwania = %s, pobrania = %s, tokeny_wejscie = %s, tokeny_wyjscie = %s,
                      blad = %s, zakonczono = CASE WHEN %s THEN NOW() ELSE zakonczono END
               WHERE id = %s""",
            (przebieg.status, przebieg.runda, len(przebieg.wyniki()), round(l.usd, 4),
             l.zapytania, l.wyszukiwania, l.pobrania, l.wejscie, l.wyjscie,
             przebieg.blad, koniec, szukanie_id),
        )


def _data(tekst):
    try:
        return datetime.date.fromisoformat(str(tekst)[:10]) if tekst else None
    except ValueError:
        return None


def zapisz_wyniki(url, przebieg, szukanie_id, uzytkownik):
    """Zapisuje dobre oferty (bez duplikatów: ON CONFLICT) i odrzucone linki. Zwraca (nowe, juz_byly)."""
    nowe = juz_byly = 0
    with polacz(url) as conn:
        with conn.transaction():   # wszystko albo nic
            for oferta, status in przebieg.wyniki():
                wiersz = conn.execute(
                    """INSERT INTO oferty (uzytkownik, stanowisko, firma, miasto, wynagrodzenie, poziom, link, pasuje,
                                           szukanie_id, wymagania, spelnione_wymagania, brakujace_wymagania,
                                           data_publikacji, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (uzytkownik, link) DO NOTHING
                       RETURNING id""",
                    (uzytkownik, oferta.stanowisko, oferta.firma, oferta.miasto, oferta.wynagrodzenie, oferta.poziom,
                     oferta.link, oferta.pasuje, szukanie_id, oferta.wymagania, oferta.spelnione_wymagania,
                     oferta.brakujace_wymagania, _data(oferta.data_dodania), status),
                ).fetchone()
                if wiersz:      # RETURNING nic nie zwraca, gdy link już był w bazie
                    nowe += 1
                else:
                    juz_byly += 1
            for oferta, powod in przebieg.odrzucone:
                conn.execute(
                    """INSERT INTO odrzucone (uzytkownik, link, powod, szukanie_id) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (uzytkownik, link) DO NOTHING""",
                    (uzytkownik, oferta.link, powod, szukanie_id),
                )
    return nowe, juz_byly


# ---------- odczyt dla API ----------

KOLUMNY_OFERTY = """id, stanowisko, firma, miasto, wynagrodzenie, poziom, link, pasuje, wymagania,
    spelnione_wymagania, brakujace_wymagania, data_publikacji, data_dodania, status, aplikowano, szukanie_id"""


def lista_ofert(url, uzytkownik, pasuje=None, szukanie_id=None, aplikowano=None, limit=100):
    """Oferty TEGO użytkownika, najnowsze pierwsze. Filtry są opcjonalne."""
    # w SQL wklejam tylko moje stałe kawałki tekstu - wartości od użytkownika idą zawsze jako %s
    warunki, parametry = ["uzytkownik = %s"], [uzytkownik]
    if pasuje:
        warunki.append("pasuje = %s")
        parametry.append(pasuje)
    if szukanie_id is not None:
        warunki.append("szukanie_id = %s")
        parametry.append(szukanie_id)
    if aplikowano is not None:
        warunki.append("aplikowano IS NOT NULL" if aplikowano else "aplikowano IS NULL")
    gdzie = " WHERE " + " AND ".join(warunki)
    with polacz(url) as conn:
        conn.row_factory = dict_row   # wiersze jako słowniki - prosto zamienić na JSON
        return conn.execute(f"SELECT {KOLUMNY_OFERTY} FROM oferty{gdzie} ORDER BY id DESC LIMIT %s",
                            (*parametry, limit)).fetchall()


def jedna_oferta(url, uzytkownik, oferta_id):
    # cudza oferta wygląda tak samo jak nieistniejąca (None -> 404) - nie zdradzam, że takie id jest
    with polacz(url) as conn:
        conn.row_factory = dict_row
        return conn.execute(f"SELECT {KOLUMNY_OFERTY} FROM oferty WHERE id = %s AND uzytkownik = %s",
                            (oferta_id, uzytkownik)).fetchone()


def oznacz_aplikowano(url, uzytkownik, oferta_id, wyslane=True):
    """Zaznaczam, że RĘCZNIE wysłałem CV (albo cofam). Agent sam niczego nie wysyła.
    COALESCE - drugie kliknięcie nie nadpisuje daty pierwszego wysłania."""
    with polacz(url) as conn:
        conn.row_factory = dict_row
        return conn.execute(
            f"""UPDATE oferty SET aplikowano = CASE WHEN %s THEN COALESCE(aplikowano, NOW()) ELSE NULL END
                WHERE id = %s AND uzytkownik = %s RETURNING {KOLUMNY_OFERTY}""",
            (wyslane, oferta_id, uzytkownik),
        ).fetchone()


def jedno_szukanie(url, uzytkownik, szukanie_id):
    with polacz(url) as conn:
        conn.row_factory = dict_row
        return conn.execute("SELECT * FROM szukania WHERE id = %s AND uzytkownik = %s",
                            (szukanie_id, uzytkownik)).fetchone()


def moje_szukania(url, uzytkownik, limit=20):
    """Lista moich ostatnich szukań - do filtra "Z szukania" na stronie."""
    with polacz(url) as conn:
        conn.row_factory = dict_row
        return conn.execute("""SELECT id, utworzono, status, stanowisko, miasto, liczba_ofert, koszt_usd FROM szukania
                               WHERE uzytkownik = %s ORDER BY id DESC LIMIT %s""", (uzytkownik, limit)).fetchall()



# ---------- automatyczne szukanie ----------

def zapisz_automat(url, uzytkownik, kryteria, co_ile_dni, godzina, klucz_zaszyfrowany):
    """Włącza (albo zmienia) automat. ostatnio = teraz, więc pierwsze szukanie za co_ile_dni o danej godzinie."""
    with polacz(url) as conn:
        conn.execute(
            """INSERT INTO automaty (uzytkownik, kryteria, co_ile_dni, godzina, klucz_zaszyfrowany, wlaczony, ostatnio)
               VALUES (%s, %s, %s, %s, %s, TRUE, NOW())
               ON CONFLICT (uzytkownik) DO UPDATE SET kryteria = EXCLUDED.kryteria, co_ile_dni = EXCLUDED.co_ile_dni,
                   godzina = EXCLUDED.godzina, klucz_zaszyfrowany = EXCLUDED.klucz_zaszyfrowany, wlaczony = TRUE,
                   ostatni_wynik = NULL""",
            (uzytkownik, Jsonb(kryteria), co_ile_dni, godzina, klucz_zaszyfrowany),
        )


def automat(url, uzytkownik):
    """Ustawienia automatu BEZ zaszyfrowanego klucza (ten nigdy nie wychodzi z bazy do API)."""
    with polacz(url) as conn:
        conn.row_factory = dict_row
        return conn.execute("""SELECT kryteria, co_ile_dni, godzina, wlaczony, ostatnio, ostatni_wynik FROM automaty
                               WHERE uzytkownik = %s""", (uzytkownik,)).fetchone()


def usun_automat(url, uzytkownik):
    """Wyłączenie = usunięcie całego wiersza, razem z zaszyfrowanym kluczem."""
    with polacz(url) as conn:
        return conn.execute("DELETE FROM automaty WHERE uzytkownik = %s", (uzytkownik,)).rowcount > 0


# komu przyszła pora: dziś (w mojej strefie czasowej) minęła godzina startu, a od ostatniego szukania
# minęło co najmniej co_ile_dni DNI KALENDARZOWYCH. Dni, nie godziny - inaczej start "co 2 dni o 7:00"
# by się przesuwał, a zwykły cron "*/2" na przełomie miesięcy odpala się dwa dni z rzędu (31. i 1.)
_PORA = """wlaczony
    AND EXTRACT(HOUR FROM NOW() AT TIME ZONE %(strefa)s) >= godzina
    AND (ostatnio IS NULL
         OR (NOW() AT TIME ZONE %(strefa)s)::date - (ostatnio AT TIME ZONE %(strefa)s)::date >= co_ile_dni)"""


def przejmij_automat(url, strefa):
    """Bierze JEDEN automat, któremu przyszła pora, i od razu ustawia mu ostatnio = teraz.
    FOR UPDATE SKIP LOCKED - nawet przy kilku procesach serwera każdy automat ruszy tylko raz.
    Zwraca też poprzednie "ostatnio", żeby je przywrócić, gdy serwer jest zajęty."""
    with polacz(url) as conn:
        conn.row_factory = dict_row
        return conn.execute(
            f"""WITH kandydat AS (
                    SELECT uzytkownik, ostatnio FROM automaty WHERE {_PORA}
                    ORDER BY ostatnio NULLS FIRST LIMIT 1 FOR UPDATE SKIP LOCKED)
                UPDATE automaty a SET ostatnio = NOW() FROM kandydat k WHERE a.uzytkownik = k.uzytkownik
                RETURNING a.uzytkownik, a.kryteria, a.klucz_zaszyfrowany, k.ostatnio AS poprzednio""",
            {"strefa": strefa},
        ).fetchone()


def oddaj_automat(url, uzytkownik, poprzednio):
    """Serwer był zajęty - cofam ostatnio, więc automat ruszy przy następnym sprawdzeniu."""
    with polacz(url) as conn:
        conn.execute("UPDATE automaty SET ostatnio = %s WHERE uzytkownik = %s", (poprzednio, uzytkownik))


def wynik_automatu(url, uzytkownik, opis, wylacz=False):
    with polacz(url) as conn:
        conn.execute("UPDATE automaty SET ostatni_wynik = %s, wlaczony = wlaczony AND NOT %s WHERE uzytkownik = %s",
                     (opis, wylacz, uzytkownik))


# ---------- subskrypcje push ----------

def zapisz_subskrypcje(url, uzytkownik, dane):
    # ta sama przeglądarka zapisana drugi raz (albo przez kogoś z innym kluczem) - nadpisuję właściciela
    with polacz(url) as conn:
        conn.execute("""INSERT INTO subskrypcje_push (uzytkownik, endpoint, dane) VALUES (%s, %s, %s)
                        ON CONFLICT (endpoint) DO UPDATE SET uzytkownik = EXCLUDED.uzytkownik, dane = EXCLUDED.dane""",
                     (uzytkownik, dane["endpoint"], Jsonb(dane)))


def subskrypcje(url, uzytkownik):
    with polacz(url) as conn:
        return [w[0] for w in conn.execute("SELECT dane FROM subskrypcje_push WHERE uzytkownik = %s", (uzytkownik,))]


def usun_subskrypcje(url, endpoint, uzytkownik=None):
    """uzytkownik=None - usuwa ją serwer, bo usługa push odpowiedziała, że subskrypcja wygasła."""
    with polacz(url) as conn:
        if uzytkownik is None:
            return conn.execute("DELETE FROM subskrypcje_push WHERE endpoint = %s", (endpoint,)).rowcount > 0
        return conn.execute("DELETE FROM subskrypcje_push WHERE endpoint = %s AND uzytkownik = %s",
                            (endpoint, uzytkownik)).rowcount > 0
