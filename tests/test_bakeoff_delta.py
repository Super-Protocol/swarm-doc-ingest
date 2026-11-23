# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""The subtraction, tested on the rows that must not be subtracted.

`delta.py` prints the number this whole comparison exists to produce, and the
way it can be wrong without anything failing is arithmetic on a document one
side never read: a runner records an extractor that raised as an empty
extraction, which scores table F1 0.000 and CER 1.0000, and averaging that is a
landslide for whoever did run.

Two of those are certain rather than hypothetical — nv-ingest has no HTML
backend, so `page.html` errors on every real run; and a credential the inference
plane refuses errors on *every* row while the sweep still exits 0 — so both are
asserted here against rows built by hand, which is the only way to put an error
on exactly one side of exactly one document.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bakeoff"))

from delta import compare, main, shipped_rows  # noqa: E402


def ours_row(document, corpus="ours", f1=1.0, cells=1.0, cer=0.0, error=None,
             engine="docling", layout="heron_default", table_mode="accurate"):
    return {"document": document, "corpus": corpus, "engine": engine,
            "layout": layout, "table_mode": table_mode,
            "table_f1_mean": f1, "cell_set_f1_mean": cells, "text_cer": cer,
            "tables_emitted": 2, "tables_expected": 2, "error": error}


def upstream_row(document, corpus="ours", f1=0.0, cells=0.0, cer=1.0, error=None,
                 engine="nv-ingest"):
    return {"document": document, "corpus": corpus, "engine": engine,
            "table_f1_mean": f1, "cell_set_f1_mean": cells, "text_cer": cer,
            "tables_emitted": 0, "tables_expected": 2, "error": error}


HTML_ERROR = "ValueError: nv-ingest has no extraction backend for .html files"


def test_a_document_upstream_could_not_read_is_not_a_delta():
    """The `page.html` case, which happens on every real run. Averaging it
    credits the open stack with a gap in the other stack's format coverage."""
    comparison = compare(
        [ours_row("born_digital.pdf", f1=0.5), ours_row("page.html", f1=1.0)],
        [upstream_row("born_digital.pdf"), upstream_row("page.html", error=HTML_ERROR)],
    )
    assert [e["document"] for e in comparison["scored"]] == ["born_digital.pdf"]
    assert comparison["means"]["ours"]["documents"] == 1
    assert comparison["means"]["ours"]["d_table_f1"] == pytest.approx(0.5)
    assert comparison["unread"] == [{"corpus": "ours", "document": "page.html",
                                     "side": "upstream", "error": HTML_ERROR}]


def test_the_coverage_gap_is_still_reported_rather_than_dropped():
    """Out of the mean is not out of the output: a format the upstream stack
    cannot read at all is a finding, and a finding nobody prints is lost."""
    comparison = compare([ours_row("a.pdf"), ours_row("page.html")],
                         [upstream_row("a.pdf"), upstream_row("page.html", error=HTML_ERROR)])
    assert len(comparison["unread"]) == 1
    assert comparison["unread"][0]["error"] == HTML_ERROR


def test_a_run_where_every_upstream_row_errored_is_refused():
    """The 403 of 2026-11-23: a key the inference plane refuses makes every
    `extract()` raise, the runner exits 0 anyway, and the mean of eleven empty
    extractions is a large positive delta with nothing to say it is wrong."""
    error = "RuntimeError: Error during NimClient inference: 403 Authorization failed"
    with pytest.raises(SystemExit) as refusal:
        compare([ours_row("a.pdf"), ours_row("b.pdf")],
                [upstream_row("a.pdf", error=error), upstream_row("b.pdf", error=error)])
    message = str(refusal.value)
    assert "every upstream row errored (2 of 2)" in message
    assert "403" in message


def test_a_row_our_own_stack_could_not_read_is_excluded_too():
    """Symmetrical, and for the same reason: our error scores upstream a win it
    did not earn either."""
    comparison = compare([ours_row("a.pdf"), ours_row("b.pdf", error="RuntimeError: boom")],
                         [upstream_row("a.pdf"), upstream_row("b.pdf")])
    assert [e["document"] for e in comparison["scored"]] == ["a.pdf"]
    assert comparison["unread"][0]["side"] == "ours"


def test_an_error_on_both_sides_says_so():
    comparison = compare(
        [ours_row("a.pdf"), ours_row("b.pdf", error="RuntimeError: ours")],
        [upstream_row("a.pdf"), upstream_row("b.pdf", error="RuntimeError: theirs")])
    assert comparison["unread"][0]["side"] == "both"


def test_two_corpora_are_averaged_apart():
    comparison = compare(
        [ours_row("a.pdf", f1=0.8), ours_row("t.pdf", corpus="upstream", f1=0.9)],
        [upstream_row("a.pdf", f1=0.3), upstream_row("t.pdf", corpus="upstream", f1=0.1)])
    assert comparison["means"]["ours"]["d_table_f1"] == pytest.approx(0.5)
    assert comparison["means"]["upstream"]["d_table_f1"] == pytest.approx(0.8)


def test_a_document_with_no_prose_scores_tables_and_abstains_on_cer():
    """`table_test.pdf` has no ground-truth paragraph, so its CER is `None` on
    both sides. That must not be read as a zero, and must not cost the row its
    table F1 either."""
    comparison = compare([ours_row("table_test.pdf", f1=0.95, cer=None)],
                         [upstream_row("table_test.pdf", f1=0.5, cer=None)])
    assert comparison["scored"][0]["d_cer"] is None
    assert comparison["means"]["ours"]["d_cer"] is None
    assert comparison["means"]["ours"]["d_table_f1"] == pytest.approx(0.45)


def test_result_files_that_measured_different_corpora_are_refused():
    with pytest.raises(SystemExit, match="share no document"):
        compare([ours_row("a.pdf")], [upstream_row("b.pdf")])


def test_only_the_configuration_the_service_ships_is_compared():
    """The sweep holds 59 rows per corpus. Picking the best row per document
    would describe a deployment that re-chooses its layout preset per file."""
    rows = [ours_row("a.pdf", f1=0.9, layout="egret_large"),
            ours_row("a.pdf", f1=0.4, table_mode="fast"),
            ours_row("a.pdf", f1=0.6),
            ours_row("a.pdf", f1=1.0, engine="pdfium")]
    assert shipped_rows(rows)["a.pdf"]["table_f1_mean"] == 0.6


def test_main_reads_two_files_and_prints_the_tables(tmp_path, capsys):
    ours = tmp_path / "ours.json"
    theirs = tmp_path / "theirs.json"
    ours.write_text(json.dumps([ours_row("a.pdf", f1=0.9), ours_row("page.html")]))
    theirs.write_text(json.dumps([upstream_row("a.pdf", f1=0.4),
                                  upstream_row("page.html", error=HTML_ERROR)]))
    main([str(ours), str(theirs)])
    out = capsys.readouterr().out
    assert "| ours | 1 | +0.500 |" in out
    assert "One side could not read these" in out
    assert "page.html" in out


def test_a_missing_result_file_is_refused_by_name(tmp_path):
    with pytest.raises(SystemExit, match="is missing"):
        main([str(tmp_path / "ours.json"), str(tmp_path / "nope.json")])
