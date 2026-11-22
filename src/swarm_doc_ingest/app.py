# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface.

Three endpoints and one rule: **one document is extracted at a time.**

That is not laziness about concurrency, it is the memory measurement. A single
document peaks at 2.34 GiB of resident memory on a fifteen-page scan, and the
chart declares a 4Gi limit sized from it. Two in flight would exceed that limit
and the pod would be OOMKilled mid-request, which reads to a tenant as "the
ingestion service is broken" rather than as "the quota was wrong" — marketplace
trap 2, arrived at from the other end. Requests queue instead; the queue is
visible in `/v1/health` so an operator can see the reason for the latency.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from .config import PINNED_MODELS, SUPPORTED_SUFFIXES, Settings
from .elements import to_elements
from .pipeline import get_converter

log = logging.getLogger(__name__)
logging.basicConfig(
    level=os.environ.get("INGEST_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

#: Serialises extraction. See the module docstring: this is a memory bound, not
#: a throughput choice.
_extract_lock = asyncio.Semaphore(1)
_state = {"queued": 0, "extracted": 0, "failed": 0, "ready": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = Settings()
    s.check()
    # Build the converter at boot. Three models load in seconds, and a readiness
    # probe that goes green before they are loaded hands the first real document
    # a multi-second stall that looks like a hang.
    await asyncio.to_thread(get_converter, force_ocr=False, want_images=False)
    _state["ready"] = True
    log.info("ready: layout=%s table=%s ocr=%s", s.layout_preset, s.table_mode,
             s.ocr_engine)
    yield


app = FastAPI(
    title="Confidential document ingestion",
    description=(
        "Layout, table-structure and OCR extraction on open components, emitting "
        "the nv-ingest element contract. No NIM, no NGC credential, no GPU."
    ),
    version=os.environ.get("INGEST_VERSION", "0.1.0"),
    lifespan=lifespan,
)


def require_key(authorization: str = Header(default="")) -> None:
    """Bearer token, constant-time compared.

    The key arrives through the environment from a Secret and never through a
    command-line argument: a container's command is rendered into the manifests
    and published in the evidence snapshot in clear (marketplace trap 4).
    """
    expected = Settings().api_key
    presented = ""
    if authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if not expected or not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


@app.get("/v1/health")
async def health() -> JSONResponse:
    """Unauthenticated, because a kubelet probe carries no token."""
    body = {
        "ready": _state["ready"],
        "queued": _state["queued"],
        "extracted": _state["extracted"],
        "failed": _state["failed"],
    }
    return JSONResponse(body, status_code=200 if _state["ready"] else 503)


@app.get("/v1/config", dependencies=[Depends(require_key)])
async def config() -> dict:
    """What this deployment is actually running.

    The same facts are in the chart's ConfigMap and therefore in the signed
    evidence; this endpoint is how you check that the running process agrees
    with the document that was signed.
    """
    s = Settings()
    return {
        "version": app.version,
        "layout_preset": s.layout_preset,
        "table_mode": s.table_mode,
        "ocr_engine": s.ocr_engine,
        "force_full_page_ocr_default": s.force_full_page_ocr,
        "models": PINNED_MODELS,
        "offline": os.environ.get("HF_HUB_OFFLINE") == "1",
        "supported_formats": sorted(set(SUPPORTED_SUFFIXES.values())),
        "charts": "located, not transcribed — a chart is returned as an image "
                  "element with its bounding box",
    }


def _extract_sync(data: bytes, filename: str, *, force_ocr: bool,
                  want_images: bool, source_id: str, source_location: str):
    import tempfile

    conv = get_converter(force_ocr=force_ocr, want_images=want_images)
    suffix = Path(filename).suffix.lower()
    # Docling dispatches on the suffix, so the temporary file keeps it. Written
    # to disk rather than streamed because the PDF backends seek.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(data)
        doc = conv.convert(str(path)).document
    return to_elements(
        doc,
        source_name=filename,
        source_id=source_id,
        source_location=source_location,
        include_image_content=want_images,
    )


@app.post("/v1/extract", dependencies=[Depends(require_key)])
async def extract(
    file: UploadFile = File(...),
    force_ocr: bool = Query(
        default=False,
        description="OCR every page, even one that already has a text layer. "
                    "Costs about 3.5 s a page; needed for a PDF whose text "
                    "layer is present but wrong."),
    images: bool = Query(
        default=False,
        description="Return picture crops base64 in `content`. Off by default: "
                    "a catalogue of pictures is tens of megabytes of JSON."),
    source_location: str = Query(
        default="",
        description="Opaque pointer the composing listing uses to fetch the "
                    "original back for a citation. Echoed, never dereferenced."),
) -> dict:
    s = Settings()
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        # A 415 rather than an empty element list: "this produced nothing" and
        # "we do not read this format" are different answers to a tenant.
        raise HTTPException(
            status_code=415,
            detail=f"unsupported format {suffix or '(none)'}; supported: "
                   + ", ".join(sorted(SUPPORTED_SUFFIXES)))

    data = await file.read()
    if len(data) > s.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{len(data)} bytes exceeds the {s.max_upload_bytes}-byte limit")
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")

    source_id = str(uuid.uuid4())
    want_ocr = force_ocr or s.force_full_page_ocr

    _state["queued"] += 1
    started = time.perf_counter()
    try:
        async with _extract_lock:
            elements = await asyncio.to_thread(
                _extract_sync, data, filename,
                force_ocr=want_ocr,
                want_images=images or s.include_image_content,
                source_id=source_id, source_location=source_location)
    except HTTPException:
        raise
    except Exception as exc:
        _state["failed"] += 1
        log.exception("extraction failed for %s", filename)
        raise HTTPException(status_code=500,
                            detail=f"extraction failed: {type(exc).__name__}") from exc
    finally:
        _state["queued"] -= 1

    elapsed = time.perf_counter() - started
    _state["extracted"] += 1
    by_type: dict[str, int] = {}
    for el in elements:
        key = el.content_metadata.type.value if el.content_metadata else "unknown"
        by_type[key] = by_type.get(key, 0) + 1
    log.info("extracted %s: %d elements in %.2fs (%s)", filename, len(elements),
             elapsed, by_type)

    return {
        "source_id": source_id,
        "source_name": filename,
        "elements": [el.model_dump(mode="json", exclude_none=True)
                     for el in elements],
        "stats": {"elements": len(elements), "by_type": by_type,
                  "seconds": round(elapsed, 3), "forced_ocr": want_ocr},
    }
