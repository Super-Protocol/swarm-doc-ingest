# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface, with the extraction stage stubbed.

The converter is replaced by a stub that returns the hand-built document from
`conftest`. That keeps these tests free of the 536 MiB of baked weights, and it
is the only way to assert the serialisation and the refusals deterministically —
a real extraction's element count depends on a layout model's opinion.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

KEY = "test-key"


@pytest.fixture
def client(document, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("INGEST_API_KEY", KEY)

    from swarm_doc_ingest import app as app_module

    class StubConverter:
        calls: list[dict] = []

        def convert(self, path):
            class Result:
                pass
            r = Result()
            r.document = document
            return r

    def stub(*, force_ocr, want_images):
        StubConverter.calls.append({"force_ocr": force_ocr, "want_images": want_images})
        return StubConverter()

    monkeypatch.setattr(app_module, "get_converter", stub)
    StubConverter.calls.clear()
    with TestClient(app_module.app) as c:
        c.stub = StubConverter
        yield c


def auth():
    return {"Authorization": f"Bearer {KEY}"}


def test_health_needs_no_token(client):
    """A kubelet probe carries no credentials, so this endpoint cannot want one."""
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["ready"] is True


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer wrong"}, {"Authorization": KEY},
    {"Authorization": "Basic dGVzdC1rZXk="},
])
def test_extract_and_config_refuse_a_bad_token(client, headers):
    assert client.get("/v1/config", headers=headers).status_code == 401
    r = client.post("/v1/extract", headers=headers,
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 401


def test_config_reports_the_pins_a_deployment_is_running(client):
    """The same facts are in the chart's ConfigMap and therefore in the signed
    evidence. This endpoint is how you check the process agrees with it."""
    body = client.get("/v1/config", headers=auth()).json()
    assert body["layout_preset"] == "layout_heron_default"
    assert body["table_mode"] == "accurate"
    assert body["ocr_engine"] == "rapidocr"
    assert body["offline"] is True
    assert body["models"]["layout"]["revision"] == (
        "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8")
    assert "not transcribed" in body["charts"]


def test_an_unsupported_format_is_415_and_not_an_empty_result(client):
    """"This produced nothing" and "we do not read this format" are different
    answers to a tenant, so they get different status codes."""
    r = client.post("/v1/extract", headers=auth(),
                    files={"file": ("archive.zip", b"PK\x03\x04", "application/zip")})
    assert r.status_code == 415
    assert ".pdf" in r.json()["detail"]


def test_an_empty_upload_is_400(client):
    r = client.post("/v1/extract", headers=auth(),
                    files={"file": ("empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400


def test_an_oversized_upload_is_413(client, monkeypatch):
    monkeypatch.setenv("INGEST_MAX_UPLOAD_BYTES", "8")
    r = client.post("/v1/extract", headers=auth(),
                    files={"file": ("big.pdf", b"x" * 64, "application/pdf")})
    assert r.status_code == 413


def test_extract_returns_nv_ingest_elements(client):
    r = client.post("/v1/extract", headers=auth(),
                    files={"file": ("quarterly.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["source_name"] == "quarterly.pdf"
    assert body["stats"]["by_type"] == {"text": 2, "structured": 1, "image": 1}
    types = [e["content_metadata"]["type"] for e in body["elements"]]
    assert types == ["text", "text", "structured", "image"]
    # Every element names the upload, and the source id ties them together.
    assert {e["source_metadata"]["source_id"] for e in body["elements"]} == {
        body["source_id"]}
    # `exclude_none` keeps the metadata a type does not have off the wire.
    text = body["elements"][0]
    assert "table_metadata" not in text and "image_metadata" not in text


def test_force_ocr_and_images_reach_the_pipeline(client):
    client.post("/v1/extract?force_ocr=true&images=true", headers=auth(),
                files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert client.stub.calls[-1] == {"force_ocr": True, "want_images": True}


def test_source_location_is_echoed_and_never_dereferenced(client):
    """The composing listing uses it to fetch the original back for a citation.
    This service puts it on every element and makes no request with it."""
    r = client.post("/v1/extract?source_location=s3://bucket/key", headers=auth(),
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    locations = {e["source_metadata"]["source_location"] for e in r.json()["elements"]}
    assert locations == {"s3://bucket/key"}


def test_a_path_in_the_filename_cannot_escape(client):
    """`source_name` is echoed into every element and used to pick a suffix. A
    traversal in it would be written to a temporary path."""
    r = client.post("/v1/extract", headers=auth(),
                    files={"file": ("../../etc/passwd.pdf", b"%PDF-1.4",
                                    "application/pdf")})
    assert r.status_code == 200
    assert r.json()["source_name"] == "passwd.pdf"


def test_a_failing_extraction_is_a_500_that_leaks_nothing(client, monkeypatch):
    from swarm_doc_ingest import app as app_module

    class Boom:
        def convert(self, path):
            raise ValueError("/opt/docling-models/layout/model.safetensors is corrupt")

    monkeypatch.setattr(app_module, "get_converter", lambda **kw: Boom())
    r = client.post("/v1/extract", headers=auth(),
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 500
    # The type, not the message: a path or a filename in an error body is a
    # detail about the enclave's inside that the caller did not need.
    assert r.json()["detail"] == "extraction failed: ValueError"
    assert "safetensors" not in r.text
    assert client.get("/v1/health").json()["failed"] == 1
