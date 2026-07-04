# 센서 데이터 계약 v1

CDAS의 초기 데이터 파이프라인은 열화상 센서와 LiDAR 센서가 서로 다른 노드에서 독립적으로 데이터를 전송하는 구조를 기준으로 한다.

중앙 서버는 센서 장비의 원천 데이터를 직접 해석하지 않고, 각 센서 클라이언트가 1차 가공한 특징값을 수신한다. 이후 서버는 같은 `location_id`의 최근 데이터를 시간 창 기준으로 묶어 혼잡도를 계산한다.

## 공통 요청 형식

```json
{
  "device_id": "thermal-node-001",
  "location_id": "moran-market-gate-1",
  "sensor_type": "thermal",
  "timestamp": "2026-07-04T01:40:00+09:00",
  "metrics": {}
}
```

| 필드 | 설명 |
| --- | --- |
| `device_id` | 센서 노드 식별자 |
| `location_id` | 설치 위치 식별자 |
| `sensor_type` | `thermal` 또는 `lidar` |
| `timestamp` | 센서 측정 시각 |
| `metrics` | 센서 타입별 특징값 |

서버는 최근 데이터 판단 시 센서 측정 시각인 `timestamp`가 아니라 서버 수신 시각인 `received_at`을 기준으로 사용한다. 실제 장비의 시스템 시간이 틀어질 수 있기 때문이다. `timestamp`는 측정 기록과 디버깅 용도로 보존한다.

## 열화상 센서 예시

```json
{
  "device_id": "thermal-node-001",
  "location_id": "moran-market-gate-1",
  "sensor_type": "thermal",
  "timestamp": "2026-07-04T01:40:00+09:00",
  "metrics": {
    "hotspot_count": 12,
    "avg_temp": 29.8,
    "max_temp": 36.5,
    "valid": true
  }
}
```

`hotspot_count`는 혼잡도 계산에 직접 사용하는 주요 특징값이다. `avg_temp`와 `max_temp`는 계산 핵심값이라기보다 센서 이상치 탐지와 품질 점검을 위한 보조값으로 둔다. `valid`는 boolean 값이어야 하며, 문자열 `"true"` 또는 `"false"`는 허용하지 않는다.

## LiDAR 센서 예시

```json
{
  "device_id": "lidar-node-001",
  "location_id": "moran-market-gate-1",
  "sensor_type": "lidar",
  "timestamp": "2026-07-04T01:40:02+09:00",
  "metrics": {
    "object_count": 15,
    "avg_distance": 2.1,
    "min_distance": 0.8,
    "valid": true
  }
}
```

## 초기 혼잡도 계산 방식

초기 버전은 모델 기반 추론 전에 규칙 기반 기준선을 사용한다.

- 열화상 점수: `hotspot_count`를 0~100 범위로 정규화
- LiDAR 점수: `object_count`를 중심으로 계산하고, 평균 거리가 가까울수록 일부 가산
- 최종 점수: 최근 시간 창 안에 들어온 센서별 점수의 평균

센서 하나만 들어온 경우에도 임시 혼잡도를 계산한다. 두 센서가 모두 들어오면 신뢰도를 더 높게 표시한다.
