# 엣지 추론 결과 데이터 계약 v2

CDAS는 Lepton 3.5와 SLAMTEC RPLIDAR C1 원천 데이터를 Raspberry Pi에서 결합·추론하고, 서버에는 최소 추론 결과만 보낸다. 열화상 픽셀, LiDAR 포인트와 센서별 특징값은 네트워크 payload에 포함하지 않는다.

## 추론 결과 등록

`POST /api/inference-results`

```json
{
  "node_id": "raspberry-pi-001",
  "location_id": "moran-market-gate-1",
  "timestamp": "2026-08-10T09:00:00+00:00",
  "people_count": 12,
  "confidence": 0.91
}
```

| 필드           | 형식                  | 설명                           |
| -------------- | --------------------- | ------------------------------ |
| `node_id`      | 비어 있지 않은 문자열 | 엣지 노드 식별자               |
| `location_id`  | 비어 있지 않은 문자열 | 서버의 위치 설정과 연결되는 ID |
| `timestamp`    | ISO 8601 문자열       | 센서 데이터를 결합한 시각      |
| `people_count` | 0 이상의 정수         | 모델이 추론한 인원 수          |
| `confidence`   | 0~1 숫자              | 모델 추론 신뢰도               |

정상 등록은 HTTP 201이며 서버가 `received_at`을 추가한다. 잘못된 형식은 HTTP 400으로 거부한다.

## 모델 어댑터 내부 계약

네트워크 계약과 별개로 모델 플러그인은 다음 인터페이스를 구현한다.

```python
class ModelAdapter:
    def __init__(self, model_path):
        ...

    def infer(self, sensor_data):
        return {"people_count": 12, "confidence": 0.91}
```

입력은 다음 구조다.

```text
{
  fused_at,
  thermal: {captured_at, width, height, pixels},
  lidar:   {captured_at, sequence, points}
}
```

각 LiDAR point는 `(angle_deg, distance_mm, quality_raw)`다. ONNX, TFLite, PyTorch 등의 로딩과 전처리는 플러그인이 담당한다.

## 위치별 혼잡도 계산

서버 `backend/app/environment.json`에 위치별 `area_m2`와 `capacity`를 둔다.

```json
{
  "locations": {
    "moran-market-gate-1": {
      "area_m2": 100.0,
      "capacity": 50
    }
  }
}
```

최근 시간 창 안의 최신 결과를 사용하며 최근 여부는 노드의 `timestamp`가 아닌 서버 `received_at` 기준이다.

```text
density_per_m2 = people_count / area_m2
occupancy_ratio = people_count / capacity
congestion_score = min(occupancy_ratio × 100, 100)
```

| 점수            | 단계     |
| --------------- | -------- |
| 35 미만         | `LOW`    |
| 35 이상 70 미만 | `MEDIUM` |
| 70 이상         | `HIGH`   |

`GET /api/locations/{location_id}/status`로 조회한다. `window_seconds` query의 기본값은 30초다. 위치 설정이 없으면 `LOCATION_NOT_CONFIGURED`, 최근 결과가 없으면 `NO_DATA`다.

```json
{
  "location_id": "moran-market-gate-1",
  "status": "OK",
  "node_id": "raspberry-pi-001",
  "people_count": 12,
  "area_m2": 100.0,
  "capacity": 50,
  "density_per_m2": 0.12,
  "occupancy_ratio": 0.24,
  "congestion_score": 24.0,
  "congestion_level": "LOW",
  "confidence": 0.91
}
```

## 기타 API

- `GET /health`: heartbeat
- `GET /api/inference-results/recent?limit=20&location_id=...&node_id=...`: 최근 결과
- `GET /api/locations/{location_id}/status?window_seconds=30`: 위치 혼잡도

클라이언트는 heartbeat와 추론 결과 전송에 하나의 HTTP/1.1 연결을 재사용한다.
