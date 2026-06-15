#!/usr/bin/env bash
set -euo pipefail

artifacthub_api_url="${ARTIFACTHUB_API_URL:-https://artifacthub.io/api/v1}"
artifacthub_org="${ARTIFACTHUB_ORG:-keiailab}"
artifacthub_repository_name="${ARTIFACTHUB_REPOSITORY_NAME:-keiailab}"
artifacthub_display_name="${ARTIFACTHUB_DISPLAY_NAME:-KeiaiLab Helm Charts}"
helm_repo_url="${HELM_REPO_URL:-https://keiailab.github.io/charts}"

api_key_id="${ARTIFACTHUB_API_KEY_ID:-${AH_API_KEY_ID:-}}"
api_key_secret="${ARTIFACTHUB_API_KEY_SECRET:-${AH_API_KEY_SECRET:-}}"

if [[ -z "$api_key_id" || -z "$api_key_secret" ]]; then
	echo "ERROR: ARTIFACTHUB_API_KEY_ID/AH_API_KEY_ID and ARTIFACTHUB_API_KEY_SECRET/AH_API_KEY_SECRET are required." >&2
	exit 1
fi

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/keiailab-artifacthub-register.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

jq -n \
	--arg name "$artifacthub_repository_name" \
	--arg displayName "$artifacthub_display_name" \
	--arg url "${helm_repo_url%/}" \
	'{kind: 0, name: $name, display_name: $displayName, url: $url}' >"$tmpdir/body.json"

curl -fsSL \
	-X POST "${artifacthub_api_url%/}/repositories/org/${artifacthub_org}" \
	-H "Content-Type: application/json" \
	-H "X-API-KEY-ID: ${api_key_id}" \
	-H "X-API-KEY-SECRET: ${api_key_secret}" \
	-d @"$tmpdir/body.json" \
	-o "$tmpdir/created.json"

echo "Artifact Hub repository registration requested: ${artifacthub_repository_name} -> ${helm_repo_url%/}"
cat "$tmpdir/created.json"
