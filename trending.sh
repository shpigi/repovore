#!/usr/bin/env bash
# Fetch GitHub trending (weekly) repos and process them through repovore.
# GitHub has no official trending API — this parses the trending page directly.
set -euo pipefail

N=${1:-10}       # number of repos, default 10
SINCE=${2:-weekly}  # weekly | daily | monthly

echo "Fetching GitHub trending ($SINCE)..."

urls=$(uv run python - <<EOF
import re, sys, urllib.request

EXCLUDED = {
    "sponsors", "apps", "topics", "trending", "collections", "events",
    "marketplace", "settings", "notifications", "login", "signup", "about",
    "contact", "security", "status", "explore", "features", "pricing",
    "orgs", "users", "pulls", "issues", "discussions",
}

url = "https://github.com/trending?since=$SINCE"
req = urllib.request.Request(url, headers={
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept": "text/html",
})
try:
    content = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
except Exception as e:
    print(f"error: {e}", file=sys.stderr)
    sys.exit(1)

repos = re.findall(r'href="/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"', content)
seen = set()
unique = []
for r in repos:
    owner = r.split("/")[0].lower()
    if r not in seen and owner not in EXCLUDED:
        seen.add(r)
        unique.append(r)

if not unique:
    print("No repos parsed — page structure may have changed", file=sys.stderr)
    sys.exit(1)

for repo in unique[:$N]:
    print(f"https://github.com/{repo}")
EOF
)

if [[ -z "$urls" ]]; then
    exit 1
fi

echo "Found $(echo "$urls" | wc -l | tr -d ' ') repos:"
echo "$urls" | sed 's/^/  /'
echo ""

url_args=()
while IFS= read -r url; do
    url_args+=(--url "$url")
done <<< "$urls"

uv run repovore process "${url_args[@]}"
uv run repovore show-all
