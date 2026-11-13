# Wersja z konsoli (szukaj.py): argumenty, pliki wynikowe, pamięć widzianych ofert.

import glob
import json
import sys
import zipfile

import pytest

import szukaj

from .conftest import UdawanyClaude


@pytest.fixture(autouse=True)
def bez_skutkow_ubocznych(monkeypatch, tmp_path):
    # mój prawdziwy widziane_oferty.json zostaje nietknięty, a arkusz nie otwiera się sam
    monkeypatch.setattr(szukaj, "PLIK_WIDZIANE", tmp_path / "widziane_oferty.json")
    monkeypatch.setattr(szukaj.os, "startfile", lambda sciezka: None, raising=False)


def uruchom(monkeypatch, claude, *argumenty):
    monkeypatch.setattr(sys, "argv", ["szukaj.py", *argumenty])
    return szukaj.main(claude.klient())


def test_szukanie_zapisuje_ods_i_json(monkeypatch, capsys, oferty_testowe):
    uruchom(monkeypatch, UdawanyClaude(oferty_testowe), "--stanowisko", "junior Python", "--kwalifikacje", "Python, Git")
    wyjscie = capsys.readouterr().out
    assert "Status: gotowe" in wyjscie and "Koszt:" in wyjscie

    dane = json.load(open(glob.glob("oferty_*.json")[0], encoding="utf-8"))
    assert {o["firma"] for o in dane} == {"Firma A", "Firma G"}
    assert "brakujace_wymagania" in dane[0]

    with zipfile.ZipFile(glob.glob("oferty_*.ods")[0]) as ods:
        assert ods.namelist()[0] == "mimetype"            # bez tego LibreOffice nie rozpozna pliku
        tresc = ods.read("content.xml").decode()
    assert "Otwórz ofertę" in tresc and "Koszty" in tresc


def test_drugie_uruchomienie_pomija_widziane(monkeypatch, capsys, oferty_testowe):
    uruchom(monkeypatch, UdawanyClaude(oferty_testowe), "--stanowisko", "junior Python")
    capsys.readouterr()
    uruchom(monkeypatch, UdawanyClaude(oferty_testowe), "--stanowisko", "junior Python")
    assert "pominięto 5 ofert już widzianych wcześniej" in capsys.readouterr().out


def test_zly_budzet_czytelny_blad(monkeypatch, capsys):
    with pytest.raises(SystemExit) as wyjscie:
        uruchom(monkeypatch, UdawanyClaude(), "--budzet", "5")
    assert wyjscie.value.code == 1 and "budzet_usd" in capsys.readouterr().out


def test_lista_kategorii(monkeypatch, capsys):
    with pytest.raises(SystemExit) as wyjscie:
        uruchom(monkeypatch, UdawanyClaude(), "--pomoc-kategorie")
    assert wyjscie.value.code == 0 and "logistyka" in capsys.readouterr().out
