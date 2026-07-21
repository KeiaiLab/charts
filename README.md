# KeiaiLab Helm chart catalog

이 저장소는 KeiaiLab 공개 Helm chart를 Artifact Hub에서 하나의 repository로 묶기 위한
HTTP Helm catalog입니다.

실제 chart artifact는 GHCR OCI registry에 유지합니다.

```console
helm repo add keiailab https://keiailab.github.io/charts
helm search repo keiailab --versions
helm install my-release oci://ghcr.io/keiailab/charts/<chart>
```

## 구조

- `catalog.yaml`: 중앙 catalog에 노출할 chart 이름과 version의 SSOT입니다.
- `index.yaml`: `catalog.yaml` 기준으로 생성되는 Helm repository index입니다.
- `hack/update-index.sh`: GHCR OCI chart를 pull하고 `index.yaml`을 Bitnami 방식으로 갱신합니다.
- `hack/verify-index.sh`: 생성된 index가 단일 HTTP repo + OCI chart URL 구조인지 검증합니다.

## 갱신

```console
bash hack/update-index.sh
bash hack/verify-index.sh
```

`index.yaml`의 각 chart entry는 다음 형태의 OCI URL을 가리킵니다.

```yaml
urls:
  - oci://ghcr.io/keiailab/charts/<chart>:<version>
```

이 구조는 Bitnami chart catalog와 같은 방향입니다. Artifact Hub에는 이 repository의
GitHub Pages URL을 등록하고, chart payload는 GHCR OCI에서 가져옵니다.

## 발행 일관성 (drift 방지)

OSS chart 는 릴리스마다 4채널을 함께 맞춰야 한다 — **GitHub 태그 / 컨테이너 이미지 /
ghcr OCI chart / 이 카탈로그**. 코드·이미지만 앞서가고 공개 chart 가 조용히 뒤처지는
사고를 막기 위해 두 층을 둔다.

| 층 | 도구 | 역할 |
|---|---|---|
| 예방 | 각 OSS repo `make release VERSION=x.y.z` | 게이트→태그→이미지→chart→카탈로그를 한 번에 (누락 구조적 불가) |
| 검출 | `python3 hack/verify_consistency.py` | 전 chart 4채널 대조, drift 시 exit 1 |

검출은 클러스터 CronJob(`platform-system-charts-consistency`, daily)이 자동 실행한다 —
사람이 잊어도 하루 안에 드러난다. 표준 라이브러리만 쓰고 원격 HTTP 만 읽으므로 로컬과
CronJob 에서 동일하게 동작한다.
