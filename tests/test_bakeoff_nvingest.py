# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""The bake-off's reading of nv-ingest's own elements, tested without them.

The upstream adapter (`bakeoff/run_extract_nvingest.py`) is the other half of
SUP-883's comparison, and the half of it that can be wrong without anything
failing is the normalisation: an element shape misread turns into a score, and a
score nobody can see is wrong is worse than an error.

So the element mapping is kept free of any `nv_ingest_api` import and tested
here against elements of the shape upstream's stages emit — which is also the
only way to test the cases a real extraction cannot be made to produce on
demand: a chart beside a table, a table read without geometry, a stage that
returned no cells at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bakeoff"))

from run_extract_nvingest import normalise, pipe_grid, table_grid  # noqa: E402

MARKDOWN = "\n".join([
    "| Region | Revenue | Margin |",
    "| --- | --- | --- |",
    "| EMEA | 4.1 | 0.22 |",
    "| APAC | 2.7 | 0.19 |",
])

ROWS = [["Region", "Revenue", "Margin"], ["EMEA", "4.1", "0.22"], ["APAC", "2.7", "0.19"]]


def text_element(content, **content_metadata):
    return {"content": content,
            "content_metadata": {"type": "text", "page_number": 1, **content_metadata}}


def table_element(content, fmt="markdown", subtype="table"):
    return {"content": "",
            "content_metadata": {"type": "structured", "subtype": subtype,
                                 "page_number": 1},
            "table_metadata": {"table_content": content, "table_content_format": fmt,
                               "table_format": "markdown"}}


def test_a_markdown_table_becomes_a_grid_and_the_separator_is_not_a_row():
    assert pipe_grid(MARKDOWN) == ROWS


def test_a_row_without_outer_pipes_reads_the_same():
    assert pipe_grid("Region | Revenue\nEMEA | 4.1") == [["Region", "Revenue"],
                                                         ["EMEA", "4.1"]]


def test_an_empty_table_content_is_no_table_rather_than_a_crash():
    """A stage that answered with nothing has to score zero and keep the row:
    the alternative is a traceback that drops the document out of the sweep,
    which looks like a stack that was never measured rather than one that
    failed."""
    assert table_grid({"table_content": "", "table_content_format": "markdown"}) == []
    assert table_grid({}) == []


def test_the_simple_format_is_one_row_because_that_is_all_it_says():
    """`table_content_format: simple` is OCR text joined with spaces — every
    cell read and no geometry at all. Representing it as a grid would invent
    the structure the format is missing; one row scores on `cell_set_f1` and
    near zero on `table_f1`, which is exactly the distinction."""
    assert table_grid({"table_content": "Region Revenue EMEA 4.1",
                       "table_content_format": "simple"}) == [["Region Revenue EMEA 4.1"]]


def test_the_prose_is_the_text_elements_and_nothing_a_table_element_carries():
    """A table element contributes its grid and not a word of prose. What
    upstream's own text stage puts in the text element is upstream's business —
    at page depth that includes the characters inside the table, which neither
    metric is distorted by — but the structured element must not add to it a
    second time."""
    extraction = normalise([
        text_element("Three incidents exceeded the threshold."),
        table_element(MARKDOWN),
    ])
    assert extraction["tables"] == [ROWS]
    assert extraction["text"] == "Three incidents exceeded the threshold."


def test_a_table_flattened_into_prose_earns_no_table_credit():
    """The failure the whole bake-off exists to detect, asserted on this side of
    it too: a stack that read the cells and emitted them as text scores a
    perfect CER and zero tables, because table credit comes only from a
    structured element."""
    extraction = normalise([text_element(MARKDOWN)])
    assert extraction["tables"] == []
    assert "EMEA" in extraction["text"]


def test_a_chart_is_counted_and_not_scored_as_a_table():
    """Graphic Elements turns a chart into a table of series. The ground truth's
    tables are tables, and the open stack cites charts as images by design
    (SUP-874), so a chart must not compete for a table match in either
    direction."""
    extraction = normalise([
        table_element(MARKDOWN),
        table_element("| 2024 | 11 |", subtype="chart"),
        table_element("| 2025 | 14 |", subtype="infographic"),
    ])
    assert extraction["tables"] == [ROWS]
    assert extraction["_charts"] == 2


class _Enum(str):
    """Upstream hands back `ContentTypeEnum.TEXT` from one stage and `"text"`
    from another; both have to mean the same thing here."""

    @property
    def value(self):
        return str(self)


@pytest.mark.parametrize("content_type", ["text", _Enum("text"), "TEXT"])
def test_the_element_type_is_read_whether_it_is_an_enum_or_a_string(content_type):
    element = text_element("Prose.")
    element["content_metadata"]["type"] = content_type
    assert normalise([element])["text"] == "Prose."


def test_an_image_element_contributes_nothing():
    extraction = normalise([{"content": "<base64>",
                             "content_metadata": {"type": "image"},
                             "image_metadata": {"image_type": "png"}}])
    assert extraction == {"text": "", "tables": [], "_charts": 0}


def test_whitespace_only_prose_is_dropped():
    assert normalise([text_element("   ")])["text"] == ""
