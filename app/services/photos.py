"""Photo records and a stub of the cleanup pipeline.

Avito penalizes fake photos. A synthetic image stays marked as a render.
This module does not crop, retouch, or generate pixels.
"""

from __future__ import annotations

from app.models import ListingPhoto, PhotoKind


def process_photo_stub(photo: ListingPhoto) -> ListingPhoto:
    """Mark the card as passed through a pipeline that is not implemented yet."""
    photo.cleanup_status = "stub_processed"
    if photo.kind == PhotoKind.SYNTHETIC:
        photo.processing_note = (
            "Заглушка пайплайна: кадрирование не выполнялось. "
            "Это синтетический рендер, его нельзя выдавать за живое фото."
        )
    else:
        photo.processing_note = "Заглушка пайплайна: очистка и кадрирование не выполнялись."
    return photo


def main_photo(photos: list[ListingPhoto]) -> ListingPhoto | None:
    if not photos:
        return None
    for photo in photos:
        if photo.is_main:
            return photo
    return photos[0]
