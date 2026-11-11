import re

import pytest

from pipeline.generate import COLUMNS, IBAN_POOL, build_transactions, write_csv_files

EXPECTED_HEADER = (
    "datetime_transaction,iban_origine,pays_source,banque_source,"
    "iban_destinataire,pays_destinataire,montant"
)


@pytest.fixture
def generated(tmp_path):
    paths = write_csv_files(tmp_path / "run", n_files=3, seed=42)
    return [p.read_text(encoding="utf-8").splitlines() for p in paths]


@pytest.fixture
def all_rows(generated):
    return [dict(zip(COLUMNS, line.split(","), strict=True)) for f in generated for line in f[1:]]


def test_two_runs_produce_identical_bytes(tmp_path):
    first = write_csv_files(tmp_path / "a", n_files=3, seed=42)
    second = write_csv_files(tmp_path / "b", n_files=3, seed=42)

    assert [p.name for p in first] == [p.name for p in second]
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes()


def test_build_transactions_is_deterministic():
    assert build_transactions(3, 42) == build_transactions(3, 42)


def test_known_output_snapshot(generated):
    # Garde-fou : détecte tout changement involontaire du générateur.
    assert generated[0][1] == (
        "2025-01-15 08:09:01,DE89370400440532013000,DE,Deutsche Bank,"
        "IT60X0542811101000000123456,IT,741.58"
    )
    assert [len(f) - 1 for f in generated] == [5, 10, 7]


def test_number_of_files_is_configurable(tmp_path):
    assert len(write_csv_files(tmp_path, n_files=5)) == 5


def test_rejects_less_than_two_files():
    with pytest.raises(ValueError):
        build_transactions(n_files=1)


def test_header_and_row_count(generated):
    for lines in generated:
        assert lines[0] == EXPECTED_HEADER
        assert 5 <= len(lines) - 1 <= 10


def test_amounts_have_two_decimals(all_rows):
    for row in all_rows:
        assert re.fullmatch(r"\d+\.\d{2}", row["montant"])


def test_iban_always_has_same_country_and_bank(all_rows):
    for row in all_rows:
        assert (row["pays_source"], row["banque_source"]) == IBAN_POOL[row["iban_origine"]]
        assert row["pays_destinataire"] == IBAN_POOL[row["iban_destinataire"]][0]


def test_mandatory_amounts_present(all_rows):
    amounts = {row["montant"] for row in all_rows}
    assert {"12500.00", "5000.00", "5000.01"} <= amounts


def test_sender_present_in_several_files(generated):
    senders_per_file = [{line.split(",")[1] for line in f[1:]} for f in generated]
    assert "FR7630004000010000000000101" in set.intersection(*senders_per_file)


def test_iban_both_sender_and_recipient(all_rows):
    senders = {row["iban_origine"] for row in all_rows}
    recipients = {row["iban_destinataire"] for row in all_rows}
    assert "FR7630004000010000000000101" in senders & recipients


def test_bank_with_several_sender_ibans(all_rows):
    bnp_senders = {r["iban_origine"] for r in all_rows if r["banque_source"] == "BNP Paribas"}
    assert bnp_senders == {"FR7630004000010000000000101", "FR7630004000010000000000202"}
