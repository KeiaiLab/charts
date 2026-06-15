#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
catalog_file="${CATALOG_FILE:-${repo_root}/catalog.yaml}"
index_file="${INDEX_FILE:-${repo_root}/index.yaml}"
helm_oci_repo="${HELM_OCI_REPO:-oci://ghcr.io/keiailab/charts}"

if [[ ! -f "$index_file" ]]; then
	echo "ERROR: index file not found: $index_file" >&2
	exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
	echo "ERROR: python3 is required for local HTTP verification." >&2
	exit 1
fi

ruby -ryaml -e '
catalog = YAML.safe_load(File.read(ARGV.fetch(0)))
index = YAML.safe_load(File.read(ARGV.fetch(1)), permitted_classes: [Time], aliases: true)
repo = ARGV.fetch(2).sub(%r{/+\z}, "")

expected = catalog.fetch("charts").map { |chart| [chart.fetch("name"), chart.fetch("version")] }
entries = index.fetch("entries")

expected.each do |name, version|
  entry = entries.fetch(name).find { |candidate| candidate.fetch("version") == version }
  raise "missing #{name}:#{version}" unless entry

  urls = entry.fetch("urls")
  expected_url = "#{repo}/#{name}:#{version}"
  raise "unexpected urls for #{name}: #{urls.inspect}" unless urls == [expected_url]
end

unexpected = entries.keys - expected.map(&:first)
raise "unexpected chart entries: #{unexpected.join(", ")}" unless unexpected.empty?

puts "index OK: #{expected.length} charts point to #{repo}/<chart>:<version>"
' "$catalog_file" "$index_file" "${helm_oci_repo%/}"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/keiailab-charts-helm.XXXXXX")"
server_pid=""

cleanup() {
	if [[ -n "$server_pid" ]]; then
		kill "$server_pid" >/dev/null 2>&1 || true
		wait "$server_pid" 2>/dev/null || true
	fi
	rm -rf "$tmpdir"
}
trap cleanup EXIT

port="$(ruby -rsocket -e 'server = TCPServer.new("127.0.0.1", 0); puts server.addr[1]; server.close')"
python3 -m http.server "$port" --bind 127.0.0.1 --directory "$repo_root" >"$tmpdir/http.log" 2>&1 &
server_pid="$!"

for _ in 1 2 3 4 5; do
	if curl -fsSL "http://127.0.0.1:${port}/index.yaml" >/dev/null 2>&1; then
		break
	fi
	sleep 1
done

HELM_CONFIG_HOME="$tmpdir/config" \
HELM_CACHE_HOME="$tmpdir/cache" \
HELM_DATA_HOME="$tmpdir/data" \
helm repo add keiailab "http://127.0.0.1:${port}" >/dev/null

HELM_CONFIG_HOME="$tmpdir/config" \
HELM_CACHE_HOME="$tmpdir/cache" \
HELM_DATA_HOME="$tmpdir/data" \
helm search repo keiailab --versions --devel

ruby -ryaml -e '
doc = YAML.safe_load(File.read(ARGV.fetch(0)))
doc.fetch("charts").each do |chart|
  puts [chart.fetch("name"), chart.fetch("version")].join("\t")
end
' "$catalog_file" | while IFS=$'\t' read -r chart_name chart_version; do
	HELM_CONFIG_HOME="$tmpdir/config" \
	HELM_CACHE_HOME="$tmpdir/cache" \
	HELM_DATA_HOME="$tmpdir/data" \
	helm show chart "keiailab/${chart_name}" --version "$chart_version" >/dev/null
	echo "helm show chart OK: keiailab/${chart_name}:${chart_version}"
done
