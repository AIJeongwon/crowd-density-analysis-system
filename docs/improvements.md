# 개선 예정 사항

이 문서는 초기 프로토타입 이후 놓치지 말아야 할 개선 항목을 정리한다.

## 1. 저장소 교체

현재 백엔드는 메모리 저장소만 사용한다. 서버를 재시작하면 데이터가 사라지므로, 센서 데이터가 실제로 쌓이기 시작하면 SQLite를 먼저 도입한다.

- 초기 실험: SQLite
- 배포 또는 다중 사용자 단계: PostgreSQL 검토
- 저장 대상: 센서 수집값, 위치별 혼잡도 상태, 장비 메타데이터

## 2. 센서별 payload 검증 강화

현재는 센서별 주요 metric의 타입만 검증한다. 실제 장비 연동 후에는 값의 범위와 단위를 명확히 제한해야 한다.

- `thermal.hotspot_count`: 0 이상의 정수
- `thermal.avg_temp`, `thermal.max_temp`: 섭씨 기준 합리적 범위
- `lidar.object_count`: 0 이상의 정수
- `lidar.avg_distance`, `lidar.min_distance`: 미터 단위 양수
- `valid`: boolean만 허용

## 3. 시간 동기화 전략

서버는 최근 데이터 판단에 `received_at`을 사용한다. 다만 실제 분석에서는 센서 측정 시각인 `timestamp`도 중요하다.

- 라즈베리파이 NTP 설정 확인
- 센서별 clock drift 기록
- `timestamp`와 `received_at` 차이가 큰 경우 경고 표시

## 4. 혼잡도 계산 개선

현재 혼잡도 계산은 규칙 기반 기준선이다. 실제 데이터가 모이면 이 기준선을 유지한 채 더 나은 방식으로 확장한다.

- 센서별 가중치 조정
- 위치별 임계값 분리
- 시간대별 이동 평균 적용
- 사용자 피드백 기반 보정
- AI 모델 기반 추론과 baseline 비교

## 5. 장비 상태 모니터링

센서 데이터와 혼잡도 데이터는 분리해서 생각해야 한다. 운영 단계에서는 장비가 정상적으로 값을 보내고 있는지도 확인해야 한다.

- 장비 heartbeat
- 마지막 수신 시각
- 센서별 `valid=false` 비율
- 네트워크 지연 또는 전송 실패 횟수

## 6. API 구조 정리

현재는 Python 표준 라이브러리 기반의 최소 서버다. 기능이 늘어나면 FastAPI 또는 Spring Boot로 옮기는 것을 검토한다.

- OpenAPI 문서 자동 생성
- request/response schema 명시
- 에러 응답 포맷 통일
- API versioning

## 7. 보안과 운영

실제 외부 네트워크에 배포하기 전에는 센서 노드 인증과 CORS 설정을 정리해야 한다.

- 센서 노드 API key
- HTTPS
- 제한된 CORS origin
- rate limit
- 로그와 민감 정보 분리
