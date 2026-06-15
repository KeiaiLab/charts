#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
catalog_file="${CATALOG_FILE:-${repo_root}/catalog.yaml}"
helm_oci_repo="${HELM_OCI_REPO:-oci://ghcr.io/keiailab/charts}"
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/keiailab-charts-index.XXXXXX")"

trap 'rm -rf "$tmpdir"' EXIT

if ! command -v helm >/dev/null 2>&1; then
	echo "ERROR: helm is required." >&2
	exit 1
fi

if ! command -v ruby >/dev/null 2>&1; then
	echo "ERROR: ruby is required for YAML rewriting." >&2
	exit 1
fi

mkdir -p "$tmpdir/packages"

ruby -ryaml -e '
doc = YAML.safe_load(File.read(ARGV.fetch(0)))
doc.fetch("charts").each do |chart|
  puts [chart.fetch("name"), chart.fetch("version")].join("\t")
end
' "$catalog_file" >"$tmpdir/charts.tsv"

while IFS=$'\t' read -r chart_name chart_version; do
	echo "==> pull ${helm_oci_repo%/}/${chart_name}:${chart_version}"
	helm pull "${helm_oci_repo%/}/${chart_name}" \
		--version "$chart_version" \
		--destination "$tmpdir/packages"
done <"$tmpdir/charts.tsv"

helm repo index "$tmpdir/packages"

HELM_OCI_REPO="${helm_oci_repo%/}" ruby -ryaml -rtime -e '
index_path = ARGV.fetch(0)
output_path = ARGV.fetch(1)
oci_repo = ENV.fetch("HELM_OCI_REPO").sub(%r{/+\z}, "")
index = YAML.safe_load(File.read(index_path), permitted_classes: [Time], aliases: true)

index.fetch("entries").each do |chart_name, versions|
  versions.each do |entry|
    entry["urls"] = ["#{oci_repo}/#{chart_name}:#{entry.fetch("version")}"]
  end
end

index["generated"] = Time.now.utc.iso8601
File.write(output_path, YAML.dump(index))
' "$tmpdir/packages/index.yaml" "$repo_root/index.yaml"

echo "Updated ${repo_root}/index.yaml"
