# CDAS 백엔드

백엔드는 Raspberry Pi 엣지 노드가 추론한 인원 수를 받고, 위치별 면적과 수용 인원으로 밀도·점유율·혼잡도를 계산한다. 원시 열화상 픽셀과 LiDAR 포인트는 수신하지 않는다.

## 설정과 실행

프로젝트 루트에서 예시를 서버 모듈 디렉터리로 복사한다.

```bash
cp environment.example.json backend/app/environment.json
```

백엔드는 실행 위치와 관계없이 `backend/app/environment.json`의 다음 값을 읽는다.

```json
{
  "backend": {"host": "0.0.0.0", "port": 8000},
  "locations": {
    "moran-market-gate-1": {"area_m2": 100.0, "capacity": 50}
  }
}
```

`area_m2`는 양수, `capacity`는 양의 정수다. 엣지의 `node.location_id`와 `locations` key가 같아야 한다.

```bash
python3 -m backend.app.server
```

서버는 추론 결과의 노드, 위치, 인원 수와 신뢰도를 `yy-mm-dd hh:mm:ss.ms` 시간 형식의 기본 로그로 출력한다. 현재 저장소는 메모리 기반이라 재시작하면 결과가 사라진다.

HTTP 접근 정보와 검증된 수신 JSON 전체를 확인하려면 verbose 모드로 실행한다.

```bash
python3 -m backend.app.server --verbose
```

verbose 로그에는 클라이언트 주소, HTTP 요청과 응답 상태, 수신 inference payload가 `DEBUG` 레벨로 출력된다.

## 엔드포인트

- `GET /health`
- `POST /api/inference-results`
- `GET /api/inference-results/recent`
- `GET /api/locations/{location_id}/status`

등록 예:

```bash
curl -X POST http://127.0.0.1:8000/api/inference-results \
  -H 'Content-Type: application/json' \
  -d '{"node_id":"raspberry-pi-001","location_id":"moran-market-gate-1","timestamp":"2026-08-10T09:00:00+00:00","people_count":12,"confidence":0.91}'
```

조회 예:

```bash
curl http://127.0.0.1:8000/api/locations/moran-market-gate-1/status
curl 'http://127.0.0.1:8000/api/inference-results/recent?limit=5&location_id=moran-market-gate-1'
```

최근 결과는 `limit`, `location_id`, `node_id`로 필터링한다.

## 혼잡도

```text
density_per_m2 = people_count / area_m2
occupancy_ratio = people_count / capacity
congestion_score = min(occupancy_ratio × 100, 100)
```

점수가 35 미만이면 `LOW`, 70 미만이면 `MEDIUM`, 나머지는 `HIGH`다. 기본 30초 수신 시간 창의 최신 결과를 쓰며 `window_seconds`로 바꿀 수 있다. 상세 계약은 [데이터 계약](../docs/data-contract.md)을 참고한다.

## 제거된 초기 API

`/api/sensor-readings`, `/api/readings/recent`와 서버 `--logging`은 제거되었다. 디버그 이미지는 엣지 클라이언트가 Raspberry Pi의 `/tmp/cdas`에 저장한다.
