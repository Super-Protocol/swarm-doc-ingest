# Model weights baked into this image

Three model sets, 536 MiB, all redistributable — which is why they are baked
rather than fetched at first boot. `scripts/prefetch-models.py` has the
trade-off table and the reasoning.

None of the three publishers ships a `LICENSE` file in its repository; each
declares its licence in the model-card metadata, which is where the "declared
at" column points. The licence texts themselves are beside this file.

| Model | Role | Pinned at | Licence | Declared at |
|---|---|---|---|---|
| `docling-project/docling-layout-heron` | page layout — what is a table, a heading, a picture | commit `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` | Apache-2.0 (`Apache-2.0.txt`) | `cardData.license` on the model card |
| `docling-project/docling-models` | TableFormer, `accurate` variant — the cell grid inside a table | commit `2199320848bb9a8a519d22e4b528185a4f9a6f64` | CDLA-Permissive-2.0 **and** Apache-2.0 (`CDLA-Permissive-2.0.txt`, `Apache-2.0.txt`) | `cardData.license` on the model card |
| `RapidAI/RapidOCR` PP-OCRv6 `det`/`rec` small + PP-OCRv4 `cls` | OCR, for a page with no text layer | sha256 per file, in `scripts/prefetch-models.py` | Apache-2.0 (`Apache-2.0.txt`) | `LICENSE` in `RapidAI/RapidOCR` on GitHub |

## Two things to know about the pins

**The Hugging Face repositories are pinned by commit, not by `main`.** Docling's
own model specs say `revision="main"`. A mutable revision beside an attested
render means the signed evidence names bytes that can change underneath it,
which is exactly the event a data owner's gatekeeper approval exists to catch.

**RapidOCR's files are pinned by sha256, because it does not publish them as a
Hugging Face repository.** They are plain URLs on ModelScope under release tag
`v3.10.0`, and a tag can be re-pointed. `RapidAI/RapidOCR` on Hugging Face
returns 401, so ModelScope is the only ungated source. It is reached at build
time only — the running image makes no outbound request at all — and the build
fails if any file's bytes differ from the pin.

## What is deliberately *not* here

`CodeFormulaV2` (611 MiB) and `DocumentFigureClassifier` (33 MiB) are Docling
enrichment models this image does not carry, and the four extraction models from
the NVIDIA pipeline (`nemotron-page-elements-v3`, `nemotron-table-structure-v1`,
`nemotron-graphic-elements-v1`, `nemotron-ocr-v1`) are not here either. Their
weights are open and ungated under the NVIDIA Open Model License, so this is not
a licence refusal — it is `docs/ported-listings.md` §3 in the catalog, which
makes vendor-licensed weights opt-in and holds the NVIDIA Open Model License for
a lawyer's read before any of them becomes a default. The bake-off comment on
SUP-874 has the rest of the reasoning.
