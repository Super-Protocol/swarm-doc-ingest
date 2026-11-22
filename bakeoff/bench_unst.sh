#!/bin/bash
# Unstructured over the same corpus, scored with the same evaluator.
set -u
cd "$(dirname "$0")"
U=../upstream/rag/data/multimodal
docs=(
 "corpus/born_digital.pdf corpus/ground_truth.json"
 "corpus/hard_tables.pdf corpus/ground_truth_hard.json"
 "corpus/report.docx corpus/ground_truth.json"
 "corpus/scanned.pdf corpus/ground_truth.json"
 "corpus/scanned_hard.pdf corpus/ground_truth_hard.json"
)
if [ -n "$U" ] && [ -n "$G" ]; then
  docs+=("$U/table_test.pdf $G/table_test.json"
         "$U/multimodal_test.pdf $G/multimodal_test.json"
         "$U/embedded_table.pdf $G/embedded_table.json")
else
  # Neither the upstream documents nor their transcribed grids are in this
  # repository, and must not be: see real-corpus.manifest.json.
  echo "note: skipping the upstream-corpus rows; set UPSTREAM_MULTIMODAL and INGEST_BAKEOFF_REAL_DIR to include them" >&2
fi
for d in "${docs[@]}"; do
  set -- $d; doc=$1; gt=$2; name=$(basename "$doc")
  printf '%-26s ' "$name"
  ( . .venv-unst/bin/activate; python run_extract_unst.py hi_res "$doc" "out/u_$name.json" ) 2>/dev/null \
    | ( . .venv/bin/activate; python -c "
import json,sys
d=json.load(sys.stdin); print(f\"{d['wall_s']}s {int(d['peak_rss_mib'])}MiB\", (d['error'] or '').splitlines()[0] if d['error'] else '', end=' ')" )
  ( . .venv/bin/activate; python evaluate.py "$gt" "out/u_$name.json" ) | ( . .venv/bin/activate; python -c "
import json,sys
d=json.load(sys.stdin)
cer='n/a' if d['text_cer'] is None else f\"{d['text_cer']:.4f}\"
print(f\"cer={cer} tbl={d['tables_emitted']}/{d['tables_expected']} f1={d['table_f1_mean']:.3f} cells={d['cell_set_f1_mean']:.3f}\")" )
done
