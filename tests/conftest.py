# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""Fixtures that build a `DoclingDocument` by hand.

The mapping in `elements.py` is tested against a document assembled here rather
than against one Docling produced from a PDF. Two reasons: the unit tests then
need none of the 536 MiB of baked weights, and a document built from known parts
lets a test assert the thing a real extraction cannot be made to guarantee — that
*this* table, with *these* cells, at *this* bounding box, arrives as a structured
element with its geometry converted.
"""

from __future__ import annotations

import pytest
from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)
from docling_core.types.doc.labels import DocItemLabel

PAGE_WIDTH, PAGE_HEIGHT = 595.0, 842.0


def prov(page_no: int, l: float, t: float, r: float, b: float) -> ProvenanceItem:
    """A PDF-coordinate box: the origin is bottom-left and `t` > `b`."""
    return ProvenanceItem(
        page_no=page_no,
        bbox=BoundingBox(l=l, t=t, r=r, b=b, coord_origin=CoordOrigin.BOTTOMLEFT),
        charspan=(0, 0),
    )


def grid(rows: list[list[str]]) -> TableData:
    cells = []
    for i, row in enumerate(rows):
        for j, text in enumerate(row):
            cells.append(TableCell(
                text=text, row_span=1, col_span=1,
                start_row_offset_idx=i, end_row_offset_idx=i + 1,
                start_col_offset_idx=j, end_col_offset_idx=j + 1,
                column_header=(i == 0),
            ))
    return TableData(num_rows=len(rows), num_cols=len(rows[0]), table_cells=cells)


TABLE_ROWS = [
    ["Severity", "Q1", "Q2"],
    ["Critical", "2", "1"],
    ["Major", "7", "9"],
]


@pytest.fixture
def document() -> DoclingDocument:
    doc = DoclingDocument(name="quarterly")
    doc.add_page(page_no=1, size=Size(width=PAGE_WIDTH, height=PAGE_HEIGHT))

    doc.add_heading(text="Quarterly Reliability Review", level=1,
                    prov=prov(1, 60, 800, 500, 780))
    doc.add_text(label=DocItemLabel.TEXT, text="Three incidents exceeded the threshold.",
                 prov=prov(1, 60, 760, 520, 740))
    doc.add_table(data=grid(TABLE_ROWS), prov=prov(1, 60, 700, 300, 640))
    doc.add_picture(prov=prov(1, 60, 600, 260, 500))
    # A footer repeated on every page is fifteen near-identical chunks competing
    # with the answer in a vector search, so the mapping drops it.
    doc.add_text(label=DocItemLabel.PAGE_FOOTER, text="Page 1 of 1",
                 prov=prov(1, 60, 40, 120, 30))
    doc.add_text(label=DocItemLabel.TEXT, text="   ", prov=prov(1, 60, 20, 70, 10))
    return doc
