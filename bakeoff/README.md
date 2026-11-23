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

## The upstream stack: nv-ingest and the four extraction NIMs

SUP-874 chose the open stack on the numbers above. The acceptance criterion it
could not meet was the comparison against the thing being replaced, which is
SUP-883: **the same documents, the same scorer, nv-ingest 26.3.0 and the four
extraction NIMs on the other side, and the difference reported as a number.**

`run_extract_nvingest.py` calls `nv_ingest_api`'s public extraction interface —
the same code the `nv-ingest:26.3.0` container runs — rather than standing the
service up behind Redis and a vector store. Four NIM endpoints are all it wants,
and **no GPU on this side**:

| Stage | NIM | What it contributes |
|---|---|---|
| Page Elements | `nvcr.io/nim/nvidia/nemotron-page-elements-v3:1.8.0` | where the tables and charts are |
| Table Structure | `nvcr.io/nim/nvidia/nemotron-table-structure-v1:1.8.0` | the cell geometry of each table |
| OCR | `nvcr.io/nim/nvidia/nemotron-ocr-v1:1.3.0` | the characters in each cell |
| Graphic Elements | `nvcr.io/nim/nvidia/nemotron-graphic-elements-v1:1.8.0` | a chart as a table of series |

Those are the coordinates `deploy/compose/nims.yaml` in `NVIDIA-AI-Blueprints/rag`
v2.6.2 resolves to — not the `nemoretriever-*` names the blueprint used before
2.6, which are retired (`410 Gone` on the hosted endpoint, no such tag in the
registry).

```bash
python -m venv .venv-nvingest
.venv-nvingest/bin/pip install -r requirements-nvingest.txt

# self-hosted: docker compose up page-elements graphic-elements table-structure \
#              nemotron-ocr   (rag/deploy/compose/nims.yaml; one GPU serves all four)
export YOLOX_HTTP_ENDPOINT=http://localhost:8000/v1/infer
export YOLOX_GRAPHIC_ELEMENTS_HTTP_ENDPOINT=http://localhost:8003/v1/infer
export YOLOX_TABLE_STRUCTURE_HTTP_ENDPOINT=http://localhost:8006/v1/infer
export OCR_HTTP_ENDPOINT=http://localhost:8012/v1/infer

# or NVIDIA-hosted, which needs no GPU anywhere:
# export YOLOX_HTTP_ENDPOINT=https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-page-elements-v3
# export YOLOX_GRAPHIC_ELEMENTS_HTTP_ENDPOINT=https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-graphic-elements-v1
# export YOLOX_TABLE_STRUCTURE_HTTP_ENDPOINT=https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-table-structure-v1
# export OCR_HTTP_ENDPOINT=https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1
# export NVIDIA_API_KEY=...        # never a literal in a script or a log

UPSTREAM_MULTIMODAL=/path/to/rag/data/multimodal \
INGEST_BAKEOFF_REAL_DIR=/path/to/ground-truth \
  python bench_nvingest.py          # writes results-nvingest.json

python delta.py                     # the difference, per document and in the mean
```

`stub_nims.py` answers all four endpoints with well-formed responses containing
no detections. Run against it first: it separates "the plumbing is wrong" from
"the models disagree" before a GPU hour or a credential is spent on the
distinction. A run against the stub is not a measurement, and the
`results-nvingest.json` it writes must not be kept.

`bench_nvingest.py` imports its document list, its two corpora and the manifest
hash check from `bench.py`, so "the same documents and the same scorer" is a
property of the code rather than a claim in a comment.

### The credential, which is the part that blocks

Both configurations need an **NGC personal key**, and they need different things
from it:

- **the images** need `artifact-catalog`. All four NIM tags above pull with it.
- **the hosted endpoints** need the key's products to include **NVIDIA Cloud
  Functions**. A catalogue-scoped key is refused by the inference plane with
  `403 Authorization failed` — verified on all four URLs and on
  `integrate.api.nvidia.com`, SUP-883, 2026-11-23. `POST
  https://api.ngc.nvidia.com/v3/keys/get-caller-info` with the key as
  `credentials` prints its `products`, which is the cheapest way to tell the two
  kinds of key apart before booking a GPU to work around the wrong one.

Self-hosting needs one GPU for all four NIMs — they share a card in the compose
file — and a Docker host, which a confidential cluster space is not: four
containers cannot each hold `nvidia.com/gpu: 1` on a single card.

### The no-NIM control, which is recorded

`NVINGEST_ENGINE=nv-ingest:text` runs the text stage alone and contacts no
endpoint. `results-nvingest-text-control.json` is that run on this tree, and it
is the measurement of what the four NIMs are worth rather than of the pipeline:

| | born-digital prose | tables found | a scan |
|---|---|---|---|
| nv-ingest, text stage only | CER 0.0000 | **0 of 19** | nothing at all (CER 1.0000) |

Which is the same shape as the `pdfium` control in the sweep above — upstream's
text stage *is* pdfium — and is why the comparison needs the NIMs to mean
anything. Two rows in that file are findings rather than scores: nv-ingest 26.3.0
has no HTML extraction backend at all, and its image path calls Page Elements
whatever `extract_tables` says, because an image has no text layer for a
NIM-free stage to read.
