# Strony startowe: adresy list ofert budowane z kryteriów, pobierane przez Pythona PRZED pierwszym
# wywołaniem modelu. Dzięki temu model zwykle nie musi używać web_search (0,01 $ za każde wyszukiwanie).
#
# Formaty adresów wziąłem z moich prawdziwych uruchomień (linie "pobieram: ... ok" w logach):
#   https://www.pracuj.pl/praca/junior%20python;kw/wroclaw;wp
#   https://justjoin.it/job-offers/wroclaw/python        https://justjoin.it/job-offers/all-locations/ai
#   https://theprotocol.it/filtry/junior;p/wroclaw;wp
#   https://remoteok.com/remote-junior+python-jobs       https://weworkremotely.com/remote-junior-jobs
# Jak któryś portal zmieni adresy, pobranie skończy się błędem (404) i agent po prostu użyje web_search.

import re
import unicodedata
from urllib.parse import quote

# technologie z justjoin.it (z menu strony) -> słowa, po których je rozpoznaję w stanowisku / kwalifikacjach.
# sprawdzone adresy: python i ai; pozostałe mają ten sam format, a błędny adres = zwykłe 404
TECHNOLOGIE_JUSTJOIN = {
    "python": ["python", "django", "fastapi", "flask"],
    "ai": [" ai ", "ai/", "/ai", "machine learning", " ml ", "llm", "sztuczn"],
    "data": ["data engineer", "data analyst", "analityk danych", "inżynier danych", "big data"],
    "java": ["java ", "java,", "spring"],
    "javascript": ["javascript", "typescript", "react", "node", "frontend", "front-end"],
    "testing": ["tester", "testing", "qa ", "testow"],
    "devops": ["devops", "kubernetes", "docker"],
    "php": ["php", "laravel", "symfony"],
    "net": [".net", "c#"],
}


def slug(tekst):
    """'Wrocław' -> 'wroclaw', 'Nowa Sól' -> 'nowa-sol' (bez polskich znaków, jak w adresach portali)."""
    bez_ogonkow = unicodedata.normalize("NFKD", tekst.replace("ł", "l").replace("Ł", "L"))
    bez_ogonkow = "".join(z for z in bez_ogonkow if not unicodedata.combining(z))
    return re.sub(r"[^a-z0-9]+", "-", bez_ogonkow.lower()).strip("-")


def lista_miast(miasto):
    """'Legnica / Wrocław' -> ['Legnica', 'Wrocław']"""
    return [m.strip() for m in re.split(r"[/,;]| i ", miasto or "") if m.strip()]


def frazy_stanowiska(stanowisko):
    """'junior Python developer / junior AI engineer / staż IT' -> ['junior Python developer', 'junior AI engineer']"""
    return [f.strip() for f in stanowisko.split("/") if len(f.strip()) >= 2][:2]


def technologie(kryteria):
    tekst = f" {kryteria.stanowisko} {kryteria.kwalifikacje} ".lower()
    znalezione = [t for t, slowa in TECHNOLOGIE_JUSTJOIN.items() if any(s in tekst for s in slowa)]
    return znalezione[:2]


def adresy_startowe(kryteria, domeny, limit):
    """Adresy list ofert dla portali z tej rundy (domeny), najważniejsze najpierw."""
    miasta = lista_miast(kryteria.miasto)[:2]
    frazy = frazy_stanowiska(kryteria.stanowisko)
    poczatkujacy = kryteria.doswiadczenie in ("bez_doswiadczenia", "do_2_lat")
    it = kryteria.kategoria == "it"
    adresy = []

    if "pracuj.pl" in domeny:
        for fraza in frazy[:1 if len(miasta) > 1 else 2]:
            kw = quote(fraza.lower())
            if miasta:
                adresy += [f"https://www.pracuj.pl/praca/{kw};kw/{slug(m)};wp" for m in miasta]
            else:
                adresy.append(f"https://www.pracuj.pl/praca/{kw};kw")

    if it and "justjoin.it" in domeny:
        for tech in technologie(kryteria)[:1]:
            if miasta:
                adresy += [f"https://justjoin.it/job-offers/{slug(m)}/{tech}" for m in miasta]
            else:
                adresy.append(f"https://justjoin.it/job-offers/all-locations/{tech}")

    if it and poczatkujacy and "theprotocol.it" in domeny:
        adresy += [f"https://theprotocol.it/filtry/junior;p/{slug(m)};wp" for m in miasta]

    if it and kryteria.zdalnie:
        tech = (technologie(kryteria) or [""])[0]
        if "remoteok.com" in domeny:
            slowa = (["junior"] if poczatkujacy else []) + ([tech] if tech else [])
            if slowa:
                adresy.append(f"https://remoteok.com/remote-{'+'.join(slowa)}-jobs")
        if poczatkujacy and "weworkremotely.com" in domeny:
            adresy.append("https://weworkremotely.com/remote-junior-jobs")

    return list(dict.fromkeys(adresy))[:limit]
