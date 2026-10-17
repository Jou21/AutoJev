"""Validate a user-provided image without changing its original bytes.

The upload is evidence chosen by the user, not a screenshot produced by the
browser executor. Never use a client filename or a data-URL MIME declaration
as proof that the payload is a safe raster image.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 30_000_000
MAX_IMAGE_DIMENSION = 16_384
MAX_IMAGE_DATA_URL_LENGTH = 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 64
_FORMATS = {
    "image/png": ("PNG", ".png"),
    "image/jpeg": ("JPEG", ".jpg"),
    "image/webp": ("WEBP", ".webp"),
}


class UploadedImageError(ValueError):
    """A recoverable attachment error; the checkpoint must remain paused."""


@dataclass(frozen=True)
class ValidatedUserImage:
    content: bytes
    mime_type: str
    extension: str
    width: int
    height: int
    sha256: str


def validate_user_image(data_url: str) -> ValidatedUserImage:
    if not isinstance(data_url, str) or len(data_url) > MAX_IMAGE_DATA_URL_LENGTH:
        raise UploadedImageError("A imagem deve ter no máximo 10 MB.")
    header, separator, encoded = data_url.partition(",")
    mime_type = header[5:-7] if header.startswith("data:") and header.endswith(";base64") else ""
    if not separator or mime_type not in _FORMATS or not encoded:
        raise UploadedImageError("Anexe uma imagem PNG, JPEG ou WebP válida.")
    if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise UploadedImageError("A imagem deve ter no máximo 10 MB.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UploadedImageError("Não foi possível ler a imagem. Cole ou escolha o arquivo novamente.") from exc
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise UploadedImageError("A imagem deve ter no máximo 10 MB.")
    expected_format, extension = _FORMATS[mime_type]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content), formats=[expected_format]) as image:
                width, height = image.size
                if (image.format != expected_format or width <= 0 or height <= 0
                        or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION
                        or width * height > MAX_IMAGE_PIXELS):
                    raise UploadedImageError("Use uma imagem de até 30 megapixels e 16.384 pixels por lado.")
                if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                    raise UploadedImageError("Use uma imagem estática. Imagens animadas não são aceitas.")
                image.verify()
            # Some formats only verify their headers. Decode once as well so
            # truncated/corrupt content cannot become an accepted attachment.
            with Image.open(io.BytesIO(content), formats=[expected_format]) as image:
                image.load()
    except UploadedImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise UploadedImageError("A imagem é grande demais. Use uma imagem de até 30 megapixels.") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError) as exc:
        raise UploadedImageError("A imagem está inválida ou não corresponde ao formato informado. Use PNG, JPEG ou WebP.") from exc
    return ValidatedUserImage(content, mime_type, extension, width, height, hashlib.sha256(content).hexdigest())
