#!/usr/bin/env python3
"""카탈로그에 등재된 모든 차트의 artifacthub.io/images 를 검증한다.

ArtifactHub 는 이 어노테이션에 적힌 이미지를 그대로 스캔한다. 어노테이션은 정적
텍스트라 릴리스 bump 를 따라가지 않아 조용히 썩는다 — 2026-07-29 mongodb-operator
사고(내부 전용 harbor.keiailab.dev 를 광고 → 스캐너 DNS 실패로 매 스캔 에러 메일)와
nodevitals(7 버전 뒤처진 0.1.0 광고 → 무의미한 보안 리포트)가 같은 뿌리다.

각 차트 repo 마다 게이트를 복제하는 대신, 카탈로그가 ArtifactHub 로 나가는 유일한
길목인 여기서 한 번에 막는다. 3 종을 검사한다:

  A. 내부 전용 레지스트리 광고 (*.keiailab.dev)  → 외부 스캐너 도달 불가
  B. 익명 pull 불가 이미지 (없는 태그·비공개)     → 스캔 실패
  C. 차트 자기 이미지가 appVersion 과 불일치      → 엉뚱한 버전을 스캔

stdin 으로 `<name>\t<version>\t<Chart.yaml 전문>` 레코드를 NUL 구분으로 받는다.
"""
import json
import os
import sys
import urllib.error
import urllib.request

import yaml

ACCEPT = ",".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
INTERNAL_SUFFIXES = (".keiailab.dev", ".keiailab.ops")


def http_status(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # DNS/TLS 실패 = 스캐너가 겪는 바로 그 상황
        return None, str(e).encode()


def split_ref(image):
    ref = image.split("@")[0]
    first = ref.split("/")[0]
    if "/" in ref and ("." in first or ":" in first or first == "localhost"):
        host, rest = ref.split("/", 1)
    else:
        host, rest = "docker.io", ref
    repo, _, tag = rest.partition(":")
    return host, repo, (tag or "latest")


def pullable(host, repo, tag):
    if host == "ghcr.io":
        st, body = http_status(f"https://ghcr.io/token?scope=repository:{repo}:pull&service=ghcr.io")
        if st != 200:
            return False, f"anonymous token denied ({st})"
        token = json.loads(body)["token"]
        st, _ = http_status(
            f"https://ghcr.io/v2/{repo}/manifests/{tag}",
            {"Authorization": f"Bearer {token}", "Accept": ACCEPT},
        )
        return st == 200, f"ghcr manifest {st}"
    if host in ("docker.io", "registry-1.docker.io"):
        path = repo if "/" in repo else f"library/{repo}"
        st, _ = http_status(f"https://hub.docker.com/v2/repositories/{path}/tags/{tag}")
        return st == 200, f"docker hub tag {st}"
    st, body = http_status(f"https://{host}/v2/")
    if st is None:
        return False, f"registry unreachable: {body.decode()[:80]}"
    return True, f"registry reachable (v2 {st}); tag unverified"


failures = []
checked = 0
for record in sys.stdin.read().split("\0"):
    if not record.strip():
        continue
    name, version, chart_yaml = record.split("\t", 2)
    doc = yaml.safe_load(chart_yaml) or {}
    raw = (doc.get("annotations") or {}).get("artifacthub.io/images")
    if not raw:
        # 어노테이션이 없으면 ArtifactHub 가 렌더된 매니페스트에서 직접 추출한다 —
        # 정의상 stale 될 수 없으므로 검사 대상이 아니다.
        print(f"  {name}:{version} — artifacthub.io/images 없음 (AH 자동 추출) — skip")
        continue
    app = str(doc.get("appVersion", "")).strip()
    for entry in yaml.safe_load(raw) or []:
        image = (entry.get("image") or "").strip()
        if not image:
            continue
        checked += 1
        host, repo, tag = split_ref(image)
        if any(host.endswith(s) for s in INTERNAL_SUFFIXES):
            failures.append(f"{name}:{version} [A] 내부 전용 레지스트리 광고: {image}")
            continue
        ok, detail = pullable(host, repo, tag)
        if not ok:
            failures.append(f"{name}:{version} [B] 익명 pull 불가: {image} ({detail})")
            continue
        # 차트 자기 이미지만 appVersion 정합 검사 — 런타임에 뜨는 부속 이미지는 대상 아님
        if app and repo.rsplit("/", 1)[-1] == name and tag.lstrip("v") != app.lstrip("v"):
            failures.append(f"{name}:{version} [C] appVersion({app}) 불일치: {image}")
            continue
        print(f"  {name}:{version} — OK {image} ({detail})")

if failures:
    print("\nartifacthub.io/images 검증 실패:", file=sys.stderr)
    for f in failures:
        print(f"  - {f}", file=sys.stderr)
    print(
        "\n수리: 해당 차트 repo 의 Chart.yaml 어노테이션을 공개 레지스트리 + appVersion "
        "정합 태그로 고치고 chart version 을 bump 한 뒤(미bump 시 publish skip), "
        "이 catalog.yaml 핀을 그 버전으로 올린다.",
        file=sys.stderr,
    )
    sys.exit(1)
print(f"images OK: {checked} 개 이미지가 공개 도달 가능하고 appVersion 과 정합")
