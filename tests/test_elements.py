# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""The port, tested on the two things that make it a port rather than a rewrite:
the element shape a consumer reads, and the geometry it draws with."""

from __future__ import annotations

from swarm_doc_ingest.elements import to_elements
from swarm_doc_ingest.nv_ingest_schema import (
    ContentTypeEnum,
    TableFormatEnum,
    TextTypeEnum,
    validate_metadata,
)

from conftest import PAGE_HEIGHT, TABLE_ROWS


def extract(document, **kw):
    return to_elements(document, source_name="quarterly.pdf",
                       source_id="src-1", **kw)


def test_the_table_is_one_structured_element_and_not_prose(document):
    """The failure this whole component exists to avoid, asserted directly.

    A stack that reads the cells and concatenates them into a text element
    scores a perfect character error rate and is useless: the control
    measurement puts text-only extraction at 0 tables out of 19.
    """
    elements = extract(document)
    tables = [e for e in elements
              if e.content_metadata.type is ContentTypeEnum.STRUCTURED]
    assert len(tables) == 1
    table = tables[0]
    assert table.content_metadata.subtype == "table"
    assert table.table_metadata is not None
    assert table.table_metadata.table_format is TableFormatEnum.MARKDOWN

    # Every cell is in the structured element...
    for row in TABLE_ROWS:
        for cell in row:
            assert cell in table.table_metadata.table_content

    # ...and in none of the text elements.
    prose = " ".join(e.content for e in elements
                     if e.content_metadata.type is ContentTypeEnum.TEXT)
    assert "Critical" not in prose
    assert "Severity" not in prose


def test_content_and_table_content_are_the_same_markdown(document):
    """`content` is what a retriever embeds and `table_content` is what a
    citation renders. They have to be the same bytes, or a tenant reads one
    table and the model answered from another."""
    table = next(e for e in extract(document)
                 if e.content_metadata.type is ContentTypeEnum.STRUCTURED)
    assert table.content == table.table_metadata.table_content
    assert table.content.startswith("|")


def test_bounding_boxes_are_converted_to_a_top_left_origin(document):
    """Docling reports PDF coordinates, which grow upwards; the contract's
    consumer draws downwards. A box passed through unconverted highlights the
    mirror image of the citation."""
    table = next(e for e in extract(document)
                 if e.content_metadata.type is ContentTypeEnum.STRUCTURED)
    left, top, right, bottom = table.table_metadata.table_location
    # The fixture's table is at t=700, b=640 in PDF coordinates on an 842-high
    # page, so top-left origin makes it 142 .. 202 and top < bottom.
    assert top < bottom
    assert top == PAGE_HEIGHT - 700
    assert bottom == PAGE_HEIGHT - 640
    assert (left, right) == (60, 300)
    assert table.table_metadata.table_location_max_dimensions == (595.0, PAGE_HEIGHT)


def test_a_picture_is_an_image_element_and_claims_no_table_content(document):
    """Charts are located, not transcribed. Claiming `structured` here would
    tell a consumer there is a series to search."""
    picture = next(e for e in extract(document)
                   if e.content_metadata.type is ContentTypeEnum.IMAGE)
    assert picture.table_metadata is None
    assert picture.image_metadata is not None
    assert picture.image_metadata.image_location[0] == 60
    # Crops are opt-in; without them `content` is empty rather than absent.
    assert picture.content == ""


def test_image_content_is_opt_in(document):
    """`get_image` returns None unless the pipeline generated crops, so the
    element still has to be well-formed with the width and height unknown."""
    picture = next(e for e in extract(document, include_image_content=True)
                   if e.content_metadata.type is ContentTypeEnum.IMAGE)
    assert picture.image_metadata.width == 0  # no page image in a hand-built doc


def test_headings_are_headers_and_body_is_body(document):
    texts = [e for e in extract(document)
             if e.content_metadata.type is ContentTypeEnum.TEXT]
    assert texts[0].text_metadata.text_type is TextTypeEnum.HEADER
    assert texts[1].text_metadata.text_type is TextTypeEnum.BODY


def test_page_footers_and_blank_text_are_dropped(document):
    contents = [e.content for e in extract(document)]
    assert "Page 1 of 1" not in contents
    assert all(c.strip() or c == "" for c in contents)
    # heading + one paragraph + table + picture; footer and the blank gone
    assert len(contents) == 4


def test_every_element_carries_source_and_page_provenance(document):
    for element in extract(document):
        assert element.source_metadata.source_name == "quarterly.pdf"
        assert element.source_metadata.source_type == "pdf"
        assert element.content_metadata.page_number == 1
        assert element.content_metadata.hierarchy.page == 1
        assert element.content_metadata.hierarchy.page_count == 1
        assert element.total_pages == 1


def test_elements_round_trip_through_the_upstream_validator(document):
    """The acceptance criterion, as a test: a consumer written against
    nv-ingest's schema accepts what we emit."""
    for element in extract(document):
        payload = element.model_dump(mode="json", exclude_none=True)
        assert validate_metadata(payload) == element
