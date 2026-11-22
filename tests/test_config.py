# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""Boot refusals.

Each of these is an acceptance criterion the service can only keep by refusing
to start without it. A process that starts and then 500s on the first document
looks like a model problem and is a configuration problem; worse, a process that
starts with the hub reachable looks fine right up to the moment it fetches a
recogniser while a tenant's page is in memory.
"""

from __future__ import annotations

import pytest

from swarm_doc_ingest.config import Settings


@pytest.fixture
def good_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("INGEST_API_KEY", "k")
    monkeypatch.delenv("INGEST_TABLE_MODE", raising=False)
    monkeypatch.delenv("INGEST_OCR_ENGINE", raising=False)
    monkeypatch.delenv("INGEST_LAYOUT_PRESET", raising=False)
    return tmp_path


def test_the_defaults_are_the_measured_ones(good_env):
    s = Settings()
    s.check()
    assert s.layout_preset == "layout_heron_default"
    assert s.table_mode == "accurate"
    assert s.ocr_engine == "rapidocr"
    # OCR of a page that already has a text layer costs ~3.5 s and buys
    # nothing, so forcing it is per-request rather than always-on.
    assert s.force_full_page_ocr is False
    assert s.include_image_content is False


def test_missing_weights_refuse_the_boot(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(tmp_path / "nope"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("INGEST_API_KEY", "k")
    with pytest.raises(RuntimeError, match="model artifacts not found"):
        Settings().check()


@pytest.mark.parametrize("value", [None, "0", "false", ""])
def test_a_reachable_hub_refuses_the_boot(good_env, monkeypatch, value):
    """The strongest guarantee this component makes is that no model is fetched
    at request time. It is one environment variable, so it is checked."""
    if value is None:
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    else:
        monkeypatch.setenv("HF_HUB_OFFLINE", value)
    with pytest.raises(RuntimeError, match="HF_HUB_OFFLINE must be 1"):
        Settings().check()


def test_no_key_refuses_the_boot(good_env, monkeypatch):
    monkeypatch.setenv("INGEST_API_KEY", "")
    with pytest.raises(RuntimeError, match="not a trust boundary"):
        Settings().check()


def test_an_unknown_table_mode_refuses_the_boot(good_env, monkeypatch):
    monkeypatch.setenv("INGEST_TABLE_MODE", "fastest")
    with pytest.raises(RuntimeError, match="unknown table mode"):
        Settings().check()


def test_an_ocr_engine_this_image_does_not_bake_refuses_the_boot(good_env, monkeypatch):
    """EasyOCR is a valid Docling option and is not in this image. Accepting the
    name and failing later would look like a Docling bug."""
    monkeypatch.setenv("INGEST_OCR_ENGINE", "easyocr")
    with pytest.raises(RuntimeError, match="bakes.*RapidOCR only"):
        Settings().check()


def test_ocr_can_be_turned_off(good_env, monkeypatch):
    monkeypatch.setenv("INGEST_OCR_ENGINE", "off")
    Settings().check()


def test_an_image_with_no_fonts_refuses_the_boot(good_env, monkeypatch, tmp_path):
    """The silent one, and the reason it is checked at all.

    The PDF base-14 fonts — Helvetica, Times, Courier — are by specification not
    embedded in a document that uses them, so a renderer with no font to
    substitute produces a different page image. The layout model and the OCR
    engine read that image rather than the text layer, so extraction keeps
    succeeding and only the numbers drop: measured on
    `tests/fixtures/hard_tables.pdf`, 0 of 2 tables with no fonts installed and
    2 of 2 with them, same model bytes and same options.
    """
    from swarm_doc_ingest import config as config_module

    monkeypatch.setattr(config_module, "FONT_ROOTS", (tmp_path / "no-fonts",))
    with pytest.raises(RuntimeError, match="no fonts found"):
        Settings().check()


def test_any_font_satisfies_the_check(good_env, monkeypatch, tmp_path):
    from swarm_doc_ingest import config as config_module

    fonts = tmp_path / "fonts" / "truetype"
    fonts.mkdir(parents=True)
    (fonts / "LiberationSans-Regular.ttf").write_bytes(b"not really a font")
    monkeypatch.setattr(config_module, "FONT_ROOTS", (tmp_path / "fonts",))
    Settings().check()
