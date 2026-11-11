from pipeline.generate_texts import TEXTS, write_texts


def test_two_runs_produce_identical_bytes(tmp_path):
    first = write_texts(tmp_path / "a")
    second = write_texts(tmp_path / "b")

    assert [p.name for p in first] == [f"message_00{i}.txt" for i in range(1, 5)]
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes()


def test_texts_are_utf8_french(tmp_path):
    content = write_texts(tmp_path)[1].read_text(encoding="utf-8")
    assert "crédité" in content


def test_mandatory_test_cases_in_data():
    joined = " ".join(TEXTS)
    for amount in ["6200 €", "7 300,50 €", "1 875,40 euros", "5000.00 EUR"]:
        assert amount in joined
    # IBAN et banque communs aux CSV, aux images et aux textes.
    assert "FR76 3000 4000 0100 0000 0000 101" in TEXTS[0]
    assert "BNP Paribas" in TEXTS[0]


def test_formulations_vary():
    date_formats = ["12 septembre 2026", "03/10/2026", "2026-10-15", "1er novembre 2026"]
    for text, date_format in zip(TEXTS, date_formats, strict=True):
        assert date_format in text
