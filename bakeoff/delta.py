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
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

SHIPPED = {"engines": ("docling", "docling:rapidocr"),
           "layout": "heron_default", "table_mode": "accurate"}


def shipped_rows(path: Path) -> dict:
    rows = json.loads(path.read_text())
    out = {}
    for r in rows:
        if (r["engine"] in SHIPPED["engines"] and r["layout"] == SHIPPED["layout"]
                and r["table_mode"] == SHIPPED["table_mode"]):
            out[r["document"]] = r
    return out


def upstream_rows(path: Path) -> dict:
    return {r["document"]: r for r in json.loads(path.read_text())}


def fmt(value, places=3):
    return "n/a" if value is None else f"{value:.{places}f}"


def signed(value, places=3):
    return "n/a" if value is None else f"{value:+.{places}f}"


def main() -> None:
    ours_path = HERE / (sys.argv[1] if len(sys.argv) > 1 else "results.json")
    theirs_path = HERE / (sys.argv[2] if len(sys.argv) > 2 else "results-nvingest.json")
    for p in (ours_path, theirs_path):
        if not p.exists():
            raise SystemExit(f"{p} is missing; run the sweep that writes it first.")
    ours, theirs = shipped_rows(ours_path), upstream_rows(theirs_path)
    shared = [d for d in ours if d in theirs]
    if not shared:
        raise SystemExit("the two result files share no document; they measured "
                         "different corpora and cannot be subtracted.")

    print("| Corpus | Document | table F1 ours | table F1 upstream | Δ table F1 "
          "| cell F1 ours | cell F1 upstream | Δ cell F1 "
          "| CER ours | CER upstream | Δ CER (+ = ours better) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    totals = {}
    for document in shared:
        a, b = ours[document], theirs[document]
        d_f1 = a["table_f1_mean"] - b["table_f1_mean"]
        d_cells = a["cell_set_f1_mean"] - b["cell_set_f1_mean"]
        d_cer = (None if a["text_cer"] is None or b["text_cer"] is None
                 else b["text_cer"] - a["text_cer"])
        totals.setdefault(a["corpus"], []).append((d_f1, d_cells, d_cer))
        print(f"| {a['corpus']} | `{document}` "
              f"| {fmt(a['table_f1_mean'])} | {fmt(b['table_f1_mean'])} | {signed(d_f1)} "
              f"| {fmt(a['cell_set_f1_mean'])} | {fmt(b['cell_set_f1_mean'])} | {signed(d_cells)} "
              f"| {fmt(a['text_cer'], 4)} | {fmt(b['text_cer'], 4)} | {signed(d_cer, 4)} |")

    print("\n| Corpus | documents | mean Δ table F1 | mean Δ cell F1 | mean Δ CER |")
    print("|---|---|---|---|---|")
    for corpus, deltas in totals.items():
        cers = [d[2] for d in deltas if d[2] is not None]
        print(f"| {corpus} | {len(deltas)} "
              f"| {signed(sum(d[0] for d in deltas) / len(deltas))} "
              f"| {signed(sum(d[1] for d in deltas) / len(deltas))} "
              f"| {signed(sum(cers) / len(cers), 4) if cers else 'n/a'} |")

    engines = sorted({r["engine"] for r in theirs.values()})
    print(f"\nupstream stack: {', '.join(engines)}")
    if any(e.endswith(":text") for e in engines):
        print("note: `nv-ingest:text` contacted no NIM. This is the no-NIM control, "
              "not the upstream pipeline — the delta above is what the four "
              "extraction NIMs are worth, not the delta against them.")


if __name__ == "__main__":
    main()
