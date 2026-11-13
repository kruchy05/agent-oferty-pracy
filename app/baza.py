# Zapis do Postgresa (psycopg 3). Każda funkcja otwiera krótkie połączenie - prosto i bezpiecznie
# także wtedy, gdy kilka wyszukiwań działa naraz (później z FastAPI).
# Zapytania zawsze z parametrami %s - nigdy f-stringi w SQL (SQL injection).
#
# Tabela "oferty" jest zgodna z tą, którą miałem wcześniej (data_dodania = kiedy oferta trafiła do bazy).
# Nowe kolumny dokładam przez ADD COLUMN IF NOT EXISTS - istniejące dane zostają nietknięte.

import datetime

import psycopg
from psycopg.rows import dict_row

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
]


def polacz(url):
    # autocommit: każde zapytanie zapisuje się od razu (postęp widać w bazie w trakcie wyszukiwania)
    return psycopg.connect(url, autocommit=True, connect_timeout=5)


def przygotuj_baze(url):
    """Tworzy brakujące tabele i kolumny. Można wołać przy każdym starcie - nic nie psuje."""
    with polacz(url) as conn:
        with conn.transaction():
            for polecenie in SCHEMAT:
                conn.execute(polecenie)


def widziane_linki(url):
    with polacz(url) as conn:
        linki = conn.execute("SELECT link FROM oferty UNION SELECT link FROM odrzucone").fetchall()
    return {wiersz[0] for wiersz in linki}


def nowe_szukanie(url, kryteria):
    with polacz(url) as conn:
        wiersz = conn.execute(
            """INSERT INTO szukania (kategoria, stanowisko, kwalifikacje, doswiadczenie, miasto, zdalnie, budzet_usd)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (kryteria.kategoria, kryteria.stanowisko, kryteria.kwalifikacje, kryteria.doswiadczenie,
             kryteria.miasto, kryteria.zdalnie, kryteria.budzet_usd),
        ).fetchone()
    return wiersz[0]


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


def zapisz_wyniki(url, przebieg, szukanie_id=None):
    """Zapisuje dobre oferty (bez duplikatów: ON CONFLICT) i odrzucone linki. Zwraca (nowe, juz_byly)."""
    nowe = juz_byly = 0
    with polacz(url) as conn:
        with conn.transaction():   # wszystko albo nic
            for oferta, status in przebieg.wyniki():
                wiersz = conn.execute(
                    """INSERT INTO oferty (stanowisko, firma, miasto, wynagrodzenie, poziom, link, pasuje,
                                           szukanie_id, wymagania, spelnione_wymagania, brakujace_wymagania,
                                           data_publikacji, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (link) DO NOTHING
                       RETURNING id""",
                    (oferta.stanowisko, oferta.firma, oferta.miasto, oferta.wynagrodzenie, oferta.poziom,
                     oferta.link, oferta.pasuje, szukanie_id, oferta.wymagania, oferta.spelnione_wymagania,
                     oferta.brakujace_wymagania, _data(oferta.data_dodania), status),
                ).fetchone()
                if wiersz:      # RETURNING nic nie zwraca, gdy link już był w bazie
                    nowe += 1
                else:
                    juz_byly += 1
            for oferta, powod in przebieg.odrzucone:
                conn.execute(
                    "INSERT INTO odrzucone (link, powod, szukanie_id) VALUES (%s, %s, %s) ON CONFLICT (link) DO NOTHING",
                    (oferta.link, powod, szukanie_id),
                )
    return nowe, juz_byly


# ---------- odczyt dla API ----------

KOLUMNY_OFERTY = """id, stanowisko, firma, miasto, wynagrodzenie, poziom, link, pasuje, wymagania,
    spelnione_wymagania, brakujace_wymagania, data_publikacji, data_dodania, status, aplikowano, szukanie_id"""


def lista_ofert(url, pasuje=None, szukanie_id=None, aplikowano=None, limit=100):
    """Oferty z bazy, najnowsze pierwsze. Filtry są opcjonalne."""
    # w SQL wklejam tylko moje stałe kawałki tekstu - wartości od użytkownika idą zawsze jako %s
    warunki, parametry = [], []
    if pasuje:
        warunki.append("pasuje = %s")
        parametry.append(pasuje)
    if szukanie_id is not None:
        warunki.append("szukanie_id = %s")
        parametry.append(szukanie_id)
    if aplikowano is not None:
        warunki.append("aplikowano IS NOT NULL" if aplikowano else "aplikowano IS NULL")
    gdzie = (" WHERE " + " AND ".join(warunki)) if warunki else ""
    with polacz(url) as conn:
        conn.row_factory = dict_row   # wiersze jako słowniki - prosto zamienić na JSON
        return conn.execute(f"SELECT {KOLUMNY_OFERTY} FROM oferty{gdzie} ORDER BY id DESC LIMIT %s",
                            (*parametry, limit)).fetchall()


def jedna_oferta(url, oferta_id):
    with polacz(url) as conn:
        conn.row_factory = dict_row
        return conn.execute(f"SELECT {KOLUMNY_OFERTY} FROM oferty WHERE id = %s", (oferta_id,)).fetchone()


def oznacz_aplikowano(url, oferta_id, wyslane=True):
    """Zaznaczam, że RĘCZNIE wysłałem CV (albo cofam). Agent sam niczego nie wysyła.
    COALESCE - drugie kliknięcie nie nadpisuje daty pierwszego wysłania."""
    with polacz(url) as conn:
        conn.row_factory = dict_row
        return conn.execute(
            f"""UPDATE oferty SET aplikowano = CASE WHEN %s THEN COALESCE(aplikowano, NOW()) ELSE NULL END
                WHERE id = %s RETURNING {KOLUMNY_OFERTY}""",
            (wyslane, oferta_id),
        ).fetchone()


def jedno_szukanie(url, szukanie_id):
    with polacz(url) as conn:
        conn.row_factory = dict_row
        return conn.execute("SELECT * FROM szukania WHERE id = %s", (szukanie_id,)).fetchone()
