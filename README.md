# swarm-doc-ingest

Document ingestion for confidential deployments: layout analysis, table-structure
recognition and OCR on open components, emitting the element contract the NVIDIA RAG
reference architecture's retrieval stage already reads.

It replaces `nemo-microservices/nv-ingest` and the four extraction NIMs — Nemotron Page
Elements v3, Table Structure v1, Graphic Elements v1 and Nemotron OCR — with **no NIM
container, no NGC credential and no GPU**. Built for SUP-874 in the Confidential
Blueprints epic; the Helm chart that composes it lives in
[`swarm-marketplace-catalog`](https://github.com/Super-Protocol/swarm-marketplace-catalog).

NVIDIA is a trademark of NVIDIA Corporation. This is our own implementation of a
published contract, not NVIDIA software; it is not produced, endorsed or supported by
NVIDIA.

## What it does, and the one thing it does not

| | |
|---|---|
| **In** | PDF, DOCX, PPTX, XLSX, HTML, Markdown, plain text, and PNG / JPEG / TIFF / BMP scans |
| **Out** | a list of nv-ingest `MetadataSchema` elements: text, tables as cell grids *and* Markdown, images with their bounding boxes |
| **Tables** | extracted as structure, never flattened into prose — the distinction is the whole point, and the measurement below says why |
| **Charts** | **located, not transcribed.** A chart comes back as an image element with its bounding box, not as a table of its series. Transcribing one needs a vision-language model on a GPU, and this component is deliberately CPU-only |
| **Network** | none at run time. The model set is baked into the image and `HF_HUB_OFFLINE=1`, so a missing weight is a refusal at boot, not a download while a tenant's document is in memory |
| **GPU** | not used, not wanted |

## The stack, and what choosing it cost

Decided by measurement in [`bakeoff/`](bakeoff/), not by reading model cards. The
control row is the argument for the whole component: raw PDF text extraction reads
born-digital prose at a character error rate of **0.0000** and recovers **0 tables out
of 19** across seven PDFs.

| Stage | Chosen | Licence | Why this one |
|---|---|---|---|
| Framework | Docling 2.151.1 | MIT | mean table F1 **0.844** against Unstructured's **0.332** on the same ten documents; **0.935** against **0.238** on the reference architecture's own three |
| Layout | `docling-layout-heron` | Apache-2.0 | the only preset of five that reads a table drawn without ruling lines as a table (0.968) rather than as prose (0.000) |
| Table structure | TableFormer, `accurate` | CDLA-Permissive-2.0 + Apache-2.0 | 0.956 against `fast`'s 0.491 on a 21-row table |
| OCR | RapidOCR, PP-OCRv6 ONNX | Apache-2.0 | character error rate 0.0000 against EasyOCR's 0.012–0.020, at 2.0 GiB of memory instead of 5.1 GiB and half the time |

Where each of those choices loses, and to what, is in `bakeoff/README.md`. None of them
is free and the sweep records the price.

### Measured footprint

8 vCPU, no accelerator, CPU-only PyTorch wheels:

| Workload | Pages | Wall | Per page | Peak RSS |
|---|---|---|---|---|
| HTML, DOCX | — | 2.6 s | — | 0.37 GiB |
| PDF with a text layer | 15 | 13.1 s | 0.87 s | 1.96 GiB |
| 200-dpi scan, OCR | 15 | 52.6 s | 3.5 s | 2.34 GiB |

Memory is bounded rather than linear in pages — Docling works a page at a time. CPU
scaling on the scan: 8 cores 51.7 s, **4 cores 59.4 s**, 2 cores 93.1 s, 1 core 112.7 s.
Four cores give up 15% of the eight-core time for half the quota, which is why the chart
asks for `cpu: 4` and `memory: 4Gi`.

## Running it

```bash
docker run --rm -p 8080:8080 \
  -e INGEST_API_KEY=dev-key-not-for-production \
  ghcr.io/super-protocol/swarm-doc-ingest/ingest@sha256:<digest>

curl -s localhost:8080/v1/health
curl -s -H 'Authorization: Bearer dev-key-not-for-production' localhost:8080/v1/config
curl -s -H 'Authorization: Bearer dev-key-not-for-production' \
     -F file=@report.pdf 'localhost:8080/v1/extract' | jq '.stats'
```

### Endpoints

| | |
|---|---|
| `GET /v1/health` | unauthenticated, because a kubelet probe carries no token. 503 until the models are loaded, then the queue depth and the counts |
| `GET /v1/config` | what this deployment is running: the model pins, the presets, whether the hub is off. The same facts are in the chart's ConfigMap and therefore in the signed evidence — this is how you check the process agrees with the document that was signed |
| `POST /v1/extract` | multipart `file`, with `force_ocr`, `images` and `source_location` as query parameters |

### Configuration

Everything but the key comes from the environment and is rendered into the chart's
ConfigMap, so it lands in the deployment's evidence. The key arrives through a Secret: a
container's command line is published in the evidence snapshot in clear.

| Variable | Default | |
|---|---|---|
| `INGEST_API_KEY` | — | **required.** A namespace is not a trust boundary once a second workload is in it |
| `DOCLING_ARTIFACTS_PATH` | `/opt/docling-models` | the baked model set. An empty path is a boot failure, never a download |
| `HF_HUB_OFFLINE` | `1` in the image | must be `1`. Checked at boot |
| `INGEST_LAYOUT_PRESET` | `layout_heron_default` | re-run the bake-off before changing it |
| `INGEST_TABLE_MODE` | `accurate` | `fast` loses two thirds of a wide table |
| `INGEST_OCR_ENGINE` | `rapidocr` | or `off` |
| `INGEST_FORCE_FULL_PAGE_OCR` | `false` | OCR even where a text layer exists; ~3.5 s a page |
| `INGEST_INCLUDE_IMAGE_CONTENT` | `false` | return picture crops base64 in `content` |
| `INGEST_MAX_UPLOAD_BYTES` | `268435456` | |

## One document at a time

The service extracts one document at a time and queues the rest. That is the memory
measurement, not an unfinished feature: one document peaks at 2.34 GiB against a 4Gi
limit sized from it, so two in flight would be OOMKilled mid-request — which reads to a
tenant as a broken service rather than as a wrong quota. The queue depth is in
`/v1/health` so the latency has a visible cause.

Scale by replicas, not by workers.

## The element contract

`src/swarm_doc_ingest/nv_ingest_schema.py` is NVIDIA's own schema, Apache-2.0, reduced
to the fields this service sets and with `extra="forbid"` kept. A consumer written
against nv-ingest accepts our output unchanged; that is the acceptance criterion, and
keeping the schema file rather than paraphrasing it is how it stays true.

One upstream defect found while reading it, recorded so it is not mistaken for ours: the
retrieval stage reads `content_metadata.location` for image and table citations, and
`ContentMetadataSchema` does not define that field and forbids extras. It resolves to
`None` upstream for every citation. Read `table_metadata.table_location` instead.

## Licences

Apache-2.0 (`LICENSE`). The image carries `/licenses/`: this licence, a build-time
manifest of every Python distribution and its terms, the licence texts the wheels
shipped, and `licenses/MODELS.md` for the three model sets and their pins.
`scripts/collect-licences.py` **fails the build** on copyleft or an unidentifiable
licence — which is also the tripwire for the default PyTorch wheel, since it would drag
sixteen NVIDIA proprietary CUDA packages into a component built to need none.
