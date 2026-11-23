#!/usr/bin/env python3
"""The same normalised extraction, produced by the upstream NIM pipeline.

The other half of the bake-off. `run_extract.py` runs the open stack and
`run_extract_unst.py` the rejected alternative; this runs the thing SUP-874
replaced, over the same documents and through the same scorer, so the difference
is a number rather than an impression.

It calls `nv_ingest_api`'s public extraction interface rather than standing the
`nv-ingest` service up behind Redis. The stages are the same code the
`nvcr.io/nvidia/nemo-microservices/nv-ingest:26.3.0` container runs, and they
reach the four extraction NIMs over HTTP or gRPC, so this side of the comparison
needs no broker, no vector store and **no GPU** — only endpoints:

    Page Elements      finds the table, chart and infographic regions on a page
    Table Structure    reads the cell geometry of each table region
    OCR                reads the characters inside those cells
    Graphic Elements   turns a chart region into a table of series

Endpoints come from upstream's own variable names, so one configuration serves a
self-hosted compose stack (`deploy/compose/nims.yaml` in
`NVIDIA-AI-Blueprints/rag` v2.6.2) and NVIDIA's hosted endpoints on
`ai.api.nvidia.com` alike:

    YOLOX_{GRPC,HTTP}_ENDPOINT / YOLOX_INFER_PROTOCOL                  page elements
    YOLOX_TABLE_STRUCTURE_{GRPC,HTTP}_ENDPOINT / ..._INFER_PROTOCOL    table structure
    YOLOX_GRAPHIC_ELEMENTS_{GRPC,HTTP}_ENDPOINT / ..._INFER_PROTOCOL   graphic elements
    OCR_{GRPC,HTTP}_ENDPOINT / OCR_INFER_PROTOCOL                      OCR
    NVIDIA_API_KEY or NGC_API_KEY                                      hosted bearer

The key is read from the environment and is never written into the extraction
file, the `_run` record or a log line: what is recorded is which endpoint
answered, not what authenticated to it.

**A hosted endpoint needs an NGC personal key whose products include NVIDIA
Cloud Functions.** A key scoped to the artifact catalogue pulls all four NIM
images and is refused by the inference plane with `403 Authorization failed` —
measured on SUP-883, 2026-11-23, against all four URLs.

Engines:
    nv-ingest        every stage, the pipeline as the blueprint composes it
    nv-ingest:text   the text stage only, which calls no NIM at all. The control
                     that separates "the harness works" from "the NIMs
                     answered", and the row that says what the four NIMs are
                     worth: pdfium prose, and zero tables.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import resource
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("OMP_NUM_THREADS", "8")

# Required by the pdfium extractor's schema even when no stage will call it:
# with `nv-ingest:text` nothing is sent anywhere, and a name that cannot resolve
# is a louder failure than a localhost port that might answer something.
UNUSED_ENDPOINT = "http://page-elements.unconfigured.invalid/v1/infer"

DOC_TYPES = {".pdf": "pdf", ".docx": "docx", ".png": "png", ".jpg": "jpeg",
             ".jpeg": "jpeg", ".tiff": "tiff", ".bmp": "bmp"}

# ---------------------------------------------------------------------------
# Normalising what came back. Kept free of any nv-ingest import so it can be
# tested in CI, where the service's own dependency tree is deliberately absent.
# ---------------------------------------------------------------------------

SEPARATOR_ROW = re.compile(r"^[\s|:+\-=]+$")


def _plain(value: Any) -> str:
    """Upstream hands back either the enum or its value, depending on the stage
    that wrote the field. Both mean the same thing here."""
    return str(getattr(value, "value", value) or "").strip().lower()


def pipe_grid(text: str) -> list[list[str]]:
    """A Markdown (or pseudo-Markdown) table back into a cell grid.

    `table_content` is prose with pipes in it, which is all the wire contract
    carries: nv-ingest emits the grid it read as Markdown and keeps the geometry
    only in `table_location`. So the grid the scorer compares is parsed back out
    of exactly what a consumer of the element would have to parse it out of.
    """
    rows: list[list[str]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or SEPARATOR_ROW.match(line):
            continue
        if "|" not in line:
            rows.append([line])
            continue
        cells = line.split("|")
        if cells and not cells[0].strip():
            cells = cells[1:]
        if cells and not cells[-1].strip():
            cells = cells[:-1]
        rows.append([c.strip() for c in cells])
    return rows


def table_grid(table_metadata: dict) -> list[list[str]]:
    content = table_metadata.get("table_content") or ""
    fmt = _plain(table_metadata.get("table_content_format"))
    if fmt == "simple":
        # OCR text joined with spaces: every cell read, no geometry at all. One
        # row is the honest representation — and it scores on `cell_set_f1`
        # while scoring near zero on `table_f1`, which is the distinction.
        return [[content.strip()]] if content.strip() else []
    return pipe_grid(content)


def normalise(elements: list[dict]) -> dict:
    """nv-ingest elements to the bake-off's shape: {"text", "tables"}.

    Three rules, each of them the symmetric case of one in `run_extract.py`:

    * a table arrives as a `structured` element, and its cells are taken out of
      the prose, so a table cannot earn text credit and table credit twice;
    * a chart is counted, not scored as a table. The ground truth's tables are
      tables; letting a chart's series table compete for a table match would
      score the two stacks on a representation choice rather than on the
      extraction, and the open stack deliberately locates charts instead of
      transcribing them (SUP-874);
    * an image element carries no text and is ignored.

    One asymmetry is upstream's and is left alone: its text stage reads the page
    at `page` depth, so the characters inside a table are in the prose as well
    as in the structured element. That is what the pipeline emits, and neither
    metric is distorted by it — text CER measures each ground-truth paragraph
    against its best window in the prose, so extra text cannot improve it, and
    table credit comes only from structured elements.
    """
    prose: list[str] = []
    tables: list[list[list[str]]] = []
    charts = 0
    for element in elements:
        meta = element or {}
        content_metadata = meta.get("content_metadata") or {}
        ctype = _plain(content_metadata.get("type"))
        subtype = _plain(content_metadata.get("subtype"))
        if ctype == "text":
            text = (meta.get("content") or "").strip()
            if text:
                prose.append(text)
        elif ctype == "structured":
            table_metadata = meta.get("table_metadata") or {}
            if subtype == "table":
                tables.append(table_grid(table_metadata))
            else:
                charts += 1
    return {"text": "\n".join(prose), "tables": tables, "_charts": charts}


# ---------------------------------------------------------------------------
# Running the stages.
# ---------------------------------------------------------------------------


def endpoints(prefix: str, *, required: bool) -> tuple[tuple[str | None, str | None], str]:
    grpc = os.environ.get(f"{prefix}_GRPC_ENDPOINT") or None
    http = os.environ.get(f"{prefix}_HTTP_ENDPOINT") or None
    protocol = (os.environ.get(f"{prefix}_INFER_PROTOCOL") or "http").lower()
    if not (grpc or http):
        if required:
            raise SystemExit(
                f"{prefix}_HTTP_ENDPOINT or {prefix}_GRPC_ENDPOINT is unset. The "
                "full pipeline needs all four extraction NIMs; run the engine "
                "`nv-ingest:text` for the no-NIM control.")
        return (None, UNUSED_ENDPOINT), "http"
    if protocol == "grpc" and not grpc:
        raise SystemExit(f"{prefix}_INFER_PROTOCOL=grpc with no {prefix}_GRPC_ENDPOINT")
    if protocol == "http" and not http:
        raise SystemExit(f"{prefix}_INFER_PROTOCOL=http with no {prefix}_HTTP_ENDPOINT")
    return (grpc, http), protocol


def auth_token() -> str:
    """The bearer the hosted endpoints require, and nothing a self-hosted one
    looks at. Read here, passed to the stage, never recorded."""
    return os.environ.get("NVIDIA_API_KEY") or os.environ.get("NGC_API_KEY") or ""


def frame(returned: Any):
    """Some stages return the ledger, some return `(ledger, trace)`. Upstream is
    inconsistent across the three entry points this file calls and the shape is
    not in the signature, so normalise it here rather than at each call."""
    return returned[0] if isinstance(returned, tuple) else returned


def ledger(path: Path, document_type: str):
    import pandas as pd

    content = base64.b64encode(path.read_bytes()).decode()
    return pd.DataFrame([{
        "source_id": path.name,
        "source_name": path.name,
        "content": content,
        "document_type": document_type,
        "metadata": {
            "content": content,
            "source_metadata": {"source_id": path.name, "source_name": path.name,
                                "source_type": document_type},
            "content_metadata": {"type": "document"},
        },
    }])


def extract(path: Path, *, with_nims: bool) -> tuple[list[dict], list[str]]:
    """Run nv-ingest's extraction stages over one document.

    Returns the elements and the stages that actually ran, so the row records
    what produced it rather than what was configured.
    """
    from nv_ingest_api.interface.extract import (
        extract_chart_data_from_image,
        extract_primitives_from_docx,
        extract_primitives_from_image,
        extract_primitives_from_pdf,
        extract_table_data_from_image,
    )

    suffix = path.suffix.lower()
    if suffix not in DOC_TYPES:
        # nv-ingest 26.3.0's extraction interface has no HTML backend, which is
        # a finding about the stack rather than a fault in the harness: the
        # blueprint ingests HTML through a different path entirely.
        raise ValueError(f"nv-ingest has no extraction backend for {suffix} files")
    document_type = DOC_TYPES[suffix]
    if not with_nims and document_type not in ("pdf", "docx"):
        # The image extractor calls Page Elements whatever `extract_tables` says
        # — correctly: an image has no text layer, so OCR is the only path to a
        # character. There is no NIM-free control for this row, and letting it
        # try costs seventeen minutes of DNS retries to arrive at the same fact.
        raise ValueError("nv-ingest has no NIM-free text path for an image: "
                         "every character on this page comes from the OCR NIM")

    page_ep, page_proto = endpoints("YOLOX", required=with_nims)
    token = auth_token()
    stages = ["text"]
    common = dict(extract_text=True, extract_images=False,
                  extract_tables=with_nims, extract_charts=with_nims,
                  extract_infographics=False,
                  yolox_endpoints=page_ep, auth_token=token)
    if with_nims:
        stages.append("page-elements")

    if document_type == "pdf":
        df = frame(extract_primitives_from_pdf(
            df_extraction_ledger=ledger(path, document_type),
            extract_method="pdfium", text_depth="page",
            yolox_infer_protocol=page_proto, **common))
    elif document_type == "docx":
        df = frame(extract_primitives_from_docx(
            df_ledger=ledger(path, document_type),
            yolox_infer_protocol=page_proto, **common))
    else:
        df = frame(extract_primitives_from_image(
            df_ledger=ledger(path, document_type),
            yolox_infer_protocol=page_proto, **common))

    if with_nims and len(df):
        table_ep, table_proto = endpoints("YOLOX_TABLE_STRUCTURE", required=True)
        ocr_ep, ocr_proto = endpoints("OCR", required=True)
        graphic_ep, graphic_proto = endpoints("YOLOX_GRAPHIC_ELEMENTS", required=True)
        df = frame(extract_table_data_from_image(
            df_ledger=df, yolox_endpoints=table_ep, yolox_protocol=table_proto,
            ocr_endpoints=ocr_ep, ocr_protocol=ocr_proto, auth_token=token))
        stages += ["table-structure", "ocr"]
        df = frame(extract_chart_data_from_image(
            df_ledger=df, yolox_endpoints=graphic_ep, yolox_protocol=graphic_proto,
            ocr_endpoints=ocr_ep, ocr_protocol=ocr_proto, auth_token=token))
        stages.append("graphic-elements")

    return df["metadata"].tolist(), stages


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")          # nv-ingest | nv-ingest:text
    ap.add_argument("document")
    ap.add_argument("out")
    a = ap.parse_args()
    name, _, variant = a.engine.partition(":")
    if name != "nv-ingest" or variant not in ("", "text"):
        raise SystemExit(f"unknown engine {a.engine!r}; expected nv-ingest or nv-ingest:text")
    with_nims = variant != "text"

    t0 = time.perf_counter()
    stages: list[str] = []
    try:
        elements, stages = extract(Path(a.document), with_nims=with_nims)
        ex = normalise(elements)
        err = None
    except Exception as e:                        # a stack that cannot read a
        ex = {"text": "", "tables": [], "_charts": 0}   # format is a result
        err = f"{type(e).__name__}: {e}"
    charts = ex.pop("_charts", 0)
    ex["_run"] = {
        "engine": a.engine,
        "document": os.path.basename(a.document),
        "wall_s": round(time.perf_counter() - t0, 2),
        "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "error": err,
        "charts_emitted": charts,
        "stages": stages,
        # What answered, for the record of what was measured. No credential.
        "nims": {p: os.environ.get(f"{p}_HTTP_ENDPOINT") or os.environ.get(f"{p}_GRPC_ENDPOINT")
                 for p in ("YOLOX", "YOLOX_TABLE_STRUCTURE", "YOLOX_GRAPHIC_ELEMENTS", "OCR")}
        if with_nims else {},
    }
    Path(a.out).write_text(json.dumps(ex, indent=1), encoding="utf-8")
    print(json.dumps(ex["_run"]))


if __name__ == "__main__":
    main()
