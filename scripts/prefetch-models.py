#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""Bake the model set into the image, pinned.

Run at build time only. The image that comes out never fetches a model again:
`HF_HUB_OFFLINE=1` and Docling's `artifacts_path` make a missing weight a
refusal at boot instead of a download at request time, which is the acceptance
criterion this script exists to satisfy — *no OCR model may be fetched at
request time from a remote host*.

**Why baked rather than fetched by an init container,** which is what
`swarm-model-server` does for LLM weights:

|                        | Baked *(chosen here)*        | Fetched against a manifest   |
|------------------------|------------------------------|------------------------------|
| What pins the bytes    | the image digest             | sha256 in the attested ConfigMap |
| Size                   | 536 MiB on a 1.8 GiB runtime | 536 MiB off the image        |
| Egress from the enclave| **none, ever**               | the model hosts, at first boot |
| Redistribution         | we republish the weights     | the tenant's deployment fetches them |
| First boot             | instant                      | a download and a hash        |

The model server fetches because 29 GiB cannot be baked by a GitHub-hosted
runner and because republishing Meta's and Google's weights raises a question
nobody has answered. Neither applies here: 536 MiB builds fine, and all three
licences — Apache-2.0, CDLA-Permissive-2.0, Apache-2.0 — permit redistribution.
So we take the stronger property, which is that a deployment handling documents
somebody cannot export makes no outbound request at all.

Two sources, two kinds of pin, because they publish differently:

* Hugging Face repositories are pinned by **commit**. Docling's own model specs
  say `revision="main"`, which is mutable, and a mutable revision beside an
  attested render means the evidence names bytes that can change under it.
* RapidOCR publishes its ONNX files as plain URLs on ModelScope under a release
  tag. A tag can be re-pointed, so these are pinned by **sha256 of the file**
  and the build fails on a mismatch. ModelScope is the only ungated source —
  `RapidAI/RapidOCR` on Hugging Face returns 401 — and it is reached at build
  time only, never from a running pod.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.request
from pathlib import Path

HF_MODELS = [
    # (repo, commit, the folder name Docling resolves under artifacts_path)
    ("docling-project/docling-layout-heron",
     "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
     "docling-project--docling-layout-heron"),
    ("docling-project/docling-models",
     "2199320848bb9a8a519d22e4b528185a4f9a6f64",
     "docling-project--docling-models"),
]

#: Keep the layout model's safetensors and config; drop the README's example
#: images. Keep only the TableFormer variant this build runs.
HF_ALLOW = ["*.safetensors", "*.json", "*.txt", "*.bin", "*.model"]

RAPIDOCR_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx"
RAPIDOCR_FILES = [
    ("PP-OCRv6/det/PP-OCRv6_det_small.onnx", "PP-OCRv6_det_small.onnx",
     "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"),
    ("PP-OCRv6/rec/PP-OCRv6_rec_small.onnx", "PP-OCRv6_rec_small.onnx",
     "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"),
    ("PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
     "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
     "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
]

#: Nothing is pruned, and the first draft of this file got that wrong.
#:
#: TableFormer ships both variants and this build *runs* `accurate` — measured at
#: 0.956 against `fast`'s 0.491 on a 21-row table. Dropping the unused one saved
#: 139 MiB on a 4.2 GiB image and left the chart offering `tableMode: fast` as a
#: value that renders, deploys and then fails at the first document. An option
#: that cannot run is worse than an option nobody picks.
PRUNE: list[str] = []


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def fetch_hf(out: Path) -> None:
    from huggingface_hub import snapshot_download

    for repo, commit, folder in HF_MODELS:
        dest = out / folder
        print(f"-- {repo}@{commit[:8]} -> {folder}", flush=True)
        snapshot_download(repo_id=repo, revision=commit, local_dir=str(dest),
                          allow_patterns=HF_ALLOW)
        # The hub leaves a .cache tree of locks and metadata beside the weights.
        # It is build scaffolding, and in a baked image it is 20 MiB of noise in
        # the layer whose digest a reviewer reads.
        shutil.rmtree(dest / ".cache", ignore_errors=True)


def fetch_rapidocr(out: Path) -> None:
    dest = out / "RapidOcr"
    dest.mkdir(parents=True, exist_ok=True)
    for remote, name, want in RAPIDOCR_FILES:
        target = dest / name
        url = f"{RAPIDOCR_BASE}/{remote}"
        print(f"-- {url}", flush=True)
        with urllib.request.urlopen(url, timeout=300) as resp:  # noqa: S310
            target.write_bytes(resp.read())
        got = sha256(target)
        if got != want:
            target.unlink(missing_ok=True)
            raise SystemExit(
                f"{name}: sha256 {got} does not match the pin {want}. The tag "
                "this is fetched from is mutable; if upstream has republished, "
                "re-measure with bakeoff/bench.py before moving the pin.")
        print(f"   sha256 ok ({target.stat().st_size / 1048576:.1f} MiB)")


def prune(out: Path) -> None:
    for rel in PRUNE:
        for folder in out.glob(f"*/{rel}"):
            print(f"-- pruning {folder.relative_to(out)}")
            shutil.rmtree(folder, ignore_errors=True)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/docling-models")
    out.mkdir(parents=True, exist_ok=True)
    fetch_hf(out)
    fetch_rapidocr(out)
    prune(out)
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"\nbaked {total / 1048576:.1f} MiB into {out}")
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.stat().st_size > 1_000_000:
            print(f"  {p.relative_to(out)}  {p.stat().st_size / 1048576:.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
