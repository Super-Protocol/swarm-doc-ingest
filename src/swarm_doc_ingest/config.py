# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""Configuration, and the pins it refuses to run without.

Everything here comes from the environment, because everything here is rendered
into the chart's ConfigMap and therefore into the deployment's signed evidence.
The one exception is the API key, which arrives through a Secret: a command line
and a ConfigMap are both published in clear in the evidence snapshot.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: The model set this build was tested with, pinned by the Hugging Face commit
#: rather than by `main`. Docling's own model specs say ``revision="main"``,
#: which is mutable; a mutable revision anywhere near an attested render means
#: the evidence names bytes that can change under it. These are the revisions
#: baked into the image, and `/v1/config` reports them so a deployment can be
#: asked what it is actually running.
PINNED_MODELS: dict[str, dict[str, str]] = {
    "layout": {
        "repo": "docling-project/docling-layout-heron",
        "revision": "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
        "licence": "Apache-2.0",
    },
    "table_structure": {
        "repo": "docling-project/docling-models",
        "revision": "2199320848bb9a8a519d22e4b528185a4f9a6f64",
        "licence": "CDLA-Permissive-2.0 AND Apache-2.0",
    },
    "ocr": {
        "repo": "RapidAI/RapidOCR",
        "revision": "PP-OCRv6 det/rec/cls ONNX, as published by docling-tools",
        "licence": "Apache-2.0",
    },
}

#: Document formats this service accepts. Anything else is a 415 rather than a
#: silent empty extraction, because "the file produced no elements" and "we do
#: not read this format" are different answers to a tenant.
SUPPORTED_SUFFIXES: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".txt": "txt",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".bmp": "bmp",
}


#: Where a font may live. A module constant so a test can point it somewhere
#: empty; there is no reason to make this configurable in a deployment.
FONT_ROOTS = (Path("/usr/share/fonts"), Path("/usr/local/share/fonts"))

FONT_SUFFIXES = (".ttf", ".ttc", ".otf", ".pfb")


def _has_fonts(roots: tuple[Path, ...] = FONT_ROOTS) -> bool:
    """Whether the renderer has any font to substitute with.

    Checked at boot because the failure mode is silent: extraction still
    succeeds, the text layer still reads, and only the extraction numbers
    quietly drop.
    """
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() in FONT_SUFFIXES:
                return True
    return False


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """Read from the environment on every construction, never at import.

    Every field uses ``default_factory``. A bare ``os.environ.get(...)`` as a
    dataclass default is evaluated once when this module is imported, which
    makes the value a property of import order rather than of the environment —
    and in a test, of whichever test imported first. The bug is invisible in
    production, where uvicorn imports after the pod's environment is set, and
    that is exactly what makes it worth closing here.
    """

    #: Where the baked model weights live. Set, never derived: an unset
    #: artifacts path is what makes Docling reach for the Hugging Face Hub.
    artifacts_path: Path = field(
        default_factory=lambda: Path(os.environ.get(
            "DOCLING_ARTIFACTS_PATH", "/opt/docling-models")))
    layout_preset: str = field(
        default_factory=lambda: os.environ.get("INGEST_LAYOUT_PRESET",
                                               "layout_heron_default"))
    table_mode: str = field(
        default_factory=lambda: os.environ.get("INGEST_TABLE_MODE", "accurate"))
    ocr_engine: str = field(
        default_factory=lambda: os.environ.get("INGEST_OCR_ENGINE", "rapidocr"))
    #: OCR costs ~3.5 s a page and is pointless on a document that already has a
    #: text layer, so Docling's own "only where there is no text" default stands
    #: and full-page OCR is opt-in per request.
    force_full_page_ocr: bool = field(
        default_factory=lambda: _flag("INGEST_FORCE_FULL_PAGE_OCR", False))
    max_upload_bytes: int = field(
        default_factory=lambda: int(os.environ.get(
            "INGEST_MAX_UPLOAD_BYTES", str(256 * 1024 * 1024))))
    #: Image crops are returned base64 in `content` only when asked for: a
    #: fifteen-page catalogue of pictures is tens of megabytes of JSON.
    include_image_content: bool = field(
        default_factory=lambda: _flag("INGEST_INCLUDE_IMAGE_CONTENT", False))
    api_key: str = field(
        default_factory=lambda: os.environ.get("INGEST_API_KEY", ""))

    def check(self) -> None:
        """Fail at boot rather than at the first request.

        Every one of these is a condition the acceptance criteria name: the
        weights are present locally, the hub is off, and the key exists. A
        service that starts and then 500s on the first document looks like a
        model problem and is a configuration problem.
        """
        if not self.artifacts_path.is_dir():
            raise RuntimeError(
                f"model artifacts not found at {self.artifacts_path}. This image "
                "bakes them; an empty path means the mount shadowed them.")
        if os.environ.get("HF_HUB_OFFLINE") != "1":
            raise RuntimeError(
                "HF_HUB_OFFLINE must be 1. Documents are the sensitive asset "
                "here and no model may be fetched at request time.")
        if self.table_mode not in ("fast", "accurate"):
            raise RuntimeError(f"unknown table mode {self.table_mode!r}")
        if self.ocr_engine not in ("rapidocr", "off"):
            raise RuntimeError(
                f"unknown OCR engine {self.ocr_engine!r}; this image bakes "
                "RapidOCR only")
        if not _has_fonts(FONT_ROOTS):
            raise RuntimeError(
                "no fonts found under /usr/share/fonts. The PDF base-14 fonts "
                "are not embedded by specification, so a renderer with no font "
                "to substitute produces a different page image — and the layout "
                "model and OCR read that image, not the text layer. Measured: 0 "
                "of 2 tables without fonts, 2 of 2 with them. This image "
                "installs fonts-liberation; an empty font path means a base-image "
                "change dropped them.")
        if not self.api_key:
            raise RuntimeError(
                "INGEST_API_KEY is empty. A namespace is not a trust boundary "
                "once a second workload is in it.")

