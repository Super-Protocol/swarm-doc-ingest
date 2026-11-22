# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""The wire contract, held to upstream's shape rather than to ours.

Every field asserted here is one the blueprint's retrieval stage dereferences in
`rag_server/response_generator.py`. A rename that looked harmless inside this
repository would make a consumer that reads nv-ingest's output read `None` from
ours, silently, for every citation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from swarm_doc_ingest.nv_ingest_schema import (
    ContentMetadataSchema,
    ContentTypeEnum,
    MetadataSchema,
    SourceMetadataSchema,
    TableFormatEnum,
    TableMetadataSchema,
    validate_metadata,
)

#: The literal field names the retrieval stage reads off an element.
CONSUMED = {
    "MetadataSchema": ["content", "source_metadata", "content_metadata",
                       "table_metadata", "image_metadata", "text_metadata"],
    "SourceMetadataSchema": ["source_name", "source_id", "source_location",
                             "source_type"],
    "ContentMetadataSchema": ["type", "subtype", "page_number", "description",
                             "hierarchy"],
    "TableMetadataSchema": ["table_content", "table_format", "table_location",
                            "caption"],
}


@pytest.mark.parametrize("model,fields", [
    (MetadataSchema, CONSUMED["MetadataSchema"]),
    (SourceMetadataSchema, CONSUMED["SourceMetadataSchema"]),
    (ContentMetadataSchema, CONSUMED["ContentMetadataSchema"]),
    (TableMetadataSchema, CONSUMED["TableMetadataSchema"]),
])
def test_the_consumed_fields_exist_under_their_upstream_names(model, fields):
    declared = set(model.model_fields)
    assert set(fields) <= declared, f"missing: {set(fields) - declared}"


def test_unknown_fields_are_refused():
    """`extra="forbid"` is inherited from upstream deliberately: a field we
    invent is a field the consumer will not read, and failing here beats
    shipping an element nobody downstream can use."""
    with pytest.raises(ValidationError):
        validate_metadata({
            "content": "x",
            # The field the retrieval stage reads for image and table citations
            # and which upstream's own schema does not define. Setting it to
            # paper over that would mean our output no longer validates against
            # the schema we claim to satisfy.
            "content_metadata": {"type": "image", "location": [0, 0, 1, 1]},
        })


def test_content_type_values_are_upstreams_strings():
    """A consumer compares these against literals — `"structured"`, `"text"`,
    `"image"` — so the *values* are the contract, not the enum names."""
    assert ContentTypeEnum.TEXT.value == "text"
    assert ContentTypeEnum.IMAGE.value == "image"
    assert ContentTypeEnum.STRUCTURED.value == "structured"
    assert ContentTypeEnum.AUDIO.value == "audio"


def test_table_format_values_are_upstreams_strings():
    assert TableFormatEnum.MARKDOWN.value == "markdown"
    assert TableFormatEnum.STRUCTURED.value == "structured"
    assert TableFormatEnum.PSEUDO_MARKDOWN.value == "pseudo_markdown"


def test_an_upstream_shaped_element_validates():
    """A payload written the way nv-ingest emits one, accepted as-is."""
    element = validate_metadata({
        "content": "| a | b |\n|---|---|\n| 1 | 2 |",
        "source_metadata": {
            "source_name": "report.pdf", "source_id": "abc",
            "source_location": "s3://bucket/report.pdf", "source_type": "pdf",
        },
        "content_metadata": {
            "type": "structured", "subtype": "table", "page_number": 3,
            "description": "Table 1.",
            "hierarchy": {"page_count": 10, "page": 3},
        },
        "table_metadata": {
            "table_format": "markdown",
            "table_content": "| a | b |\n|---|---|\n| 1 | 2 |",
            "table_location": [10, 20, 110, 70],
        },
        "total_pages": 10,
    })
    assert element.content_metadata.page_number == 3
    assert element.table_metadata.table_location == (10, 20, 110, 70)


def test_metadata_for_the_wrong_type_is_not_invented():
    """A text element carries no `table_metadata`, and `exclude_none` is what
    keeps it off the wire rather than sending an empty object a consumer has to
    branch on."""
    element = validate_metadata({
        "content": "hello",
        "content_metadata": {"type": "text"},
    })
    dumped = element.model_dump(mode="json", exclude_none=True)
    assert "table_metadata" not in dumped
    assert "image_metadata" not in dumped
