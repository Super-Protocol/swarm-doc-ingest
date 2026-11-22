# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""Turn a `DoclingDocument` into nv-ingest elements.

This module is the port. Everything else in the service is plumbing around it:
the extraction stack changed, and this is where its output is bent back into the
shape the blueprint's retrieval stage already reads.

Two decisions worth knowing before reading the code.

**A table's cells are not flattened into its prose.** A `TableItem` becomes one
element of ``type: structured``, ``subtype: table``, whose `content` is the
Markdown rendering — so a retriever embeds something a model reads well — and
whose `table_metadata.table_content` is the same Markdown beside the cell
geometry. The grid is never concatenated into a text element, because a table
read as prose is the failure the whole issue exists to avoid, and the control
measurement puts it at 0 tables out of 19.

**Bounding boxes are converted to a top-left origin.** Docling reports PDF
coordinates, which grow upwards. nv-ingest's `(x1, y1, x2, y2)` are read by a
viewer that draws downwards, so a box passed through unconverted highlights the
mirror image of the citation.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

from docling_core.types.doc import (
    DoclingDocument,
    PictureItem,
    TableItem,
    TextItem,
)
from docling_core.types.doc.labels import DocItemLabel

from .config import SUPPORTED_SUFFIXES
from .nv_ingest_schema import (
    ContentHierarchySchema,
    ContentMetadataSchema,
    ContentSubtypeEnum,
    ContentTypeEnum,
    ImageMetadataSchema,
    MetadataSchema,
    SourceMetadataSchema,
    TableFormatEnum,
    TableMetadataSchema,
    TextMetadataSchema,
    TextTypeEnum,
)

log = logging.getLogger(__name__)

#: Which Docling labels count as a heading rather than body text. The retrieval
#: stage does not branch on this today, but `text_type` is in the contract and a
#: wrong constant there is worse than a coarse one.
HEADER_LABELS = {
    DocItemLabel.TITLE,
    DocItemLabel.SECTION_HEADER,
    DocItemLabel.PAGE_HEADER,
}

#: Labels we deliberately drop. A page footer repeated on every page of a
#: fifteen-page document is fifteen near-identical chunks competing with the
#: answer in a vector search.
DROPPED_LABELS = {DocItemLabel.PAGE_FOOTER}


def source_type(name: str) -> str:
    return SUPPORTED_SUFFIXES.get(Path(name).suffix.lower(), "unknown")


def _page_size(doc: DoclingDocument, page_no: int) -> tuple[float, float]:
    page = doc.pages.get(page_no) if doc.pages else None
    if page is None or page.size is None:
        return (0.0, 0.0)
    return (float(page.size.width), float(page.size.height))


def _location(item, doc: DoclingDocument) -> tuple[tuple, tuple, int]:
    """`(bbox, page max dimensions, page number)`, top-left origin.

    Returns the contract's zero box for an item with no provenance — a DOCX or
    an HTML page has no geometry at all, and inventing one would put a citation
    marker on a page that does not exist.
    """
    prov = getattr(item, "prov", None)
    if not prov:
        return ((0, 0, 0, 0), (0, 0), -1)
    p = prov[0]
    width, height = _page_size(doc, p.page_no)
    box = p.bbox.to_top_left_origin(page_height=height) if height else p.bbox
    return ((box.l, box.t, box.r, box.b), (width, height), p.page_no)


def _source(name: str, source_id: str, location: str) -> SourceMetadataSchema:
    return SourceMetadataSchema(
        source_name=name,
        source_id=source_id,
        source_location=location,
        source_type=source_type(name),
    )


def _caption(item, doc: DoclingDocument) -> str:
    try:
        return item.caption_text(doc) or ""
    except Exception:                       # a caption is never worth a 500
        log.debug("caption unavailable for %s", type(item).__name__, exc_info=True)
        return ""


def _png_base64(item: PictureItem, doc: DoclingDocument) -> tuple[str, int, int]:
    image = item.get_image(doc)
    if image is None:
        return ("", 0, 0)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return (base64.b64encode(buf.getvalue()).decode("ascii"), image.width, image.height)


def to_elements(
    doc: DoclingDocument,
    *,
    source_name: str,
    source_id: str,
    source_location: str = "",
    include_image_content: bool = False,
) -> list[MetadataSchema]:
    """The whole mapping. Order follows reading order, which is what Docling
    iterates in, so an element's position in the list is usable as a tie-break
    by a consumer that wants adjacent context."""
    total_pages = doc.num_pages() or None
    out: list[MetadataSchema] = []

    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)
        if label in DROPPED_LABELS:
            continue

        bbox, max_dims, page_no = _location(item, doc)
        hierarchy = ContentHierarchySchema(
            page_count=total_pages if total_pages is not None else -1,
            page=page_no,
        )
        src = _source(source_name, source_id, source_location)

        if isinstance(item, TableItem):
            markdown = item.export_to_markdown(doc)
            caption = _caption(item, doc)
            out.append(MetadataSchema(
                # `content` is what gets embedded. Markdown rather than the raw
                # grid: a model reads a Markdown table, and the grid is still
                # one field away for whoever renders the citation.
                content=markdown,
                source_metadata=src,
                content_metadata=ContentMetadataSchema(
                    type=ContentTypeEnum.STRUCTURED,
                    subtype=ContentSubtypeEnum.TABLE,
                    page_number=page_no,
                    hierarchy=hierarchy,
                    description=caption,
                ),
                table_metadata=TableMetadataSchema(
                    caption=caption,
                    table_format=TableFormatEnum.MARKDOWN,
                    table_content=markdown,
                    table_content_format=TableFormatEnum.MARKDOWN,
                    table_location=bbox,
                    table_location_max_dimensions=max_dims,
                ),
                total_pages=total_pages,
            ))
            continue

        if isinstance(item, PictureItem):
            caption = _caption(item, doc)
            content, width, height = (
                _png_base64(item, doc) if include_image_content else ("", 0, 0))
            out.append(MetadataSchema(
                content=content,
                source_metadata=src,
                content_metadata=ContentMetadataSchema(
                    type=ContentTypeEnum.IMAGE,
                    page_number=page_no,
                    hierarchy=hierarchy,
                    description=caption,
                ),
                image_metadata=ImageMetadataSchema(
                    # Not `structured`: this stage locates a chart and does not
                    # transcribe its series. Claiming a subtype we cannot read
                    # would tell a consumer there is table content to search.
                    image_type=source_type(source_name),
                    caption=caption,
                    image_location=bbox,
                    image_location_max_dimensions=max_dims,
                    width=width,
                    height=height,
                ),
                total_pages=total_pages,
            ))
            continue

        if isinstance(item, TextItem):
            text = (item.text or "").strip()
            if not text:
                continue
            out.append(MetadataSchema(
                content=text,
                source_metadata=src,
                content_metadata=ContentMetadataSchema(
                    type=ContentTypeEnum.TEXT,
                    page_number=page_no,
                    hierarchy=hierarchy,
                ),
                text_metadata=TextMetadataSchema(
                    text_type=(TextTypeEnum.HEADER if label in HEADER_LABELS
                               else TextTypeEnum.BODY),
                    text_location=bbox,
                    text_location_max_dimensions=max_dims,
                ),
                total_pages=total_pages,
            ))

    return out
