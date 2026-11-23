#!/usr/bin/env python3
"""The delta, as a number: the open stack against the upstream NIM pipeline.

SUP-874 chose the open stack on measurements of its own. SUP-883 is the
acceptance criterion that was left open — the same corpus through
`nv-ingest` 26.3.0 and the four extraction NIMs, with the difference reported
rather than inferred. This reads the two result files and prints it.

    python bench.py                 # writes results.json          (open stack)
    python bench_nvingest.py        # writes results-nvingest.json (upstream)
    python delta.py                 # prints the difference

The open stack's row is the configuration the service ships — Docling with
`layout_heron_default`, TableFormer `accurate`, RapidOCR where OCR is needed.
Comparing the sweep's best row per document would flatter us by picking a
different configuration per document, which is not a thing a deployment can do.

A positive delta is the open stack ahead on that metric; for text CER, where
lower is better, the sign is flipped so that positive still means "ours is
better" and the column says so.

**A document one side could not read is not a delta.** An extractor that raised
is recorded by its runner as an empty extraction, which scores table F1 0.000
and CER 1.0000 — arithmetically a landslide for whoever did run. Two of those
are certain rather than hypothetical: nv-ingest 26.3.0 has no HTML backend, so
`page.html` errors on every real run; and a wrong credential or a NIM that is
down errors on *every* row while the sweep still exits 0. So a row with an error
on either side is kept out of the means and listed on its own as a coverage
finding, and a file in which nothing on the upstream side succeeded is refused
instead of averaged. A run where nothing ran is not a measurement.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

SHIPPED = {"engines": ("docling", "docling:rapidocr"),
           "layout": "heron_default", "table_mode": "accurate"}


def shipped_rows(rows: list[dict]) -> dict:
    """The open stack's rows for the configuration the service ships."""
    return {r["document"]: r for r in rows
            if r["engine"] in SHIPPED["engines"]
            and r["layout"] == SHIPPED["layout"]
            and r["table_mode"] == SHIPPED["table_mode"]}


def by_document(rows: list[dict]) -> dict:
    return {r["document"]: r for r in rows}


def first_line(error) -> str:
    return (error or "").splitlines()[0] if error else ""


def compare(ours_rows: list[dict], theirs_rows: list[dict]) -> dict:
    """Subtract the two sweeps.

    Returns `{"scored": [...], "unread": [...], "means": {corpus: {...}}}`:
    `scored` is one entry per document both stacks read, `unread` one per
    document at least one of them could not, and `means` is over `scored`
    alone. Raises `SystemExit` when there is nothing to subtract — no shared
    document, or an upstream side that failed everywhere, which is what a wrong
    credential looks like from here.
    """
    ours, theirs = shipped_rows(ours_rows), by_document(theirs_rows)
    shared = [d for d in ours if d in theirs]
    if not shared:
        raise SystemExit("the two result files share no document; they measured "
                         "different corpora and cannot be subtracted.")

    upstream_errors = [d for d in shared if theirs[d].get("error")]
    if len(upstream_errors) == len(shared):
        raise SystemExit(
            f"every upstream row errored ({len(shared)} of {len(shared)}); there is "
            "no delta to report. First error: "
            f"{first_line(theirs[shared[0]].get('error'))}\n"
            "A credential the inference plane refuses, or a NIM that is not "
            "answering, produces exactly this — and an empty extraction scores "
            "table F1 0.000, which would print as a win rather than as a failure.")

    scored, unread = [], []
    for document in shared:
        a, b = ours[document], theirs[document]
        if a.get("error") or b.get("error"):
            side = ("both" if a.get("error") and b.get("error")
                    else "upstream" if b.get("error") else "ours")
            unread.append({"corpus": a["corpus"], "document": document, "side": side,
                           "error": first_line(b.get("error") or a.get("error"))})
            continue
        scored.append({
            "corpus": a["corpus"], "document": document,
            "ours": a, "upstream": b,
            "d_table_f1": a["table_f1_mean"] - b["table_f1_mean"],
            "d_cell_f1": a["cell_set_f1_mean"] - b["cell_set_f1_mean"],
            "d_cer": (None if a["text_cer"] is None or b["text_cer"] is None
                      else b["text_cer"] - a["text_cer"]),
        })

    if not scored:
        raise SystemExit(f"no document was read by both stacks ({len(unread)} "
                         "skipped); there is no delta to report.")

    means = {}
    for entry in scored:
        means.setdefault(entry["corpus"], []).append(entry)
    for corpus, entries in list(means.items()):
        cers = [e["d_cer"] for e in entries if e["d_cer"] is not None]
        means[corpus] = {
            "documents": len(entries),
            "d_table_f1": sum(e["d_table_f1"] for e in entries) / len(entries),
            "d_cell_f1": sum(e["d_cell_f1"] for e in entries) / len(entries),
            "d_cer": (sum(cers) / len(cers)) if cers else None,
        }
    return {"scored": scored, "unread": unread, "means": means}


def fmt(value, places=3):
    return "n/a" if value is None else f"{value:.{places}f}"


def signed(value, places=3):
    return "n/a" if value is None else f"{value:+.{places}f}"


def render(comparison: dict, theirs_rows: list[dict]) -> None:
    print("| Corpus | Document | table F1 ours | table F1 upstream | Δ table F1 "
          "| cell F1 ours | cell F1 upstream | Δ cell F1 "
          "| CER ours | CER upstream | Δ CER (+ = ours better) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for e in comparison["scored"]:
        a, b = e["ours"], e["upstream"]
        print(f"| {e['corpus']} | `{e['document']}` "
              f"| {fmt(a['table_f1_mean'])} | {fmt(b['table_f1_mean'])} | {signed(e['d_table_f1'])} "
              f"| {fmt(a['cell_set_f1_mean'])} | {fmt(b['cell_set_f1_mean'])} | {signed(e['d_cell_f1'])} "
              f"| {fmt(a['text_cer'], 4)} | {fmt(b['text_cer'], 4)} | {signed(e['d_cer'], 4)} |")

    print("\n| Corpus | documents scored | mean Δ table F1 | mean Δ cell F1 | mean Δ CER |")
    print("|---|---|---|---|---|")
    for corpus, m in comparison["means"].items():
        print(f"| {corpus} | {m['documents']} | {signed(m['d_table_f1'])} "
              f"| {signed(m['d_cell_f1'])} | {signed(m['d_cer'], 4)} |")

    if comparison["unread"]:
        # Kept out of the means above and reported here instead: a stack that
        # could not read the document has not lost on quality, it has a gap.
        print("\n### One side could not read these, so they are not in the means\n")
        print("| Corpus | Document | Could not read | Error |")
        print("|---|---|---|---|")
        for u in comparison["unread"]:
            print(f"| {u['corpus']} | `{u['document']}` | {u['side']} | {u['error']} |")

    engines = sorted({r["engine"] for r in theirs_rows})
    print(f"\nupstream stack: {', '.join(engines)}")
    if any(e.endswith(":text") for e in engines):
        print("note: `nv-ingest:text` contacted no NIM. This is the no-NIM control, "
              "not the upstream pipeline — the delta above is what the four "
              "extraction NIMs are worth, not the delta against them.")


def load(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"{path} is missing; run the sweep that writes it first.")
    return json.loads(path.read_text())


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ours_path = HERE / (argv[0] if len(argv) > 0 else "results.json")
    theirs_path = HERE / (argv[1] if len(argv) > 1 else "results-nvingest.json")
    theirs_rows = load(theirs_path)
    render(compare(load(ours_path), theirs_rows), theirs_rows)


if __name__ == "__main__":
    main()
