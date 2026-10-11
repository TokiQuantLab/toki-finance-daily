"""Shared brand image for portable HTML outputs."""
from __future__ import annotations

import base64
from pathlib import Path


def logo_data_uri() -> str:
    image = Path(__file__).with_name("assets") / "logo.png"
    return "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode("ascii")
