#!/usr/bin/env bash
# Reprocess all previously fetched repos from scratch (--force).
set -euo pipefail

DB="${REPOVORE_DATA_DIR:-data}/repovore.db"

if [[ ! -f "$DB" ]]; then
    echo "Database not found at $DB" >&2
    exit 1
fi

urls=$(sqlite3 "$DB" "SELECT url FROM repos ORDER BY project_path;")

if [[ -z "$urls" ]]; then
    echo "No repos found in database." >&2
    exit 1
fi

echo "Reprocessing $(echo "$urls" | wc -l | tr -d ' ') repos..."

url_args=()
while IFS= read -r url; do
    url_args+=(--url "$url")
done <<< "$urls"

uv run repovore process "${url_args[@]}" --force "$@"
