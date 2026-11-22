# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""The Docling converter, built once and pinned.

Every option that is set here is set because leaving it at its default either
reaches the network, costs a model we do not bake, or changes a number the
bake-off measured. The choices and what each one cost are in
`bakeoff/README.md`; the short version:

* layout ``heron`` — the only preset of five that reads a table drawn without
  ruling lines as a table rather than as prose (0.968 against 0.000);
* TableFormer ``accurate`` — 0.956 against 0.491 for ``fast`` on a 21-row table;
* RapidOCR — character error rate 0.0000 against EasyOCR's 0.012–0.020, at
  2.0 GiB of memory against 5.1 GiB.
"""

from __future__ import annotations

import functools
import logging

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    LayoutObjectDetectionOptions,
    PdfPipelineOptions,
    RapidOcrOptions,
    TableFormerMode,
)
from docling.document_converter import (
    DocumentConverter,
    ImageFormatOption,
    PdfFormatOption,
)

from .config import Settings

log = logging.getLogger(__name__)


def _pdf_options(s: Settings, *, force_ocr: bool, want_images: bool) -> PdfPipelineOptions:
    o = PdfPipelineOptions()
    o.artifacts_path = str(s.artifacts_path)
    o.layout_options = LayoutObjectDetectionOptions.from_preset(s.layout_preset)
    o.do_table_structure = True
    o.table_structure_options.mode = TableFormerMode(s.table_mode)
    # Cell matching maps the PDF's own text runs onto the predicted grid instead
    # of re-reading the cells. On a document with a text layer that is both
    # faster and exact.
    o.table_structure_options.do_cell_matching = True

    if s.ocr_engine == "off":
        o.do_ocr = False
    else:
        o.do_ocr = True
        o.ocr_options = RapidOcrOptions(
            lang=["en"],
            # Paths are not given here on purpose: Docling resolves RapidOCR's
            # three ONNX files under `artifacts_path` and raises if any is
            # missing. That refusal is the guarantee — a stack that silently
            # downloaded a recogniser would pass every test here and send a
            # page of a tenant's document to a CDN in the cluster.
            force_full_page_ocr=force_ocr,
        )

    # Enrichment models we do not bake, each off explicitly so that a Docling
    # release which flips a default cannot make this image reach for a weight
    # it does not have.
    o.do_code_enrichment = False
    o.do_formula_enrichment = False
    o.do_picture_classification = False
    o.do_picture_description = False

    # Charts are located, not transcribed (SUP-874, accepted by the CTO): the
    # crop is what a citation shows. Generating it costs memory, so it is only
    # on when the caller asked for image content.
    o.generate_picture_images = want_images
    o.images_scale = 2.0
    return o


@functools.lru_cache(maxsize=4)
def converter(settings_key: tuple, *, force_ocr: bool, want_images: bool) -> DocumentConverter:
    """One converter per distinct option set, kept warm.

    Construction loads three models, which is seconds; doing it per request
    would dominate the 0.87 s a page the extraction itself costs.
    """
    s = Settings()
    o = _pdf_options(s, force_ocr=force_ocr, want_images=want_images)
    log.info(
        "building converter: layout=%s table=%s ocr=%s force_ocr=%s images=%s",
        s.layout_preset, s.table_mode, s.ocr_engine, force_ocr, want_images)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=o),
            # An image is a one-page scan: same layout, table and OCR stages,
            # through the backend that expects pixels rather than a PDF.
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=o),
        }
    )


def get_converter(*, force_ocr: bool, want_images: bool) -> DocumentConverter:
    s = Settings()
    key = (str(s.artifacts_path), s.layout_preset, s.table_mode, s.ocr_engine)
    return converter(key, force_ocr=force_ocr, want_images=want_images)
