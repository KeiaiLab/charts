#!/usr/bin/env python3
"""발행 일관성 상시 감시 — 카탈로그의 전 chart 에 대해 4채널 대조.

    catalog.yaml  ↔  ghcr OCI chart  ↔  Pages index  ↔  GitHub 최신 태그

왜 필요한가: 각 OSS repo 는 릴리스마다 chart 를 수동 발행해야 해서, 코드/이미지가
앞서가고 공개 chart 만 조용히 뒤처진다(2026-07-21 실측: 라이브 qdrant-operator v0.6.0
인데 공개 chart 0.4.0 — 2버전 잠복). 릴리스 스크립트가 누락을 막고, 이 스크립트가
이미 벌어진 drift 를 잡는다.

의존 0(표준 라이브러리만) + 원격 HTTP 만 읽으므로 로컬과 클러스터 CronJob 에서 동일하게
동작한다. drift 가 있으면 exit 1.

환경변수:
  STRICT_TAG=0     GitHub 최신 태그 뒤처짐을 경고로만 (기본 1 = 실패)
  CATALOG_URL      기본 https://raw.githubusercontent.com/KeiaiLab/charts/main/catalog.yaml
  INDEX_URL        기본 https://keiailab.github.io/charts/index.yaml
  GITHUB_ORG       기본 KeiaiLab
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

CATALOG_URL = os.environ.get(
    "CATALOG_URL", "https://raw.githubusercontent.com/KeiaiLab/charts/main/catalog.yaml"
)
INDEX_URL = os.environ.get("INDEX_URL", "https://keiailab.github.io/charts/index.yaml")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "KeiaiLab")
STRICT_TAG = os.environ.get("STRICT_TAG", "1") == "1"
TIMEOUT = 20


def http(url, headers=None, want_json=False):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            return json.loads(body) if want_json else body.decode()
    except Exception:
        return None


def head_status(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def parse_catalog(text):
    """catalog.yaml → [(name, version)] — 단순 리스트라 정규식으로 충분."""
    out, name = [], None
    for line in text.splitlines():
        m = re.match(r"\s*-\s*name:\s*(\S+)", line)
        if m:
            name = m.group(1)
            continue
        v = re.match(r"\s*version:\s*(\S+)", line)
        if v and name:
            out.append((name, v.group(1)))
            name = None
    return out


def parse_index(text):
    """index.yaml → {chart: (version, appVersion)}.

    chart 항목의 필드는 정확히 4칸 들여쓰기다. CRD 어노테이션 블록 안에도
    'version: v1alpha1' 이 있어(mongodb/valkey 실측) 느슨한 매칭은 그걸 잡는다.
    """
    data, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^  ([a-z0-9-]+):\s*$", line)
        if m:
            cur = m.group(1)
            data.setdefault(cur, {})
            continue
        if not cur:
            continue
        for key in ("version", "appVersion"):
            f = re.match(r"^    " + key + r":\s*(\S+)\s*$", line)
            if f and key not in data[cur]:
                data[cur][key] = f.group(1).strip('"')
    return {k: (v.get("version"), v.get("appVersion")) for k, v in data.items()}


def latest_tag(repo):
    tags = http(f"https://api.github.com/repos/{GITHUB_ORG}/{repo}/tags?per_page=100", want_json=True)
    if not isinstance(tags, list):
        return None
    sem = [t["name"] for t in tags if re.fullmatch(r"v?\d+\.\d+\.\d+", t.get("name", ""))]
    if not sem:
        return None
    return sorted(sem, key=lambda t: tuple(int(x) for x in t.lstrip("v").split(".")))[-1]


def ghcr_has_chart(name, version):
    tok = http(f"https://ghcr.io/token?scope=repository:keiailab/charts/{name}:pull", want_json=True)
    token = (tok or {}).get("token", "")
    return head_status(
        f"https://ghcr.io/v2/keiailab/charts/{name}/manifests/{version}",
        {"Authorization": f"Bearer {token}", "Accept": "application/vnd.oci.image.manifest.v1+json"},
    ) == 200


def main():
    catalog_text = http(CATALOG_URL)
    index_text = http(INDEX_URL)
    if not catalog_text or not index_text:
        print("✗ 카탈로그/인덱스를 읽을 수 없음 — 네트워크 또는 URL 확인", file=sys.stderr)
        return 2

    entries = parse_catalog(catalog_text)
    index = parse_index(index_text)
    print(f"발행 일관성 감시 — {len(entries)} chart\n")

    failed, warned = [], []
    for name, ver in entries:
        marks = []

        if ghcr_has_chart(name, ver):
            marks.append("ghcr✓")
        else:
            marks.append("ghcr✗")
            failed.append(f"{name}: ghcr 에 chart {ver} 없음(익명 pull 불가) — helm push 또는 패키지 공개 필요")

        idx_ver, idx_app = index.get(name, (None, None))
        if idx_ver == ver:
            marks.append("index✓")
        else:
            marks.append(f"index✗({idx_ver or '없음'})")
            failed.append(f"{name}: Pages index {idx_ver or '없음'} ≠ catalog {ver} — update-index 누락")

        # 앱 버전은 appVersion 이 진본(chart 버전과 독립적으로 매길 수 있다).
        app = (idx_app or ver).lstrip("v")
        tag = latest_tag(name)
        if tag is None:
            marks.append("tag~")
        elif tag.lstrip("v") == app:
            marks.append("tag✓")
        else:
            marks.append(f"tag✗(GitHub {tag})")
            msg = f"{name}: GitHub 최신 {tag} 인데 공개 chart appVersion {app} — chart 발행 누락"
            (failed if STRICT_TAG else warned).append(msg)

        print(f"  {name} {ver}: " + " ".join(marks))

    print()
    for m in failed:
        print(f"  ✗ {m}")
    for m in warned:
        print(f"  △ {m}")
    if failed:
        print("\n✗ 발행 drift — 해당 repo 에서 `make release VERSION=<x.y.z>` 로 4채널을 맞춘다.")
        return 1
    print("✓ 전 chart 발행 일관성 유지" + (" (경고 있음)" if warned else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
