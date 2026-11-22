# The ingestion bake-off

The measurement behind the pipeline choice in SUP-874: which open stack replaces
`nv-ingest` and the four extraction NIMs, decided on numbers rather than on preference.

Run it when a candidate changes — a new Docling release, a new layout preset, a new OCR
engine — before the image's version moves. The pins in `pyproject.toml` and
`scripts/prefetch-models.py` are exact so that moving one is a deliberate act with this
sweep behind it.

## What it measures, and why these two numbers

An extractor can lose a document in two independent ways, and one number hides the
other:

- **text CER** — per ground-truth paragraph, the best-matching window of the extracted
  prose, Levenshtein distance over the paragraph's length, averaged weighted by length.
  This is what OCR gets wrong.
- **table F1** — over `(row, column, cell text)` triples. **A table read as prose scores
  zero here however perfect its text is**, which is the distinction SUP-874's acceptance
  criterion is about: "tables extracted rather than flattened into prose".

`cell_set_f1` is the same cells as a multiset with positions ignored. It separates "read
the values" from "read the geometry", so two stacks that disagree only about how a
spanning header is represented in a grid are not scored apart on a convention.

The control is `pdfium` — raw PDF text, no layout model and no table model. It scores a
text CER of 0.0000 on born-digital documents and finds **0 tables out of 19** across the
seven PDFs here. That is the whole argument for a layout and table model rather than a
text extractor, in one row.

## The two corpora

`make_corpus.py` generates `corpus/` from the structures declared inside it, so the
ground truth is exact rather than annotated, and it is ours to commit and to publish:

| File | What it is for |
|---|---|
| `born_digital.pdf` | ruled tables with a text layer |
| `hard_tables.pdf` | **no ruling lines**, and a two-level spanning header — where layout models disagree |
| `report.docx`, `page.html` | the same content through the non-PDF backends |
| `scanned.pdf`, `scanned_hard.pdf`, `scanned_page1.png` | the above rasterised at 200 dpi, rotated 0.6°, blurred and speckled: no text layer at all |

The second corpus is the blueprint's own sample documents — `data/multimodal` in
`NVIDIA-AI-Blueprints/rag` 2.6.2 — with ground truth transcribed from them:
`table_test.pdf` (one 21×6 table), `multimodal_test.pdf` (two tables, two charts, three
bullet points — the document states its own expected answer in its conclusion) and
`embedded_table.pdf` (eight dependency tables from the pandas manual).

**Neither those documents nor the grids transcribed from them are in this repository,
and neither may be.** They are under the NVIDIA Asset License Agreement: evaluation is
permitted, redistribution is not, and a verbatim transcription of an NVIDIA document's
own table cells committed to a public repository is a partial reproduction of the asset.
Decided on SUP-874, 2026-11-22.

`real-corpus.manifest.json` is what keeps that half reproducible anyway: it records the
upstream repository, tag and path, the sha256 and byte count of each PDF, and the sha256
of each ground-truth file. Set `UPSTREAM_MULTIMODAL` and `INGEST_BAKEOFF_REAL_DIR` and
the harness verifies both sides against those hashes before it scores anything — so a
re-run can show it measured the same extraction against the same truth. With either
unset, or with any file drifted from its hash, those rows are skipped and the reason is
printed. The scores stay: they are ours.

## Running it

```bash
# Docling, every layout preset × TableFormer mode × OCR engine, both corpora
python -m venv .venv && . .venv/bin/activate
pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
pip install docling rapidocr-onnxruntime reportlab python-docx pypdfium2 pillow rapidfuzz
docling-tools models download -o ./models layout tableformer
docling-tools models download rapidocr --rapidocr-backend-lang onnxruntime:en -o ./models

python make_corpus.py
DOCLING_ARTIFACTS_PATH=$PWD/models \
UPSTREAM_MULTIMODAL=/path/to/rag/data/multimodal \
INGEST_BAKEOFF_REAL_DIR=/path/to/ground-truth \
  python bench.py                       # writes results.json and a Markdown table

# Unstructured, the rejected alternative, same documents and same scorer
python -m venv .venv-unst && . .venv-unst/bin/activate
pip install 'unstructured[pdf,docx]'    # plus the tesseract-ocr system package
UPSTREAM_MULTIMODAL=/path/to/rag/data/multimodal \
INGEST_BAKEOFF_REAL_DIR=/path/to/ground-truth ./bench_unst.sh
```

Install torch from the CPU index. The default wheel pulls sixteen NVIDIA proprietary
CUDA packages into a component whose entire purpose is not needing any, and takes the
environment from 1.8 GiB to 6.3 GiB.

`results.json` is the recorded run on this tree: 59 rows, 8 vCPU, no GPU.
