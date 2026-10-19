#!/usr/bin/env bash
# Create the schema and landing Volume (first run only) and upload the silver Parquet files
# written by `python -m pipeline.cli export`. Uses the CLI's default profile.
#   bash databricks/upload.sh
set -euo pipefail
CATALOG="${CATALOG:-workspace}"
SCHEMA="${SCHEMA:-nfip}"
cd "$(dirname "$0")/.."

databricks schemas create "$SCHEMA" "$CATALOG" >/dev/null 2>&1 && echo "created schema $CATALOG.$SCHEMA" \
  || echo "schema $CATALOG.$SCHEMA exists"
databricks volumes create "$CATALOG" "$SCHEMA" landing MANAGED >/dev/null 2>&1 && echo "created volume landing" \
  || echo "volume landing exists"

for t in nfip_claims community_status policy_exposure_community_year; do
  f="data/export/$t.parquet"
  [ -f "$f" ] || { echo "missing $f: run python -m pipeline.cli export first" >&2; exit 1; }
  echo "uploading $f ($(du -h "$f" | cut -f1))"
  databricks fs cp --overwrite "$f" "dbfs:/Volumes/$CATALOG/$SCHEMA/landing/$t.parquet"
done
databricks fs ls "dbfs:/Volumes/$CATALOG/$SCHEMA/landing"
