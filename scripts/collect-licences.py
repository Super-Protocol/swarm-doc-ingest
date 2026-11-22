#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
"""Write /licenses/ into the image.

`docs/ported-listings.md` §2.5 in the catalog: an image is redistribution in
object form, so the licences of what it carries go inside it. This runs at build
time against the environment that was actually installed, so the manifest cannot
drift from the wheels in the image the way a hand-written list does.

It also **fails the build** on a licence the epic does not accept. Copyleft or an
unidentifiable licence in a component whose whole selling point is that it needs
no proprietary entitlement is worth a red build, not a footnote: the default
`pip install docling` pulls sixteen NVIDIA proprietary CUDA wheels, and the only
thing standing between that and this image is the CPU index in the Dockerfile.
This check is what notices if that line is ever dropped.
"""

from __future__ import annotations

import shutil
import sys
from importlib.metadata import distributions
from pathlib import Path

#: Substrings that fail the build. Not a licence parser — a tripwire for the
#: three ways this image could acquire terms it must not have. "GPL" catches
#: LGPL as well, which is deliberate: the LGPL is not unusable here, but it is
#: not something to acquire by accident through a transitive dependency either.
#: EasyOCR pulls `python-bidi` (LGPL), which is one more reason the bake-off's
#: OCR decision went the way it did.
FORBIDDEN = ("GPL", "AGPL", "SSPL", "SSPL-1", "Elastic", "Proprietary",
             "BUSL", "Commons Clause", "NonCommercial", "CC-BY-NC")

#: Licences that are fine and need no further thought.
PERMISSIVE_HINTS = ("MIT", "BSD", "Apache", "ISC", "PSF", "Python-2.0",
                    "MPL-2.0", "Zlib", "0BSD", "CC0", "Unlicense", "CDLA",
                    "LGPL-is-not-here")


def licence_of(dist) -> str:
    """Prefer `License-Expression`, then classifiers, then legacy `License`.

    `License-Expression` is authoritative whatever its length: it is a normalised
    SPDX expression by definition, and a compound one is long rather than
    suspect — torch declares eight licences in 103 characters, and a length
    heuristic applied to it reports the one dependency nobody would doubt as
    unidentified. The legacy free-text `License` field is the one worth
    distrusting, because wheels have been known to paste the whole licence into
    it.
    """
    md = dist.metadata
    expression = (md.get("License-Expression") or "").strip()
    if expression:
        return expression
    classifiers = [c for c in (md.get_all("Classifier") or [])
                   if c.startswith("License ::")]
    if classifiers:
        return "; ".join(c.split("::")[-1].strip() for c in classifiers)
    legacy = (md.get("License") or "").strip()
    if legacy and len(legacy) <= 60 and "\n" not in legacy:
        return legacy
    return "UNKNOWN"


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/licenses")
    out.mkdir(parents=True, exist_ok=True)

    rows, problems = [], []
    for dist in sorted(distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"]
        if not name:
            continue
        lic = licence_of(dist)
        rows.append((name, dist.version, lic))
        upper = lic.upper()
        if any(bad.upper() in upper for bad in FORBIDDEN):
            problems.append(f"{name} {dist.version}: {lic}")
        elif lic == "UNKNOWN":
            problems.append(f"{name} {dist.version}: licence could not be determined")

        # Copy whatever licence text the wheel shipped, so /licenses/ holds the
        # terms and not only their names.
        for path in (dist.files or []):
            parts = str(path).replace("\\", "/").split("/")
            if len(parts) >= 2 and parts[-2] == "licenses" or (
                    parts[-1].upper().startswith(("LICENSE", "COPYING", "NOTICE"))):
                src = dist.locate_file(path)
                try:
                    if not Path(src).is_file():
                        continue
                    dest = out / "python" / name
                    dest.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest / parts[-1])
                except OSError:
                    continue

    manifest = out / "THIRD_PARTY.md"
    lines = [
        "# Third-party components in this image",
        "",
        "Generated at build time from the installed environment by",
        "`scripts/collect-licences.py`. Licence texts, where the wheel shipped one,",
        "are under `python/<distribution>/`. Model weights and their licences are",
        "under `models/`.",
        "",
        "| Distribution | Version | Licence |",
        "|---|---|---|",
    ]
    lines += [f"| `{n}` | {v} | {l} |" for n, v, l in rows]
    lines += ["", f"{len(rows)} Python distributions.", ""]
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {manifest} ({len(rows)} distributions)")

    if problems:
        print("\nREFUSING THE BUILD — licences this image must not carry:",
              file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("\nIf torch came from the default index rather than "
              "https://download.pytorch.org/whl/cpu, that is the cause: the "
              "CUDA wheels are NVIDIA proprietary and this component exists to "
              "need none of them.", file=sys.stderr)
        return 1
    print("no forbidden or unidentified licences")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
