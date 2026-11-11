"""Générateur déterministe de textes .txt : une transaction décrite en français.

Formulations variées (ordre des informations, formats de date, d'heure et de
montant) pour éprouver l'extraction par SLM. Aucun aléa, aucune date
courante : deux exécutions produisent des fichiers identiques octet pour octet.

Cas de test garantis :
- montants > 5000 (6200 €, 7 300,50 €) et <= 5000 (1 875,40 euros, 5000.00 EUR pile) ;
- l'IBAN FR7630004000010000000000101 et la banque BNP Paribas, déjà présents
  dans les CSV et les images (cumul des totaux entre les trois sources).
"""

import argparse
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path("data/incoming")

TEXTS = [
    # Ordre chronologique, date en toutes lettres, heure « 14h30 », IBAN groupés.
    "Le 12 septembre 2026 à 14h30, le compte FR76 3000 4000 0100 0000 0000 101 de la "
    "banque BNP Paribas (France) a envoyé 6200 € vers le compte "
    "DE89 3704 0044 0532 0130 00 en Allemagne.",
    # Destinataire d'abord, date JJ/MM/AAAA, montant à virgule en « euros ».
    "Un virement de 1 875,40 euros a été crédité le 03/10/2026 à 09:05 sur le compte "
    "IT60X0542811101000000123456, domicilié en Italie. L'émetteur est le compte "
    "ES9121000418450200051332 de la banque Santander, en Espagne.",
    # Style relevé : date ISO, montant à point en « EUR », champs étiquetés.
    "Ordre de virement exécuté le 2026-10-15 à 16:45. Montant : 5000.00 EUR. "
    "Compte débité : BE68 5390 0754 7034 (ING Belgique, Belgique). "
    "Compte crédité : FR76 3000 4000 0100 0000 0000 202 (France).",
    # La banque en sujet, date ordinale « 1er », heure en fin de phrase.
    "Deutsche Bank (Allemagne) a transféré 7 300,50 € depuis le compte "
    "DE89370400440532013000 vers le compte FR7630003000010000000000303, ouvert en "
    "France. L'opération date du 1er novembre 2026, 8h15.",
]


def write_texts(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[Path]:
    """Écrit message_XXX.txt (UTF-8) dans output_dir et renvoie leurs chemins."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, text in enumerate(TEXTS, start=1):
        path = output_dir / f"message_{idx:03d}.txt"
        path.write_bytes((text + "\n").encode("utf-8"))
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Génère des textes de transactions.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    for path in write_texts(args.output_dir):
        print(f"Généré : {path}")


if __name__ == "__main__":
    main()
