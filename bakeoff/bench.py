#!/usr/bin/env python3
"""The ingestion bake-off: run every stack over both corpora, score, tabulate.

Two corpora, because they answer different questions.

  corpus/   generated here from the structures in make_corpus.py, so the ground
            truth is exact rather than annotated, and it is ours to commit.
  the upstream corpus
            the blueprint's own sample documents (data/multimodal in
            NVIDIA-AI-Blueprints/rag 2.6.2) and the cell grids transcribed from
            them. Both are under the NVIDIA Asset License Agreement —
            evaluation is permitted, redistribution is not — so **neither is in
            this repository**. Set UPSTREAM_MULTIMODAL and
            INGEST_BAKEOFF_REAL_DIR to run those rows; without them the sweep
            runs the generated corpus and says what it skipped.
            real-corpus.manifest.json holds the sha256 of every file on both
            sides, so a re-run can prove it scored the same extraction against
            the same truth.
"""
import hashlib, json, os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(HERE))
from evaluate import score

MANIFEST = json.loads((HERE / "real-corpus.manifest.json").read_text())
UPSTREAM = Path(os.environ.get("UPSTREAM_MULTIMODAL", "/nonexistent"))
REAL_GT = Path(os.environ.get("INGEST_BAKEOFF_REAL_DIR", "/nonexistent"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def upstream_available() -> bool:
    """True only when every document and every ground-truth file is present
    *and* hashes to the pin. A partial or drifted upstream corpus silently
    changes the numbers, which is worse than not running those rows at all."""
    problems = []
    for entry in MANIFEST["documents"]["files"]:
        path = UPSTREAM / entry["name"]
        if not path.is_file():
            problems.append(f"missing document {path}")
        elif sha256(path) != entry["sha256"]:
            problems.append(f"{path} does not match the manifest sha256")
    for entry in MANIFEST["ground_truth"]["files"]:
        path = REAL_GT / entry["name"]
        if not path.is_file():
            problems.append(f"missing ground truth {path}")
        elif sha256(path) != entry["sha256"]:
            problems.append(f"{path} does not match the manifest sha256")
    if problems:
        print("-- skipping the upstream-corpus rows:", file=sys.stderr)
        for p in problems:
            print(f"     {p}", file=sys.stderr)
        print("   Set UPSTREAM_MULTIMODAL to a checkout of "
              f"{MANIFEST['documents']['source']} at "
              f"{MANIFEST['documents']['tag']} ({MANIFEST['documents']['path']}) "
              "and INGEST_BAKEOFF_REAL_DIR to the ground-truth directory. "
              "Neither is in this repository, and must not be: "
              + " ".join(MANIFEST["why"][:3]), file=sys.stderr)
        return False
    return True

# (document path, ground truth path, needs OCR)
DOCS = [
    (HERE / "corpus/born_digital.pdf", HERE / "corpus/ground_truth.json", False),
    (HERE / "corpus/hard_tables.pdf", HERE / "corpus/ground_truth_hard.json", False),
    (HERE / "corpus/report.docx", HERE / "corpus/ground_truth.json", False),
    (HERE / "corpus/page.html", HERE / "corpus/ground_truth.json", False),
    (HERE / "corpus/scanned.pdf", HERE / "corpus/ground_truth.json", True),
    (HERE / "corpus/scanned_hard.pdf", HERE / "corpus/ground_truth_hard.json", True),
    (HERE / "corpus/scanned_page1.png", HERE / "corpus/ground_truth.json", True),
]

UPSTREAM_DOCS = [
    (UPSTREAM / "table_test.pdf", REAL_GT / "table_test.json", False),
    (UPSTREAM / "multimodal_test.pdf", REAL_GT / "multimodal_test.json", False),
    (UPSTREAM / "embedded_table.pdf", REAL_GT / "embedded_table.json", False),
]

LAYOUTS = ["layout_heron_default", "layout_egret_large"]
MODES = ["fast", "accurate"]
OCRS = ["rapidocr", "easyocr"]


def stacks(needs_ocr):
    yield ("pdfium", {})
    for layout in LAYOUTS:
        for mode in MODES:
            env = {"DOCLING_LAYOUT": layout, "DOCLING_TABLE_MODE": mode}
            if needs_ocr:
                for ocr in OCRS:
                    yield (f"docling:{ocr}", env)
            else:
                yield ("docling", env)


def main():
    rows = []
    docs = DOCS + (UPSTREAM_DOCS if upstream_available() else [])
    for doc, gtf, needs_ocr in docs:
        if not doc.exists():
            print(f"-- skipping {doc.name}: not present", file=sys.stderr)
            continue
        gt = json.loads(gtf.read_text())
        for engine, env in stacks(needs_ocr):
            if engine == "pdfium" and doc.suffix not in (".pdf",):
                continue
            tag = "_".join([doc.stem, engine.replace(":", "-"),
                            env.get("DOCLING_LAYOUT", "").replace("layout_", ""),
                            env.get("DOCLING_TABLE_MODE", "")]).strip("_")
            dest = OUT / f"{tag}.json"
            r = subprocess.run(
                [sys.executable, str(HERE / "run_extract.py"), engine, str(doc), str(dest)],
                env=dict(os.environ, **env), capture_output=True, text=True)
            if r.returncode != 0:
                print(f"!! {tag} exited {r.returncode}: {r.stderr[-400:]}", file=sys.stderr)
                continue
            ex = json.loads(dest.read_text())
            s = score(gt, ex)
            rows.append({"document": doc.name, "corpus": "upstream" if doc.parent == UPSTREAM else "ours",
                         "engine": engine,
                         "layout": env.get("DOCLING_LAYOUT", "-").replace("layout_", ""),
                         "table_mode": env.get("DOCLING_TABLE_MODE", "-"),
                         **{k: v for k, v in s.items() if k != "text_cer_per_paragraph"},
                         **ex["_run"]})
            last = rows[-1]
            print(f"{tag:66s} cer={last['text_cer']} f1={last['table_f1_mean']:.3f} "
                  f"cells={last['cell_set_f1_mean']:.3f} "
                  f"{last['tables_emitted']}/{last['tables_expected']} "
                  f"{last['wall_s']}s {last['peak_rss_mib']:.0f}MiB "
                  f"{(last['error'] or '').splitlines()[0] if last['error'] else ''}",
                  flush=True)
    (HERE / "results.json").write_text(json.dumps(rows, indent=1) + "\n")

    print("\n| Corpus | Document | Stack | Layout | Table mode | text CER | tables "
          "| table F1 | cell F1 | wall | peak RSS |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        cer = "n/a" if r["text_cer"] is None else f"{r['text_cer']:.4f}"
        print(f"| {r['corpus']} | `{r['document']}` | {r['engine']} | {r['layout']} "
              f"| {r['table_mode']} | {cer} | {r['tables_emitted']}/{r['tables_expected']} "
              f"| {r['table_f1_mean']:.3f} | {r['cell_set_f1_mean']:.3f} | {r['wall_s']} s "
              f"| {r['peak_rss_mib']:.0f} MiB |")


if __name__ == "__main__":
    main()
