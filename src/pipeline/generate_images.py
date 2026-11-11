"""Générateur déterministe d'images PNG d'avis de virement (une transaction par image).

Rendu reproductible : police embarquée (DejaVu Sans Mono, voir
fonts/LICENSE-DejaVu.txt), taille et résolution fixes, fond blanc, texte noir
sans anticrénelage (pixels 0 ou 255 uniquement). Aucune date courante, aucun
aléa : deux exécutions produisent des fichiers identiques octet pour octet.

Cas de test garantis :
- un montant > 5000 (7350,50) et deux montants <= 5000, dont 5000,00 pile ;
- un IBAN émetteur et une banque déjà présents dans les CSV
  (FR7630004000010000000000101, BNP Paribas) pour vérifier le cumul des totaux ;
- montants écrits avec une virgule décimale et suffixe EUR, IBAN groupés par
  4 caractères : l'extraction doit normaliser.
"""

import argparse
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pipeline.extraction import NOTICE_LABELS

FONT_PATH = Path(__file__).parent / "fonts" / "DejaVuSansMono.ttf"
# Taille et rendu choisis en comparant Tesseract 5.5.4 (brew) et 5.3.4 (Ubuntu
# 24.04) : 48 px binaire est le seul réglage sans aucune erreur sur les deux
# (il passe aussi en CI : Ubuntu 26.04, Tesseract 5.5.0).
# À 40 px, le zéro pointé de la police est parfois lu comme un 9.
FONT_SIZE = 48  # px
DPI = 300
WIDTH, HEIGHT = 1800, 900  # fixes : 9 lignes au maximum
MARGIN_X, MARGIN_Y, LINE_HEIGHT = 60, 60, 84
TITLE = "AVIS DE VIREMENT"
DEFAULT_OUTPUT_DIR = Path("data/incoming")

# Valeurs telles qu'imprimées sur les avis (avant normalisation).
IMAGE_TRANSACTIONS: list[dict[str, str]] = [
    {
        "datetime_transaction": "2025-01-18 09:15:00",
        "iban_origine": "FR7630004000010000000000101",
        "pays_source": "FR",
        "banque_source": "BNP Paribas",
        "iban_destinataire": "DE89370400440532013000",
        "pays_destinataire": "DE",
        "montant": "7350,50 EUR",
    },
    {
        "datetime_transaction": "2025-01-18 10:40:00",
        "iban_origine": "ES9121000418450200051332",
        "pays_source": "ES",
        "banque_source": "Santander",
        "iban_destinataire": "FR7630004000010000000000202",
        "pays_destinataire": "FR",
        "montant": "5000,00 EUR",
    },
    {
        "datetime_transaction": "2025-01-18 14:05:30",
        "iban_origine": "BE68539007547034",
        "pays_source": "BE",
        "banque_source": "ING Belgique",
        "iban_destinataire": "IT60X0542811101000000123456",
        "pays_destinataire": "IT",
        "montant": "1250,75 EUR",
    },
]


def _display_value(field: str, value: str) -> str:
    if field.startswith("iban_"):
        # Présentation usuelle d'un IBAN : groupes de 4 caractères.
        return " ".join(value[i : i + 4] for i in range(0, len(value), 4))
    return value


def render_notice(fields: dict[str, str]) -> bytes:
    """Rend un avis de virement en PNG. fields : {colonne: valeur affichée}.

    Les colonnes absentes de fields ne sont pas imprimées (utile pour tester
    un champ manquant).
    """
    font = ImageFont.truetype(str(FONT_PATH), FONT_SIZE)
    image = Image.new("L", (WIDTH, HEIGHT), color=255)
    draw = ImageDraw.Draw(image)
    draw.fontmode = "1"  # pas d'anticrénelage : rendu binaire, reproductible

    lines = [TITLE, ""] + [
        f"{NOTICE_LABELS[field]} : {_display_value(field, value)}"
        for field, value in fields.items()
        if field in NOTICE_LABELS
    ]
    for idx, line in enumerate(lines):
        draw.text((MARGIN_X, MARGIN_Y + idx * LINE_HEIGHT), line, font=font, fill=0)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(DPI, DPI), optimize=False)
    return buffer.getvalue()


def write_images(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[Path]:
    """Écrit virement_XXX.png dans output_dir et renvoie leurs chemins."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, fields in enumerate(IMAGE_TRANSACTIONS, start=1):
        path = output_dir / f"virement_{idx:03d}.png"
        path.write_bytes(render_notice(fields))
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Génère des images d'avis de virement.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    for path in write_images(args.output_dir):
        print(f"Généré : {path}")


if __name__ == "__main__":
    main()
