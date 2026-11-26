#!/usr/bin/env python3
"""What the caption row costs upstream's table F1. A diagnostic, not a score.

`table_f1` is over `(row, column, cell text)` triples, so a grid that is right
but offset by one row scores near zero. Upstream's Page Elements crop includes
the table's caption, the caption becomes row 0 of the markdown, and every real
row shifts down — which is why `results-nvingest.json` reads table F1 0.019
beside cell F1 0.872 on the same document.

Both numbers are true and neither is the whole picture, so this says how much of
the gap is position and how much is extraction: it drops leading rows until the
row that best matches a ground-truth header, and re-scores with the same
evaluator. Measured 2026-11-26, the five table documents of the generated
corpus go from 0.000-0.019 as emitted to 0.968-0.990 trimmed.

**The trimmed number is not a result for either stack.** It is the answer to
"would a consumer that dropped the caption row get a usable grid", and the
answer is yes. Report the emitted number, and report this beside it.

    python caption_shift.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from evaluate import norm_cell, score  # noqa: E402

DOCS = [("born_digital.pdf", "ground_truth.json"),
        ("hard_tables.pdf", "ground_truth_hard.json"),
        ("scanned.pdf", "ground_truth.json"),
        ("scanned_hard.pdf", "ground_truth_hard.json"),
        ("scanned_page1.png", "ground_truth.json")]


def trim_to_header(table, headers):
    """Drop leading rows until the one that best matches a ground-truth header."""
    best_index, best_hits = 0, -1
    for index, row in enumerate(table[:4]):
        cells = {norm_cell(c) for c in row if norm_cell(c)}
        hits = max((len(cells & {norm_cell(c) for c in h}) for h in headers), default=0)
        if hits > best_hits:
            best_index, best_hits = index, hits
    return table[best_index:]


def main(engine="nv-ingest"):
    print("| Document | table F1 as emitted | after dropping the caption row | cell F1 |")
    print("|---|---|---|---|")
    for name, gt_name in DOCS:
        extraction = HERE / "out" / f"{name.rsplit('.', 1)[0]}_{engine.replace(':', '-')}.json"
        if not extraction.is_file():
            print(f"| `{name}` | not run | | |")
            continue
        gt = json.loads((HERE / "corpus" / gt_name).read_text())
        ex = json.loads(extraction.read_text())
        headers = [t[0] for t in gt["tables"]]
        emitted = score(gt, ex)
        trimmed = score(gt, {"text": ex["text"],
                             "tables": [trim_to_header(t, headers) for t in ex["tables"]]})
        print(f"| `{name}` | {emitted['table_f1_mean']:.3f} "
              f"| {trimmed['table_f1_mean']:.3f} "
              f"| {emitted['cell_set_f1_mean']:.3f} |")


if __name__ == "__main__":
    main(*sys.argv[1:])
