# SPDX-FileCopyrightText: Copyright (c) 2024, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Modified by Super Protocol, 2026: reduced to the element contract the RAG
# retrieval stage actually dereferences, with the producer-side fields this
# service does not set removed (embedding, audio, error and info metadata, the
# object-store mirrors, the access-control and collection fields). Field names,
# types, defaults and the extra="forbid" behaviour are unchanged, because the
# point of this file is that a consumer written against NVIDIA's schema accepts
# our output without a change.
#
# Derived from `nemo_retriever/common/api/internal/schemas/meta/metadata_schema.py`
# in NVIDIA/nv-ingest (Apache-2.0). The upstream licence is in LICENSE beside
# this repository's own, which is the same licence.
"""The wire contract.

SUP-874 replaces nv-ingest and the four extraction NIMs. It does **not** replace
the shape of what they emitted: the blueprint's retrieval stage reads
`content`, `source_metadata`, `content_metadata` and `table_metadata` off every
element, and has to keep working against ours unchanged.

So this module is the specification, not a convenience. `extra="forbid"` is
inherited deliberately — a field we invent is a field the consumer will not read,
and failing here is better than shipping an element nobody downstream can use.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BaseModelNoExt(BaseModel):
    """Upstream's base: unknown fields are an error, not a pass-through."""

    model_config = ConfigDict(extra="forbid")


class ContentTypeEnum(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    STRUCTURED = "structured"
    AUDIO = "audio"
    NONE = "none"


class ContentSubtypeEnum(str, Enum):
    TABLE = "table"
    CHART = "chart"
    INFOGRAPHIC = "infographic"
    NONE = ""


class TextTypeEnum(str, Enum):
    HEADER = "header"
    BODY = "body"
    PAGE = "page"
    DOCUMENT = "document"
    NEARBY_BLOCK = "nearby_block"
    BLOCK = "block"
    LINE = "line"
    SPAN = "span"


class TableFormatEnum(str, Enum):
    STRUCTURED = "structured"
    MARKDOWN = "markdown"
    HTML = "html"
    LATEX = "latex"
    SIMPLE = "simple"
    PSEUDO_MARKDOWN = "pseudo_markdown"


class DocumentTypeEnum(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    HTML = "html"
    MARKDOWN = "md"
    TXT = "txt"
    PNG = "png"
    JPEG = "jpeg"
    TIFF = "tiff"
    BMP = "bmp"
    UNKNOWN = "unknown"


class SourceMetadataSchema(BaseModelNoExt):
    """Where the content came from."""

    source_name: str
    source_id: str
    source_location: str = ""
    source_type: DocumentTypeEnum | str
    date_created: str = ""
    last_modified: str = ""
    summary: str = ""
    partition_id: int = -1


class ContentHierarchySchema(BaseModelNoExt):
    """Where in the source it sits. `page` and `page_count` are the two the
    retrieval stage uses; block/line/span are kept so the shape matches."""

    page_count: int = -1
    page: int = -1
    block: int = -1
    line: int = -1
    span: int = -1


class ContentMetadataSchema(BaseModelNoExt):
    type: ContentTypeEnum
    description: str = ""
    page_number: int = -1
    hierarchy: ContentHierarchySchema = Field(default_factory=ContentHierarchySchema)
    subtype: ContentSubtypeEnum | str = ""


class TextMetadataSchema(BaseModelNoExt):
    text_type: TextTypeEnum
    summary: str = ""
    keywords: str | list[str] | dict = ""
    language: str = "en"
    text_location: tuple = (0, 0, 0, 0)
    text_location_max_dimensions: tuple = (0, 0)


class ImageMetadataSchema(BaseModelNoExt):
    image_type: DocumentTypeEnum | str
    structured_image_type: ContentTypeEnum = ContentTypeEnum.NONE
    caption: str = ""
    text: str = ""
    image_location: tuple = (0, 0, 0, 0)
    image_location_max_dimensions: tuple = (0, 0)
    width: int = 0
    height: int = 0


class TableMetadataSchema(BaseModelNoExt):
    caption: str = ""
    table_format: TableFormatEnum
    table_content: str = ""
    table_content_format: TableFormatEnum | str = ""
    table_location: tuple = (0, 0, 0, 0)
    table_location_max_dimensions: tuple = (0, 0)


class MetadataSchema(BaseModelNoExt):
    """One extracted element."""

    content: str = ""
    content_url: str = ""
    source_metadata: SourceMetadataSchema | None = None
    content_metadata: ContentMetadataSchema | None = None
    text_metadata: TextMetadataSchema | None = None
    image_metadata: ImageMetadataSchema | None = None
    table_metadata: TableMetadataSchema | None = None
    total_pages: int | None = None
    custom_content: dict[str, Any] | None = None


def validate_metadata(metadata: dict[str, Any]) -> MetadataSchema:
    """Upstream's entry point, kept by name so a consumer's import still works."""
    return MetadataSchema(**metadata)
