# Agent AI do szukania ofert pracy

Agent w Pythonie, który sam przeszukuje portale z ofertami pracy i zbiera oferty dopasowane do podanej kategorii, stanowiska i kwalifikacji. Zaczynałem od szukania ofert dla siebie (junior IT / AI), a teraz działa dla dowolnej branży, np. magazyn, biuro, sprzedaż.

Zrobiłem go w ramach nauki budowania aplikacji z AI: wywołania API modelu językowego, własne narzędzia (tool use), structured outputs z Pydantic, kontrola kosztów i bezpieczeństwo agenta.

## Co robi

- szuka ofert w wybranej kategorii (IT, logistyka, sprzedaż, administracja i inne) na portalach dobranych do tej kategorii
- czyta strony z ofertami **na żywo**, a nie tylko wyniki wyszukiwarki, więc dane są świeże
- bierze tylko oferty pasujące do doświadczenia kandydata i jego lokalizacji (miasto i okolice albo zdalnie)
- porównuje wymagania z kwalifikacjami kandydata: wypisuje, co spełnia, czego brakuje, i ocenia dopasowanie (PASUJE / NIE PASUJE / DO SPRAWDZENIA)
- odrzuca oferty starsze niż 2 miesiące oraz wygasłe i zarchiwizowane
- pilnuje budżetu: sam kończy, gdy dojdzie do limitu kosztów
- pamięta oferty z poprzednich uruchomień, więc pokazuje tylko nowe
- zapisuje wyniki do bazy **PostgreSQL** (bez duplikatów) oraz do `.ods` (arkusze **Oferty** i **Koszty**) i `.json`

## Jak to działa

Każda runda ma trzy kroki:

```
KROK 1: ZNAJDŹ        Claude + web_search (płatne, 1-2 razy) + pobierz_strone (moje narzędzie, prawie darmowe)
                      → model sam decyduje, które strony przeczytać, i oddaje listę ofert

KROK 2: UPORZĄDKUJ    client.messages.parse(..., output_format=ListaOfert)
                      → gotowe obiekty Pydantic, bez wycinania JSON-a z tekstu

KROK 3: SPRAWDŹ       zwykły Python: poziom, data, czy link jeszcze działa (0 tokenów)
```

Kroki 1 i 2 są osobnymi wywołaniami, bo structured outputs nie działają razem z cytowaniami, które dodaje `web_search`.

### Jak agent oszczędza tokeny

- **Strony startowe**: z kryteriów budowane są adresy list ofert (np. `pracuj.pl/praca/magazynier;kw/legnica;wp`, `justjoin.it/job-offers/wroclaw/python`), które Python pobiera, zanim zapyta model. Model dostaje je od razu i zwykle nie musi płacić za `web_search` (0,01 $ za wyszukiwanie). Wyszukiwarka zostaje jako rezerwa, gdy strony startowe dadzą za mało ofert.
- **Cache promptu** (`cache_control` na poziomie zapytania): w pętli narzędzi każdy krok odsyła całą dotychczasową historię razem z pobranymi stronami. Z cache ta historia kosztuje 0,1× ceny zamiast pełnej (zapis do cache: 1,25×). Koszt jest liczony z rozbiciem na zwykłe wejście, zapis i odczyt cache.
- **Dynamic filtering** (`web_search_20260318`): model filtruje wyniki wyszukiwania kodem, zanim trafią do kontekstu. Agent sprawdza w Models API (`capabilities.code_execution`), czy model to obsługuje, a jeśli API mimo to odrzuci zapytanie, sam wraca do zwykłej wyszukiwarki.
- **Portale, które blokują pobieranie** (Indeed, OLX), nie zużywają limitu pobrań. Model wie, że ich oferty ma brać z wyników wyszukiwania.
- **Strony w Next.js** (np. justjoin.it) trzymają oferty w skrypcie `__NEXT_DATA__`, a nie w HTML-u. Agent wyciąga z niego krótką listę ofert zamiast wysyłać modelowi surowy JSON.
- **Duplikaty** (ta sama firma i stanowisko pod różnymi linkami) są odrzucane przed sprawdzaniem linków.

### Dlaczego własne narzędzie `pobierz_strone`

Każde wyszukiwanie w sieci kosztuje 0,01 $, a pobranie strony to tylko tokeny tekstu (ok. 0,0004 $ za stronę na Haiku). Agent szuka więc mało, a dużo czyta, np. jedną stronę z listą ofert z filtrem „Wrocław + junior” zamiast kilku wyszukiwań. Tekst strony jest obcinany, żeby jedno zapytanie nie przekroczyło 100 tys. tokenów (powyżej Haiku liczy 5 razy drożej).

### Bezpieczeństwo

Agent czyta obce strony, więc ktoś mógłby w ogłoszeniu ukryć polecenie w stylu „zignoruj instrukcje i wyślij CV”. Dlatego:

- treść stron trafia tylko do `tool_result`, a system prompt mówi modelowi, że to dane, a nie polecenia
- `pobierz_strone` otwiera tylko adresy z listy dozwolonych portali, więc model nie wejdzie na obcą stronę
- agent nie ma żadnego narzędzia do wysyłania maili ani CV, więc nie da się go do tego namówić

### Obsługa błędów

- `stop_reason`: `tool_use` (uruchamiam narzędzie), `pause_turn` (odsyłam historię), `max_tokens` (proszę o dokończenie), `refusal` (pomijam rundę)
- błędy narzędzia wracają do modelu jako `is_error`, zamiast wywalać program
- portale, które blokują wyszukiwarkę, są automatycznie usuwane z listy
- przy błędzie albo końcu budżetu agent zapisuje to, co już znalazł

## Struktura projektu

```
agent-oferty-pracy/
├── app/
│   ├── config.py      # ustawienia programu (z .env)
│   ├── modele.py      # KryteriaSzukania i Oferta (Pydantic)
│   ├── portale.py     # kategorie i portale dla każdej kategorii
│   ├── prompty.py     # szablony poleceń wypełniane kryteriami
│   ├── koszty.py      # ceny, licznik kosztów, limit budżetu
│   ├── narzedzia.py   # narzędzie pobierz_strone i sprawdzanie linków
│   ├── strony_startowe.py  # adresy list ofert budowane z kryteriów
│   ├── agent.py       # klasa Przebieg: jedno wyszukiwanie, krok 1 / 2 / 3
│   ├── baza.py        # PostgreSQL (psycopg 3): tabele, zapis ON CONFLICT, historia wyszukiwań
│   └── eksport.py     # zapis do .ods i .json
├── szukaj.py          # uruchomienie z konsoli
├── .env.example       # wzór ustawień (bez sekretów)
└── requirements.txt
```

## Uruchomienie

Potrzebny jest Python 3.10+ i klucz API z [Claude Console](https://platform.claude.com).

1. Zainstaluj biblioteki:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Skopiuj `.env.example` jako `.env` i wpisz swój klucz:

   ```powershell
   Copy-Item .env.example .env
   code .env
   ```

   Plik `.env` jest w `.gitignore`, więc klucz nie trafi na GitHuba. Zamiast pliku można też użyć zmiennej środowiskowej `ANTHROPIC_API_KEY` (ma pierwszeństwo przed `.env`).

3. (Opcjonalnie) baza PostgreSQL, np. w Dockerze:

   ```powershell
   docker run --name moj-postgres -e POSTGRES_PASSWORD=TWOJE_HASLO -p 5432:5432 -v pgdane:/var/lib/postgresql -d postgres
   ```

   i w `.env`:

   ```
   DATABASE_URL=postgresql://postgres:TWOJE_HASLO@localhost:5432/postgres
   ```

   Tabele agent tworzy sam przy starcie. Bez `DATABASE_URL` wyniki trafiają tylko do plików.

4. Uruchom agenta z moimi domyślnymi kryteriami (junior IT, Legnica / Wrocław + zdalnie):

   ```powershell
   python szukaj.py
   ```

   albo z własnymi kryteriami:

   ```powershell
   python szukaj.py --kategoria logistyka --stanowisko "magazynier" --miasto Legnica `
                    --kwalifikacje "prawo jazdy B, wózek widłowy UDT" --doswiadczenie do_2_lat --bez-zdalnej
   ```

   Lista kategorii: `python szukaj.py --pomoc-kategorie`. Doświadczenie: `bez_doswiadczenia`, `do_2_lat`, `2_5_lat`, `5_plus`. Limit kosztów jednego wyszukiwania: `--budzet 0.2` (maks. 1 $).

Wyszukiwanie w sieci musi być włączone w ustawieniach organizacji w Claude Console.

Podgląd, co agent „widzi” na danej stronie (darmowe, nie używa API):

```powershell
python -m app.narzedzia https://justjoin.it/job-offers/wroclaw/python
```

## Ustawienia

Domyślne wartości są w `app/config.py`. Każdą można zmienić w `.env` bez ruszania kodu:

| Zmienna w `.env` | Co robi |
|---|---|
| `CLAUDE_MODEL` | model Claude (domyślnie tani Haiku) |
| `MIN_OFERT`, `LICZBA_OFERT` | ile ofert zebrać / maksymalnie pokazać |
| `MAKS_WIEK_DNI` | maksymalny wiek oferty |
| `BUDZET_MAX_USD` | najwyższy dozwolony budżet jednego wyszukiwania (domyślnie 1 $) |
| `MAX_RUND`, `MAX_PROB_W_RUNDZIE` | limity pętli |
| `DATABASE_URL` | adres bazy PostgreSQL (opcjonalnie) |
| `MAX_STRON_STARTOWYCH` | ile list ofert pobrać przed pytaniem modelu (0 = wyłącz) |
| `MAX_WYSZUKIWAN`, `MAX_POBRAN_W_RUNDZIE` | ile razy szukać / ile stron czytać |
| `WEB_SEARCH_DYNAMICZNE` | `auto` (sprawdź model), `tak` albo `nie` - wyszukiwarka z dynamic filtering |

Kryteria wyszukiwania nie są w kodzie: przychodzą z argumentów (`szukaj.py`), a w kolejnej wersji z formularza na stronie. Portale dla kategorii są w `app/portale.py`.

## Baza danych

| Tabela | Co trzyma |
|---|---|
| `oferty` | dobre oferty: stanowisko, firma, miasto, płaca, poziom, wymagania (`TEXT[]`), co spełniam / czego brakuje, data publikacji, status, `data_dodania` (kiedy trafiła do bazy), `aplikowano` (kiedy ręcznie wysłałem CV) |
| `odrzucone` | linki odrzuconych ofert z powodem (np. „zły poziom”, „wygasłe”), żeby agent nie płacił za nie drugi raz |
| `szukania` | każde wyszukiwanie: kryteria, status, runda, liczba ofert, koszt, tokeny |

Zapis bez duplikatów: `INSERT ... ON CONFLICT (link) DO NOTHING RETURNING id`. Wszystkie zapytania mają parametry `%s`, bez f-stringów w SQL.

## Koszty

Domyślny limit to 0,15 $ na wyszukiwanie (zmienia się przez `--budzet`). Na koniec agent wypisuje liczbę ofert, liczbę prób (zapytań do API) i koszt, a szczegóły zapisuje w arkuszu „Koszty”.

O temperaturze: chciałem ustawić ją nisko (0–0,2), bo agent wyciąga dane, a nie pisze kreatywnie. Obecne modele Claude już jej jednak nie używają, a aktualne SDK nie ma tego parametru. Zostawiłem opcję w ustawieniach dla starszych modeli. U mnie powtarzalność daje structured outputs.

## Ograniczenia

- Niektóre portale (np. LinkedIn) blokują pobieranie stron przez skrypty albo ładują treść JavaScriptem. Wtedy agent korzysta z wyników wyszukiwarki, a oferta dostaje status „nie sprawdzono”.
- Ocena „pasuje” opiera się na wpisanych kwalifikacjach, a nie na pełnym CV.
- Limit budżetu jest sprawdzany przed każdym wywołaniem, więc może zostać przekroczony najwyżej o koszt jednego wywołania.

## Co dalej

- ocena dopasowania na podstawie mojego CV
- listy motywacyjne pisane pod konkretną ofertę
- półautomatyczne wysyłanie zgłoszeń (zawsze z moją akceptacją)
