"""OCR (module d'I/O) : image en bytes -> texte brut, avec Tesseract.

Tesseract est une dépendance système (brew install tesseract / apt install
tesseract-ocr). Aucune interprétation du texte ici : voir extraction.py.
"""

import io

import pytesseract
from PIL import Image, UnidentifiedImageError

# --psm 6 : un bloc de texte uniforme (nos avis : lignes alignées à gauche).
# --dpi 300 : résolution du rendu (generate_images.DPI), évite l'estimation.
TESSERACT_CONFIG = "--psm 6 --dpi 300"
TESSERACT_LANG = "eng"


class OcrError(Exception):
    """Image illisible ou OCR impossible."""


def image_to_text(content: bytes, name: str) -> str:
    """Renvoie le texte brut reconnu dans l'image. name sert aux messages d'erreur."""
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise OcrError(f"{name} : image illisible ({exc})") from exc
    try:
        return pytesseract.image_to_string(image, lang=TESSERACT_LANG, config=TESSERACT_CONFIG)
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError(
            "Tesseract introuvable : installer tesseract (brew install tesseract, "
            "apt install tesseract-ocr)"
        ) from exc
    except pytesseract.TesseractError as exc:
        raise OcrError(f"{name} : échec de l'OCR ({exc})") from exc
