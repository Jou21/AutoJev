"""Public, local-only fixtures shared by backend regression tests."""

import base64
import hashlib
from io import BytesIO

import pytest
from PIL import Image, ImageDraw


@pytest.fixture
def user_print_image():
    """An arbitrary image, deliberately unrelated to the tested web page."""
    image = Image.new("RGB", (240, 120), "#15364c")
    ImageDraw.Draw(image).text((12, 50), "Evidence from another application", fill="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    payload = buffer.getvalue()
    return {
        "bytes": payload,
        "data_url": "data:image/png;base64," + base64.b64encode(payload).decode("ascii"),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "width": 240,
        "height": 120,
        "mime_type": "image/png",
    }
