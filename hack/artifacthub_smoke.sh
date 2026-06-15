#!/usr/bin/env bash
set -euo pipefail

artifacthub_api_url="${ARTIFACTHUB_API_URL:-https://artifacthub.io/api/v1}"
artifacthub_repository_name="${ARTIFACTHUB_REPOSITORY_NAME:-keiailab}"
expected_repository_url="${EXPECTED_ARTIFACTHUB_REPOSITORY_URL:-https://keiailab.github.io/charts}"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/keiailab-artifacthub-smoke.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

curl -fsSL "${artifacthub_api_url%/}/packages/search?ts_query_web=keiailab&sort=relevance&page=1" \
	-o "$tmpdir/search.json"

jq -e \
	--arg repo "$artifacthub_repository_name" \
	--arg url "${expected_repository_url%/}" \
	'.packages
	 | map(select(.repository.name == $repo and (.repository.url | sub("/+$"; "") == $url)))
	 | length >= 4' "$tmpdir/search.json" >/dev/null

jq -r \
	--arg repo "$artifacthub_repository_name" \
	'.packages[]
	 | select(.repository.name == $repo)
	 | [.name, .version, .repository.name, .repository.url]
	 | @tsv' "$tmpdir/search.json"
