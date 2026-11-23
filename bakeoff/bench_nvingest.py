#!/usr/bin/env python3
"""The upstream NIM pipeline over both corpora, scored by the same evaluator.

A separate driver from `bench.py` for the same reason `bench_unst.sh` is one:
the stacks cannot share a virtualenv, and the comparison is only fair if each
brings its own dependency tree. What they do share is this file's imports — the
document list, the two corpora and the manifest hash check all come from
`bench.py`, so "the same documents and the same scorer" is a fact about the code
rather than a claim in a comment.

    NVINGEST_PYTHON=/path/to/.venv-nvingest/bin/python \
    YOLOX_HTTP_ENDPOINT=... YOLOX_TABLE_STRUCTURE_HTTP_ENDPOINT=... \
    YOLOX_GRAPHIC_ELEMENTS_HTTP_ENDPOINT=... OCR_HTTP_ENDPOINT=... \
    NVIDIA_API_KEY=... \
    UPSTREAM_MULTIMODAL=/path/to/rag/data/multimodal \
    INGEST_BAKEOFF_REAL_DIR=/path/to/ground-truth \
      python bench_nvingest.py

Writes `results-nvingest.json` and prints the Markdown table. `delta.py` then
reads both result files and prints the difference, which is the number SUP-883
asks for.

`NVINGEST_ENGINE=nv-ingest:text` runs the no-NIM control: the text stage alone,
no endpoint contacted, which is what the four NIMs are being measured against.
It writes `results-nvingest-text-control.json` instead — a result file named
after what produced it, so a control run can never be read as the pipeline.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from bench import DOCS, UPSTREAM_DOCS, UPSTREAM, OUT, upstream_available  # noqa: E402
from evaluate import score  # noqa: E402

ENGINE = os.environ.get("NVINGEST_ENGINE", "nv-ingest")
PYTHON = os.environ.get("NVINGEST_PYTHON", str(HERE / ".venv-nvingest/bin/python"))
RUNNER = HERE / "run_extract_nvingest.py"


def main() -> None:
    if not Path(PYTHON).exists():
        raise SystemExit(
            f"{PYTHON} does not exist. The upstream stack needs its own "
            "virtualenv (nv-ingest-api, nv-ingest-client); see README.md, or "
            "set NVINGEST_PYTHON.")
    rows, skipped = [], []
    docs = DOCS + (UPSTREAM_DOCS if upstream_available() else [])
    for doc, gtf, _needs_ocr in docs:
        if not doc.exists():
            print(f"-- skipping {doc.name}: not present", file=sys.stderr)
            skipped.append((doc.name, "not present"))
            continue
        gt = json.loads(gtf.read_text())
        tag = f"{doc.stem}_{ENGINE.replace(':', '-')}"
        dest = OUT / f"{tag}.json"
        r = subprocess.run([PYTHON, str(RUNNER), ENGINE, str(doc), str(dest)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            # A runner that exits non-zero writes no extraction, so this
            # document has no row — and `delta.py` would then simply have one
            # fewer document to subtract. Collected here so a short sweep is
            # visible in the output rather than only in scrolled stderr.
            print(f"!! {tag} exited {r.returncode}: {r.stderr[-600:]}", file=sys.stderr)
            skipped.append((doc.name, f"runner exited {r.returncode}: "
                                      f"{(r.stderr.strip().splitlines() or [''])[-1][:160]}"))
            continue
        ex = json.loads(dest.read_text())
        s = score(gt, ex)
        rows.append({"document": doc.name,
                     "corpus": "upstream" if doc.parent == UPSTREAM else "ours",
                     "engine": ex["_run"]["engine"],
                     **{k: v for k, v in s.items() if k != "text_cer_per_paragraph"},
                     **ex["_run"]})
        last = rows[-1]
        cer = "n/a" if last["text_cer"] is None else f"{last['text_cer']:.4f}"
        print(f"{tag:46s} cer={cer} f1={last['table_f1_mean']:.3f} "
              f"cells={last['cell_set_f1_mean']:.3f} "
              f"{last['tables_emitted']}/{last['tables_expected']} "
              f"charts={last['charts_emitted']} {last['wall_s']}s "
              f"{last['peak_rss_mib']:.0f}MiB "
              f"{(last['error'] or '').splitlines()[0] if last['error'] else ''}",
              flush=True)

    out_name = ("results-nvingest-text-control.json" if ENGINE.endswith(":text")
                else "results-nvingest.json")
    (HERE / out_name).write_text(json.dumps(rows, indent=1) + "\n")
    print(f"\nwrote {out_name}", file=sys.stderr)

    print("\n| Corpus | Document | Stack | text CER | tables | table F1 | cell F1 "
          "| charts | wall | peak RSS |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        cer = "n/a" if r["text_cer"] is None else f"{r['text_cer']:.4f}"
        print(f"| {r['corpus']} | `{r['document']}` | {r['engine']} | {cer} "
              f"| {r['tables_emitted']}/{r['tables_expected']} "
              f"| {r['table_f1_mean']:.3f} | {r['cell_set_f1_mean']:.3f} "
              f"| {r['charts_emitted']} | {r['wall_s']} s "
              f"| {r['peak_rss_mib']:.0f} MiB |")

    print(f"\n{len(rows)} of {len(docs)} documents produced a row.")
    for name, why in skipped:
        print(f"-- no row for {name}: {why}")
    if skipped:
        print("A document with no row is not a zero and not a loss — it is "
              "absent from the comparison. Say so when reporting the delta.")


if __name__ == "__main__":
    main()
