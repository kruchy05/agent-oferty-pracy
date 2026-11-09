# Pętla agenta zamknięta w obiekcie Przebieg - jedno wyszukiwanie = jeden obiekt z własnym stanem
# (licznik kosztów, portale, znalezione oferty). Dzięki temu dwa wyszukiwania z FastAPI nie psują sobie danych.
#
# Każda runda:
#   KROK 1 - ZNAJDŹ:      Claude szuka (web_search) i czyta strony (moje narzędzie pobierz_strone)
#   KROK 2 - UPORZĄDKUJ:  messages.parse zamienia znalezione oferty na obiekty Pydantic
#   KROK 3 - SPRAWDŹ:     Python odsiewa zły poziom, stare i wygasłe oferty

import datetime
import re
from concurrent.futures import ThreadPoolExecutor

import anthropic

from .config import ustawienia as domyslne_ustawienia
from .koszty import BudzetWyczerpany, Licznik
from .modele import DOZWOLONE_POZIOMY, ListaOfert
from .narzedzia import NARZEDZIE_POBIERZ, pobierz_strone, sprawdz_link
from .portale import LICZBA_GRUP, portale_dla
from .prompty import (SYSTEM_PORZADKOWANIE, SYSTEM_SZUKANIE, polecenie_porzadkowania,
                      polecenie_szukania)


def probkowanie(temperatura):
    # dodatkowe pola zapytania tylko wtedy, gdy temperatura jest ustawiona (patrz config.py)
    return {"extra_body": {"temperature": temperatura}} if temperatura is not None else {}


def odczytaj_date(tekst):
    try:
        data = datetime.date.fromisoformat(str(tekst).strip()[:10])
        return data if data <= datetime.date.today() + datetime.timedelta(days=1) else None
    except ValueError:
        return None


class Przebieg:
    """Jedno wyszukiwanie ofert.

    klient    - anthropic.Anthropic utworzony z klucza użytkownika (agent sam klucza nie zna)
    kryteria  - KryteriaSzukania z formularza albo z konsoli
    widziane  - linki, które użytkownik już widział (z pliku albo z bazy) - te pomijam
    na_postep - opcjonalna funkcja wołana po każdej rundzie (później: zapis postępu do bazy)
    """

    def __init__(self, klient, kryteria, widziane=frozenset(), ustawienia=None, na_postep=None):
        self.klient = klient
        self.kryteria = kryteria
        self.widziane = set(widziane)
        self.ust = ustawienia or domyslne_ustawienia
        self.na_postep = na_postep

        # budżet z formularza, ale nigdy powyżej limitu z ustawień serwera
        self.licznik = Licznik(self.ust.claude_model, min(kryteria.budzet_usd, self.ust.budzet_max_usd))
        self.portale = portale_dla(kryteria.kategoria, kryteria.zdalnie)   # własna kopia listy
        self.pobrane_aktywne = set()
        self.dobre = []       # (oferta, status) - dopisuję na bieżąco, żeby nic nie zginęło przy błędzie
        self.odrzucone = []
        self.runda = 0
        self.status = "nowy"  # nowy -> trwa -> gotowe / budzet / blad
        self.blad = None
        self.od_kiedy = datetime.date.today() - datetime.timedelta(days=self.ust.maks_wiek_dni)

    # ---------- uruchomienie ----------

    def uruchom(self):
        """Robi rundy, aż zbierze min_ofert albo skończy się budżet. Nie rzuca wyjątków - wynik jest w status."""
        self.status = "trwa"
        try:
            for runda in range(self.ust.max_rund):
                if len(self.dobre) >= self.ust.min_ofert or not self.portale:
                    break
                self.runda = runda + 1
                self._jedna_runda(runda)
                self._zglos_postep()
            self.status = "gotowe"
        except BudzetWyczerpany as e:
            print(f"\n[Koniec budżetu: {e}] - zostawiam to, co mam")
            self.status = "budzet"
            self.blad = str(e)
        except Exception as e:
            # raz mi wszystko przepadło przez błąd w połowie, więc teraz zostawiam to, co już mam
            print(f"\n[Przerwano z powodu błędu: {type(e).__name__}: {e}] - zostawiam to, co mam")
            self.status = "blad"
            self.blad = f"{type(e).__name__}: {e}"
        self._zglos_postep()
        return self

    def wyniki(self):
        return self.dobre[:self.ust.liczba_ofert]

    def wszystkie_linki(self):
        return {o.link for o, _ in self.dobre + self.odrzucone}

    def _zglos_postep(self):
        if self.na_postep:
            self.na_postep(self)

    def _jedna_runda(self, runda):
        domeny = self.portale[runda % LICZBA_GRUP::LICZBA_GRUP] or list(self.portale)
        print(f"\n=== Runda {runda + 1} (dobrych ofert: {len(self.dobre)}/{self.ust.min_ofert}, "
              f"koszt: {self.licznik.usd:.4f} $) - portale: {', '.join(domeny)} ===")

        print("KROK 1: szukam i czytam strony")
        tekst = self.krok1_znajdz(domeny)
        if not tekst.strip():
            print("  brak wyników w tej rundzie")
            return

        print("KROK 2: porządkuję oferty")
        oferty = self.krok2_uporzadkuj(tekst)
        print(f"  odczytano {len(oferty)} ofert")

        print("KROK 3: sprawdzam poziom, datę i czy oferty żyją")
        dobre, zle = self.krok3_sprawdz(oferty)
        self.dobre.extend(dobre)
        self.odrzucone.extend(zle)

    # ---------- KROK 1: ZNAJDŹ (pętla agenta z narzędziami) ----------

    def krok1_znajdz(self, domeny):
        k = self.kryteria
        narzedzia = [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": self.ust.max_wyszukiwan,
                "allowed_domains": list(domeny),
                # do user_location daję tylko pierwsze miasto (np. z "Legnica / Wrocław" -> "Legnica")
                "user_location": {"type": "approximate", "country": "PL", "timezone": "Europe/Warsaw",
                                  **({"city": re.split(r"[/,]", k.miasto)[0].strip()} if k.miasto else {})},
            },
            NARZEDZIE_POBIERZ,
        ]
        juz_mam = [o for o, _ in self.dobre + self.odrzucone]
        wiadomosci = [{"role": "user", "content": polecenie_szukania(
            k, domeny, self.od_kiedy, juz_mam, self.ust.max_wyszukiwan, self.ust.max_pobran_w_rundzie)}]
        zebrany_tekst = []
        pobrania = 0

        for proba in range(self.ust.max_prob_w_rundzie):
            self.licznik.pilnuj()
            try:
                odp = self.klient.messages.create(
                    model=self.ust.claude_model, max_tokens=4000, system=SYSTEM_SZUKANIE, tools=narzedzia,
                    messages=wiadomosci, **probkowanie(self.ust.temperatura_szukanie),
                )
            except anthropic.BadRequestError as e:
                # jak jakiś portal blokuje wyszukiwarkę, API odrzuca całe zapytanie - wywalam go i ponawiam
                dozwolone = narzedzia[0]["allowed_domains"]
                zablokowane = [d for d in re.findall(r"'([^']+)'", str(e)) if d in dozwolone]
                if "not accessible" in str(e) and zablokowane:
                    print(f"  [pomijam zablokowane portale: {', '.join(zablokowane)}]")
                    narzedzia[0]["allowed_domains"] = [d for d in dozwolone if d not in zablokowane]
                    self.portale = [p for p in self.portale if p not in zablokowane]
                    if narzedzia[0]["allowed_domains"]:
                        continue
                    return ""
                raise

            self.licznik.licz(odp.usage)
            wiadomosci.append({"role": "assistant", "content": odp.content})

            for blok in odp.content:
                if blok.type == "server_tool_use" and blok.name == "web_search":
                    print(f"     szukam: {blok.input.get('query')}")

            if odp.stop_reason == "tool_use":
                # model chce użyć MOJEGO narzędzia - wykonuję je i odsyłam wynik jako tool_result.
                # treść strony ląduje tylko w tool_result, nigdy w moich poleceniach
                wyniki = []
                for blok in odp.content:
                    if blok.type != "tool_use":
                        continue
                    if blok.name != "pobierz_strone":
                        tekst, blad = "Nieznane narzędzie.", True
                    elif pobrania >= self.ust.max_pobran_w_rundzie:
                        tekst, blad = "Limit pobrań stron w tej rundzie wyczerpany. Zakończ i podaj listę ofert.", True
                    else:
                        url = str(blok.input.get("url", ""))
                        tekst, blad, aktywna = pobierz_strone(url, self.portale, self.ust.max_znakow_strony)
                        pobrania += 1
                        if not tekst.startswith("Odmowa"):
                            self.licznik.pobrania += 1
                        if aktywna:
                            self.pobrane_aktywne.add(url)
                        print(f"     pobieram: {url[:90]}  {'BŁĄD' if blad else 'ok'}")
                    wyniki.append({"type": "tool_result", "tool_use_id": blok.id,
                                   "content": tekst, "is_error": blad})
                wiadomosci.append({"role": "user", "content": wyniki})
                continue

            if odp.stop_reason == "pause_turn":
                # API przerwało w trakcie wyszukiwania - odsyłam historię, żeby dokończył
                continue

            tekst = "".join(b.text for b in odp.content if b.type == "text")

            if odp.stop_reason == "max_tokens":
                # zabrakło miejsca na odpowiedź - zbieram to, co jest, i proszę o dokończenie
                zebrany_tekst.append(tekst)
                wiadomosci.append({"role": "user", "content": "Kontynuuj listę dokładnie od miejsca, w którym przerwałeś."})
                continue

            if odp.stop_reason == "refusal":
                print("  [model odmówił odpowiedzi w tej rundzie - pomijam ją]")
                return "\n".join(zebrany_tekst)

            zebrany_tekst.append(tekst)   # end_turn - gotowe
            return "\n".join(zebrany_tekst)

        print(f"  [limit {self.ust.max_prob_w_rundzie} prób w rundzie - biorę to, co jest]")
        return "\n".join(zebrany_tekst)

    # ---------- KROK 2: UPORZĄDKUJ (structured outputs) ----------
    # osobne wywołanie, bo structured outputs nie działają razem z cytowaniami z web_search

    def krok2_uporzadkuj(self, tekst_ofert):
        # dzielę na paczki po 8 ofert, żeby odpowiedź nigdy nie ucięła się w połowie JSON-a
        bloki = [b.strip() for b in re.split(r"\n\s*-{3,}\s*\n", tekst_ofert) if "link" in b.lower()]
        paczki = [bloki[i:i + 8] for i in range(0, len(bloki), 8)]
        oferty = []

        for paczka in paczki:
            self.licznik.pilnuj()
            try:
                odp = self.klient.messages.parse(
                    model=self.ust.claude_model, max_tokens=8000, system=SYSTEM_PORZADKOWANIE,
                    messages=[{"role": "user", "content": polecenie_porzadkowania(self.kryteria, paczka)}],
                    output_format=ListaOfert, **probkowanie(self.ust.temperatura_porzadkowanie),
                )
            except anthropic.APIError:
                raise
            except Exception as e:
                # niepoprawny JSON (np. ucięty) - parse rzuca wyjątek zanim zobaczę usage,
                # więc doliczam koszt "na zapas", żeby budżet się zgadzał
                print(f"  [nie udało się odczytać paczki ofert ({type(e).__name__}) - pomijam ją]")
                self.licznik.dolicz_szacunek(8000)
                continue

            self.licznik.licz(odp.usage)
            if odp.stop_reason == "refusal":
                print("  [model odmówił uporządkowania paczki - pomijam ją]")
                continue
            if odp.stop_reason == "max_tokens" or odp.parsed_output is None:
                print("  [odpowiedź ucięta - pomijam paczkę]")
                continue
            oferty += odp.parsed_output.oferty

        return oferty

    # ---------- KROK 3: SPRAWDŹ (zwykły Python, 0 tokenów) ----------

    def krok3_sprawdz(self, oferty):
        znane = self.widziane | self.wszystkie_linki()
        nowe, juz_widziane = [], 0
        for o in oferty:
            if o.link in znane or any(o.link == n.link for n in nowe):
                juz_widziane += 1
                continue
            nowe.append(o)
        if juz_widziane:
            # wcześniej te oferty znikały po cichu i nie było wiadomo, czemu jest ich tak mało
            print(f"  pominięto {juz_widziane} ofert już widzianych wcześniej")
        if not nowe:
            return [], []

        with ThreadPoolExecutor(max_workers=8) as pula:   # 8 linków naraz, inaczej trwa wieki
            statusy = list(pula.map(lambda o: sprawdz_link(o.link, self.pobrane_aktywne), nowe))

        dozwolone = DOZWOLONE_POZIOMY[self.kryteria.doswiadczenie]
        dobre, odrzucone = [], []
        powody = {"zły poziom": 0, "nie pasuje": 0, "wygasłe": 0, "za stare": 0, "bez daty i niesprawdzalne": 0}
        for o, status in zip(nowe, statusy):
            data = odczytaj_date(o.data_dodania)
            o.data_dodania = data.isoformat() if data else None
            if o.poziom != "nieznany" and o.poziom not in dozwolone:
                powod = "zły poziom"
            elif o.pasuje == "NIE PASUJE":
                powod = "nie pasuje"
            elif status == "wygasła":
                powod = "wygasłe"
            elif data and data < self.od_kiedy:
                powod = "za stare"
            elif not data and status != "aktywna":
                powod = "bez daty i niesprawdzalne"   # nie wiem, czy świeża, i nie mogę sprawdzić
            else:
                dobre.append((o, status))
                continue
            powody[powod] += 1
            odrzucone.append((o, status))

        print(f"  + {len(dobre)} dobrych | odrzucone: " + ", ".join(f"{k}: {v}" for k, v in powody.items() if v))
        return dobre, odrzucone
