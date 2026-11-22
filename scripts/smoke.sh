#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 Super Protocol
# SPDX-License-Identifier: Apache-2.0
#
# The gate on publishing an image.
#
# A render test and a readiness probe both pass on a build that extracts
# nothing. SUP-839 recorded that lesson about vLLM — nine releases that started,
# passed a health check, served at full speed and returned fluent nonsense — so
# what gates a release here is a real document through a real container, and the
# assertions are about cells that have to come out of it.
#
# Usage: scripts/smoke.sh <image-ref>
set -euo pipefail

IMAGE="${1:?usage: smoke.sh <image-ref>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES="${HERE}/tests/fixtures"
KEY="smoke-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
PORT="${SMOKE_PORT:-18080}"
NAME="swarm-doc-ingest-smoke-$$"
FAILED=0

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAILED=1; }

cleanup() {
  # Only ever the container this script started, by its own unique name.
  docker rm -f "${NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
say "Extraction with no network at all"
# The strongest claim this component makes is that it fetches no model at
# request time. The direct proof is to take the network away and extract
# anyway: --network none gives the container no interface but loopback, so a
# stack that reached for a recogniser could not hide it.
offline_out="$(docker run --rm --network none \
  -v "${FIXTURES}:/fixtures:ro" \
  -e INGEST_API_KEY=offline-probe \
  --entrypoint python "${IMAGE}" -c '
from swarm_doc_ingest.config import Settings
from swarm_doc_ingest.elements import to_elements
from swarm_doc_ingest.pipeline import get_converter
Settings().check()
doc = get_converter(force_ocr=False, want_images=False).convert(
    "/fixtures/hard_tables.pdf").document
els = to_elements(doc, source_name="hard_tables.pdf", source_id="offline")
tables = [e for e in els if e.content_metadata.type.value == "structured"]
print("TABLES", len(tables))
for t in tables:
    print("CELLS", t.table_metadata.table_content.replace("\n", " "))
')" || { bad "offline extraction exited non-zero"; echo "${offline_out}"; }

if [ "$(grep -c '^TABLES 2$' <<<"${offline_out}")" = "1" ]; then
  ok "two tables extracted with the network removed"
else
  bad "expected two tables with no network; got: $(grep '^TABLES' <<<"${offline_out}" || echo none)"
fi
for cell in Region Compute EMEA "412,900" Platform; do
  if grep -q -- "${cell}" <<<"${offline_out}"; then
    ok "cell present: ${cell}"
  else
    bad "cell missing from the extracted grids: ${cell}"
  fi
done

# ---------------------------------------------------------------------------
say "Serving"
docker run -d --name "${NAME}" -p "127.0.0.1:${PORT}:8080" \
  -e "INGEST_API_KEY=${KEY}" "${IMAGE}" >/dev/null

base="http://127.0.0.1:${PORT}"
for _ in $(seq 1 90); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${base}/v1/health" || true)" = "200" ]; then
    break
  fi
  sleep 2
done
if [ "$(curl -s -o /dev/null -w '%{http_code}' "${base}/v1/health")" = "200" ]; then
  ok "ready"
else
  bad "never became ready"
  docker logs "${NAME}" 2>&1 | tail -40
  exit 1
fi

code="$(curl -s -o /dev/null -w '%{http_code}' "${base}/v1/config")"
[ "${code}" = "401" ] && ok "config refuses an unauthenticated caller" \
                      || bad "config returned ${code} without a token"

cfg="$(curl -sf -H "Authorization: Bearer ${KEY}" "${base}/v1/config")"
for expect in '"table_mode":"accurate"' '"layout_preset":"layout_heron_default"' \
              '"ocr_engine":"rapidocr"' '"offline":true'; do
  if grep -q "${expect}" <<<"$(tr -d ' ' <<<"${cfg}")"; then
    ok "config reports ${expect}"
  else
    bad "config does not report ${expect}"
  fi
done

# ---------------------------------------------------------------------------
say "A document with a text layer"
body="$(curl -sf -H "Authorization: Bearer ${KEY}" \
  -F "file=@${FIXTURES}/hard_tables.pdf" "${base}/v1/extract")"
structured="$(python3 -c '
import json,sys
d=json.load(sys.stdin)
print(sum(1 for e in d["elements"] if e["content_metadata"]["type"]=="structured"))' \
  <<<"${body}")"
[ "${structured}" = "2" ] && ok "two structured elements over HTTP" \
                          || bad "expected 2 structured elements, got ${structured}"
grep -q "412,900" <<<"${body}" && ok "a known cell survived the round trip" \
                               || bad "the known cell is missing"
# The failure this component exists to avoid: a table read as prose.
prose="$(python3 -c '
import json,sys
d=json.load(sys.stdin)
print(" ".join(e["content"] for e in d["elements"]
               if e["content_metadata"]["type"]=="text"))' <<<"${body}")"
grep -q "412,900" <<<"${prose}" && bad "table cells leaked into a text element" \
                                || ok "no table cell appears in any text element"

# ---------------------------------------------------------------------------
say "A 200-dpi scan, which has no text layer at all"
scan="$(curl -sf -H "Authorization: Bearer ${KEY}" \
  -F "file=@${FIXTURES}/scanned_hard.pdf" "${base}/v1/extract")"
scan_tables="$(python3 -c '
import json,sys
d=json.load(sys.stdin)
print(sum(1 for e in d["elements"] if e["content_metadata"]["type"]=="structured"))' \
  <<<"${scan}")"
[ "${scan_tables}" = "2" ] && ok "two structured elements from the scan" \
                           || bad "expected 2 structured elements from the scan, got ${scan_tables}"
grep -q "412,900" <<<"${scan}" && ok "OCR recovered a known cell" \
                               || bad "OCR did not recover the known cell"

say "Result"
if [ "${FAILED}" = "0" ]; then
  echo "  everything passed"
else
  echo "  something failed; container logs follow"
  docker logs "${NAME}" 2>&1 | tail -60
fi
exit "${FAILED}"
