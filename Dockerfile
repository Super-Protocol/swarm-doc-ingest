# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
#
# The ingestion image. Three properties it is built for, in order:
#
#   1. It makes no outbound request when it runs. The model set is baked and
#      HF_HUB_OFFLINE is 1, so a missing weight is a refusal at boot rather than
#      a download while a tenant's document is in memory.
#   2. It carries no proprietary or copyleft terms. torch comes from the CPU
#      index; the default wheel would add sixteen NVIDIA proprietary CUDA
#      packages to a component whose entire purpose is needing none, and
#      scripts/collect-licences.py fails the build if that ever happens.
#   3. It needs no GPU. Measured: 4 cores give up 15% of an 8-core run.
#
# Pinned by digest, because `3.12-slim-bookworm` is a moving tag.
ARG BASE=python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# ---------------------------------------------------------------- models ------
FROM ${BASE} AS models
ARG MODELS_DIR=/opt/docling-models
RUN pip install --no-cache-dir "huggingface_hub==1.33.0"
COPY scripts/prefetch-models.py /tmp/prefetch-models.py
# Hugging Face repositories pinned by commit, RapidOCR's ONNX files by sha256.
# A mismatch stops the build here; see the script for why each pin has the shape
# it does.
RUN python /tmp/prefetch-models.py "${MODELS_DIR}"

# ------------------------------------------------------------------ deps ------
FROM ${BASE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# torch first and from the CPU index alone, so no resolution can reach the
# default index for it. This is the single most consequential line in the file.
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
      "torch==2.15.0+cpu" "torchvision==0.29.2+cpu"

COPY pyproject.toml README.md /src/
COPY src /src/src
RUN pip install /src

COPY scripts/collect-licences.py /tmp/collect-licences.py
RUN python /tmp/collect-licences.py /licenses/

# ----------------------------------------------------------------- image ------
FROM ${BASE} AS runtime
ARG MODELS_DIR=/opt/docling-models
ARG VERSION=0.0.0-dev
ARG SOURCE_COMMIT=unknown

LABEL org.opencontainers.image.title="swarm-doc-ingest" \
      org.opencontainers.image.description="Confidential document ingestion on open components, emitting the nv-ingest element contract" \
      org.opencontainers.image.source="https://github.com/Super-Protocol/swarm-doc-ingest" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}"

# libgomp is OpenMP for onnxruntime and torch; libgl/libglib are what OpenCV's
# wheel links against.
#
# The fonts are not cosmetic, and leaving them out cost an afternoon. A slim
# image has no fonts at all. The PDF base-14 fonts — Helvetica, Times, Courier —
# are by specification *not embedded* in a document that uses them, so the
# renderer substitutes; with no font on the system and no fontconfig to ask, it
# substitutes badly. The text layer still reads perfectly, so extraction looks
# fine. What changes is the rendered page image, which is exactly what the
# layout model and the OCR engine see: on `tests/fixtures/hard_tables.pdf` this
# image found **0 of 2 tables** with no fonts installed and **2 of 2** with them,
# same model bytes, same options, same document. `scripts/smoke.sh` is what
# caught it and is why it asserts cells rather than a status code.
#
# fonts-liberation is the one that matters: metric-compatible with Helvetica,
# Arial, Times and Courier. fonts-dejavu-core is the broad-coverage fallback for
# everything else.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libgomp1 libgl1 libglib2.0-0 \
      fontconfig fonts-liberation fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f \
 && test "$(fc-list | wc -l)" -gt 0

COPY --from=deps /opt/venv /opt/venv
COPY --from=deps /licenses /licenses
COPY --from=models ${MODELS_DIR} ${MODELS_DIR}
COPY LICENSE /licenses/LICENSE
COPY licenses/ /licenses/models/

# A document is the sensitive asset here; the process that handles it owns
# nothing it can write to except a tmpfs the chart mounts.
RUN useradd --system --uid 10001 --create-home --home-dir /home/ingest ingest \
 && chown -R ingest:ingest /home/ingest
USER 10001

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOCLING_ARTIFACTS_PATH=${MODELS_DIR} \
    HF_HUB_OFFLINE=1 \
    HF_HOME=/home/ingest/.cache/huggingface \
    OMP_NUM_THREADS=4 \
    INGEST_VERSION=${VERSION}

EXPOSE 8080
# One worker on purpose. A document peaks at 2.34 GiB of resident memory and the
# chart's limit is 4Gi, sized from that; a second worker would double the demand
# and be OOMKilled mid-request, which reads as a broken service rather than as a
# wrong quota. The service queues instead, and says so in /v1/health.
CMD ["uvicorn", "swarm_doc_ingest.app:app", "--host", "0.0.0.0", "--port", "8080", \
     "--workers", "1", "--timeout-keep-alive", "75", "--no-access-log"]
