from app.planering import CsvFel, likhet, liknande, tolka_csv

import pytest


def test_csv_semikolon_med_rubrik_och_flera_kolumner():
    data = "Huvudsökord;Sekundära\nventilation i gamla hus;självdrag, fukt;FTX\nvärmepump;luft-vatten|pris\n".encode()
    assert tolka_csv(data) == [
        ("ventilation i gamla hus", ["självdrag", "fukt", "FTX"]),
        ("värmepump", ["luft-vatten", "pris"]),
    ]


def test_csv_komma_och_windows_kodning():
    data = "tilläggsisolering,vind,kostnad\n\nfukt i källare,dränering\n".encode("cp1252")
    assert tolka_csv(data) == [("tilläggsisolering", ["vind", "kostnad"]), ("fukt i källare", ["dränering"])]


def test_csv_tom_fil_ger_fel():
    with pytest.raises(CsvFel):
        tolka_csv(b"   \n")


def test_likhet():
    assert likhet("värmepump", "värmepumpar") == 1.0
    assert likhet("ventilation i gamla hus", "ventilation gamla hus") == 1.0
    assert likhet("fukt i källare", "värmepump luft vatten") == 0.0


def test_liknande_markerar_bada_hallen():
    artiklar = [{"id": 1, "huvudsokord": "värmepump"}, {"id": 2, "huvudsokord": "värmepumpar pris"},
                {"id": 3, "huvudsokord": "fönsterbyte"}]
    res = liknande(artiklar)
    assert [x["id"] for x in res[1]] == [2]
    assert [x["id"] for x in res[2]] == [1]
    assert res[3] == []
