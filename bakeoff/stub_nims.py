#!/usr/bin/env python3
"""Four NIM-shaped endpoints that detect nothing, on one port.

The upstream run has two independent ways to fail, and they cost very different
amounts to discover: the plumbing (endpoints, protocol, bearer, response shape,
stage order) and the models. This stub removes the first one from the bill.

It answers every `POST` with a well-formed detection response containing no
detections, so `run_extract_nvingest.py` completes all five stages and returns
prose with zero tables. What that proves is the wire path — that the stages are
reached in order, that each endpoint is called, that the bearer is attached and
that the parser accepts the payload. It proves **nothing** about extraction
quality: a run against this stub is not a measurement and must never be reported
as one.

    python stub_nims.py 8599 &
    E=http://127.0.0.1:8599/v1/infer
    YOLOX_HTTP_ENDPOINT=$E YOLOX_TABLE_STRUCTURE_HTTP_ENDPOINT=$E \
    YOLOX_GRAPHIC_ELEMENTS_HTTP_ENDPOINT=$E OCR_HTTP_ENDPOINT=$E \
    NVIDIA_API_KEY=stub \
      python run_extract_nvingest.py nv-ingest corpus/born_digital.pdf out/stub.json

Each line it prints is one NIM call: the path, whether a bearer arrived, and how
many images were in the batch. A stage that never appears is a stage that was
never reached.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server's spelling
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            batch = max(1, len(json.loads(body).get("input", [])))
        except Exception:
            batch = 1
        authorised = self.headers.get("Authorization") is not None
        print(f"{self.path} auth={'yes' if authorised else 'no'} batch={batch}", flush=True)
        payload = json.dumps(
            {"data": [{"index": i, "bounding_boxes": {}} for i in range(batch)]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - the NIMs' readiness probe
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        """The access log would double every line printed above."""


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8599
    print(f"stub NIMs on 127.0.0.1:{port} — no detections, no measurement", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
