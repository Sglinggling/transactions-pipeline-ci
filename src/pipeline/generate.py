"""Générateur déterministe de fichiers CSV de transactions.

Deux exécutions avec les mêmes paramètres produisent des fichiers identiques
octet pour octet : seed fixe, dates de référence en dur, aucun appel à
datetime.now().

Cas de test garantis dans les données générées (avec n_files >= 2) :
- un montant nettement supérieur à 5000 (12500.00) ;
- un montant à 5000.00 pile et un à 5000.01 ;
- un IBAN émetteur présent dans tous les fichiers (RECURRING_SENDER) ;
- un IBAN à la fois émetteur et destinataire (RECURRING_SENDER) ;
- une banque avec plusieurs IBAN émetteurs (BNP Paribas).
"""

import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

COLUMNS = [
    "datetime_transaction",
    "iban_origine",
    "pays_source",
    "banque_source",
    "iban_destinataire",
    "pays_destinataire",
    "montant",
]

DEFAULT_SEED = 42
DEFAULT_N_FILES = 3
DEFAULT_OUTPUT_DIR = Path("data/incoming")
MIN_ROWS, MAX_ROWS = 5, 10

# Date de référence en dur : le fichier i couvre la journée BASE_DATE + i jours.
BASE_DATE = datetime(2025, 1, 15, 8, 0, 0)
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Pool restreint et cohérent : un IBAN a toujours le même pays et la même banque.
IBAN_POOL: dict[str, tuple[str, str]] = {
    "FR7630004000010000000000101": ("FR", "BNP Paribas"),
    "FR7630004000010000000000202": ("FR", "BNP Paribas"),
    "FR7630003000010000000000303": ("FR", "Societe Generale"),
    "DE89370400440532013000": ("DE", "Deutsche Bank"),
    "ES9121000418450200051332": ("ES", "Santander"),
    "IT60X0542811101000000123456": ("IT", "UniCredit"),
    "BE68539007547034": ("BE", "ING Belgique"),
}

RECURRING_SENDER = "FR7630004000010000000000101"  # BNP Paribas
SECOND_BNP_SENDER = "FR7630004000010000000000202"  # BNP Paribas
FOREIGN_SENDER = "DE89370400440532013000"

# (iban_origine, iban_destinataire, montant) injectés en round-robin sur les fichiers.
SPECIAL_TRANSACTIONS: list[tuple[str, str, str]] = [
    (RECURRING_SENDER, "ES9121000418450200051332", "12500.00"),
    (SECOND_BNP_SENDER, "IT60X0542811101000000123456", "5000.00"),
    (FOREIGN_SENDER, RECURRING_SENDER, "5000.01"),
]


def _random_amount(rng: random.Random) -> str:
    # Montants aléatoires < 5000 pour que seuls les cas injectés testent le seuil.
    cents = rng.randint(1_000, 499_999)
    return f"{cents // 100}.{cents % 100:02d}"


def _row(dt: datetime, origine: str, destinataire: str, montant: str) -> list[str]:
    pays_source, banque_source = IBAN_POOL[origine]
    pays_destinataire, _ = IBAN_POOL[destinataire]
    return [
        dt.strftime(DATETIME_FORMAT),
        origine,
        pays_source,
        banque_source,
        destinataire,
        pays_destinataire,
        montant,
    ]


def build_transactions(
    n_files: int = DEFAULT_N_FILES, seed: int = DEFAULT_SEED
) -> list[list[list[str]]]:
    """Construit en mémoire les lignes de chaque fichier (sans en-tête)."""
    if n_files < 2:
        raise ValueError("n_files doit être >= 2 pour couvrir les cas multi-fichiers")

    rng = random.Random(seed)
    ibans = sorted(IBAN_POOL)
    files: list[list[list[str]]] = []

    for file_idx in range(n_files):
        # Chaque fichier contient l'émetteur récurrent + sa part des cas spéciaux.
        pairs = [(RECURRING_SENDER, "BE68539007547034", _random_amount(rng))]
        pairs += [t for i, t in enumerate(SPECIAL_TRANSACTIONS) if i % n_files == file_idx]

        n_rows = rng.randint(max(MIN_ROWS, len(pairs)), MAX_ROWS)
        while len(pairs) < n_rows:
            origine, destinataire = rng.sample(ibans, 2)
            pairs.append((origine, destinataire, _random_amount(rng)))
        rng.shuffle(pairs)

        dt = BASE_DATE + timedelta(days=file_idx)
        rows = []
        for origine, destinataire, montant in pairs:
            dt += timedelta(minutes=rng.randint(5, 90), seconds=rng.randint(0, 59))
            rows.append(_row(dt, origine, destinataire, montant))
        files.append(rows)

    return files


def write_csv_files(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    n_files: int = DEFAULT_N_FILES,
    seed: int = DEFAULT_SEED,
) -> list[Path]:
    """Écrit les fichiers transactions_XXX.csv dans output_dir et renvoie leurs chemins."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, rows in enumerate(build_transactions(n_files, seed), start=1):
        path = output_dir / f"transactions_{idx:03d}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(COLUMNS)
            writer.writerows(rows)
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Génère des CSV de transactions déterministes.")
    parser.add_argument("--n-files", type=int, default=DEFAULT_N_FILES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    for path in write_csv_files(args.output_dir, args.n_files, args.seed):
        print(f"Généré : {path}")


if __name__ == "__main__":
    main()
