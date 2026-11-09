# Kategorie ofert i portale, na których agent szuka w danej kategorii.
# Klucze są bez polskich znaków (idą do bazy i adresów), etykiety są do wyświetlania.

KATEGORIE = {
    "it": "IT / programowanie",
    "sprzedaz": "Sprzedaż",
    "obsluga_klienta": "Obsługa klienta",
    "administracja": "Administracja / biuro",
    "logistyka": "Logistyka / magazyn",
    "produkcja": "Produkcja",
    "budownictwo": "Budownictwo",
    "gastronomia": "Gastronomia / hotelarstwo",
    "finanse": "Finanse / księgowość",
    "marketing": "Marketing",
    "medycyna": "Medycyna / opieka",
    "edukacja": "Edukacja",
    "inna": "Inna",
}

# portale ogólne - mają oferty z prawie każdej branży
# (indeed i olx blokują pobieranie stron, ale wyszukiwarka dalej je widzi)
PORTALE_OGOLNE = ["pracuj.pl", "rocketjobs.pl", "olx.pl", "pl.indeed.com", "linkedin.com"]

# nofluffjobs blokuje wyszukiwarkę Claude, więc go tu nie ma
PORTALE_IT = ["justjoin.it", "theprotocol.it", "pracuj.pl", "linkedin.com", "pl.indeed.com"]
PORTALE_ZDALNE_IT = ["remoteok.com", "weworkremotely.com", "wellfound.com"]

LICZBA_GRUP = 3   # każda runda szuka na innej grupie portali


def portale_dla(kategoria, zdalnie):
    """Lista portali dla kategorii. Każde wywołanie daje NOWĄ listę, więc przebiegi jej sobie nie psują."""
    if kategoria == "it":
        portale = PORTALE_IT + (PORTALE_ZDALNE_IT if zdalnie else []) + ["rocketjobs.pl", "olx.pl"]
    else:
        portale = list(PORTALE_OGOLNE)
    return list(dict.fromkeys(portale))   # bez duplikatów, w tej samej kolejności
