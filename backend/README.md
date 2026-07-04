# 백엔드 프로토타입

Python 표준 라이브러리만 사용한 초기 백엔드 서버입니다. 실제 프레임워크를 도입하기 전, 센서 데이터 수집 API와 혼잡도 계산 흐름을 빠르게 검증하기 위한 용도입니다.

## 실행

```bash
python -m backend.app.server --host 127.0.0.1 --port 8000
```

## 엔드포인트

- `GET /health`
- `POST /api/sensor-readings`
- `GET /api/readings/recent`
- `GET /api/locations/{location_id}/status`
