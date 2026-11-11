import io

from PIL import Image

from pipeline.generate_images import (
    DPI,
    HEIGHT,
    IMAGE_TRANSACTIONS,
    WIDTH,
    render_notice,
    write_images,
)


def test_two_runs_produce_identical_bytes(tmp_path):
    first = write_images(tmp_path / "a")
    second = write_images(tmp_path / "b")

    assert [p.name for p in first] == ["virement_001.png", "virement_002.png", "virement_003.png"]
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes()


def test_fixed_size_resolution_and_black_on_white():
    image = Image.open(io.BytesIO(render_notice(IMAGE_TRANSACTIONS[0])))

    assert image.format == "PNG"
    assert image.size == (WIDTH, HEIGHT) == (1800, 900)
    assert image.mode == "L"
    assert tuple(round(d) for d in image.info["dpi"]) == (DPI, DPI) == (300, 300)
    # Rendu binaire : uniquement du blanc (fond) et du noir (texte).
    assert {value for _, value in image.getcolors()} == {0, 255}


def test_mandatory_test_cases_in_data():
    amounts = [t["montant"] for t in IMAGE_TRANSACTIONS]
    assert amounts == ["7350,50 EUR", "5000,00 EUR", "1250,75 EUR"]
    # IBAN émetteur et banque déjà présents dans les CSV : cumul entre sources.
    assert IMAGE_TRANSACTIONS[0]["iban_origine"] == "FR7630004000010000000000101"
    assert IMAGE_TRANSACTIONS[0]["banque_source"] == "BNP Paribas"


def test_missing_field_is_not_printed():
    fields = {k: v for k, v in IMAGE_TRANSACTIONS[0].items() if k != "montant"}
    assert render_notice(fields) != render_notice(IMAGE_TRANSACTIONS[0])
