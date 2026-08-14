# 인구 밀집 구역 혼잡도 분석 시스템 (CDAS)

CDAS(Crowd Density Analysis System)는 열화상 카메라와 LiDAR 센서를 활용해 인구 밀집 구역의 혼잡도를 추정하고, 이를 지도 기반 웹 대시보드에서 시각화하는 IoT/AI 프로젝트입니다.

전통시장, 팝업스토어, 행사장처럼 방문 전 혼잡도를 파악하기 어려운 공간을 대상으로 센서 데이터를 수집하고, 사용자가 현장 상황을 더 쉽게 판단할 수 있도록 돕는 것을 목표로 합니다.

## 개요

이 프로젝트는 센서 노드, 데이터 수집 서버, 혼잡도 분석 로직, 웹 대시보드를 하나의 흐름으로 연결하는 엔드투엔드 시스템을 지향합니다.

초기에는 실제 센서 연동 전 가상 센서 클라이언트를 통해 데이터 수집 흐름을 먼저 검증하고, 이후 라즈베리파이 기반 센서 모듈과 AI 추론 로직을 단계적으로 통합합니다.

## 시스템 구조

원시 센서 데이터는 Raspberry Pi 안에서만 처리한다. 서버에는 인원 수와 신뢰도만 보내며, 서버가 장소의 면적과 수용 인원에 따라 밀도와 혼잡도를 계산한다.

```text
Lepton 3.5 ─> ThermalSensor queue ─┐
                                   ├─> Fusion ─> ModelAdapter
RPLIDAR C1 ─> LidarSensor queue ───┘                   │
                                                       ▼
                                            people_count/confidence
                                                       │
                                                       ▼
                       backend ─> density / occupancy / congestion
```

Raspberry Pi 프로세스는 Main과 5개 작업 스레드로 구성된다.

- ThermalSensor: Lepton 3.5 Y16 프레임
- LidarSensor: SLAMTEC C1 스캔
- Fusion: 두 FIFO 큐를 설정 주기로 결합
- ModelAdapter: 플러그인 모델로 인원 수 추론
- Communication: 추론 결과와 heartbeat를 하나의 HTTP/1.1 연결로 전송
- Main: 공유 자원, 생성·감시·정상 종료

## 주요 기능

현재 구현된 기능과 앞으로 구현할 기능을 함께 정리합니다.

- PureThermal USB-UVC 기반 Lepton 3.5 수집
- 공식 SLAMTEC SDK 기반 RPLIDAR C1 C++ 브리지
- 두 센서 FIFO 중합과 주기적 오래된 큐 정리
- 프레임워크 독립 Python 모델 어댑터
- 손실 없는 단일 슬롯 결과 전달
- HTTP/1.1 연결 재사용, 끊김 시 1회 재연결, 지연 warning과 연속 실패 처리
- 디버그용 컬러 열화상 및 흑백 LiDAR 이미지
- 위치별 밀도, 점유율과 혼잡도 API

* 수집된 센서 데이터 기반 혼잡도 추정
* 혼잡도 데이터 수집 및 조회를 위한 REST API
* 장소별 혼잡도 시각화를 위한 웹 대시보드
* 추정 신뢰도 개선을 위한 사용자 피드백 데이터 수집

## 빠른 시작

클라이언트와 서버 설정 파일을 각각 생성한다.

```bash
cp environment.example.json sensor-client/environment.json
cp environment.example.json backend/app/environment.json
```

장치 경로, 서버 주소, 모델과 위치 설정을 수정한다. C1 브리지는 Raspberry Pi에서 네이티브 빌드한다.

```bash
git clone https://github.com/Slamtec/rplidar_sdk.git
make
```

서버:

```bash
python3 -m backend.app.server
python3 -m backend.app.server --debug
```

서버는 실행 위치와 관계없이 `backend/app/environment.json`을 읽는다. `--debug/-d`를 사용하면 HTTP 접근 정보와 검증된 수신 JSON payload를 `DEBUG` 로그로 출력한다.

Raspberry Pi 클라이언트:

```bash
python3 sensor-client/sensor_client.py
python3 sensor-client/sensor_client.py --debug --verbose
```

클라이언트 옵션은 `--debug`, `--verbose`, `--help`뿐이다. IP, 장치, 주기와 모델 경로는 `sensor-client/environment.json`에서 읽는다. `--debug`에서 adapter module 경로가 없거나 파일을 찾지 못하면 0~50의 임의 인원 수와 신뢰도 0.0을 서버로 전송한다.

```bash
curl http://127.0.0.1:8000/api/locations/moran-market-gate-1/status
```

## 데이터 규칙

- 원시 열화상과 LiDAR 데이터는 서버로 전송하지 않고 엣지에서 처리한다.
- payload는 `node_id`, `location_id`, `timestamp`, `people_count`, `confidence`다.
- 서버는 `area_m2`와 `capacity`로 혼잡도를 계산한다.
- 디버그 이미지는 엣지의 `/sensor-client/debug`에 저장한다.

## 문서

- [센서 클라이언트](sensor-client/README.md)
- [데이터 계약](docs/data-contract.md)
- [개발 가이드](docs/development.md)
- [개선 예정 사항](docs/improvements.md)
- [백엔드](backend/README.md)

## 문서화 계획

프로젝트가 진행되면서 다음 문서를 순차적으로 정리합니다.

- `docs/data-contract.md`: 센서 데이터 요청 형식과 초기 혼잡도 계산 방식
- `docs/development.md`: 커밋, 브랜치, 테스트, 코드 스타일 기준
- `docs/improvements.md`: 향후 개선 항목
- `docs/architecture.md`: 전체 시스템 구조와 데이터 흐름
- `docs/hardware.md`: 센서 구성, 라즈베리파이 설정, 설치 기록
- `docs/api.md`: 데이터 수집 및 조회 API 명세
- `docs/model.md`: 혼잡도 산출 기준과 모델 실험 기록
- `docs/deployment.md`: 실행 환경 및 배포 과정
- `docs/troubleshooting.md`: 개발 중 발생한 문제와 해결 과정

## 개발 계획

### 1. 가상 데이터 파이프라인

실제 센서가 준비되기 전까지 가상의 센서 데이터를 생성하고, 서버 수집 API와 데이터 저장 흐름을 먼저 구현합니다.

### 2. 하드웨어 연동

라즈베리파이에 열화상 카메라와 LiDAR 센서를 연결하고, 센서별 원천 데이터를 수집하는 클라이언트를 구현합니다.

### 3. 혼잡도 추정

수집된 데이터를 기반으로 혼잡도 산출 기준을 정의하고, 규칙 기반 추정부터 AI 모델 기반 추론까지 단계적으로 확장합니다.

### 4. 웹 대시보드

장소별 혼잡 상태를 조회하고, 지도 또는 대시보드 형태로 실시간 혼잡도를 시각화합니다.

### 5. 배포 및 문서화

Docker 기반 실행 환경과 AWS 배포 환경을 구성하고, API 명세와 트러블슈팅 과정을 문서화합니다.

## 기술 스택

| 영역            | 기술                                                  |
| --------------- | ----------------------------------------------------- |
| 하드웨어        | Raspberry Pi, Thermal Camera, LiDAR                   |
| 센서 클라이언트 | Python                                                |
| 백엔드          | Python 표준 라이브러리, FastAPI 또는 Spring Boot 검토 |
| AI / 데이터     | Python, OpenCV, NumPy, PyTorch                        |
| 프론트엔드      | React, TypeScript                                     |
| 데이터 저장     | 메모리 저장소, SQLite 또는 PostgreSQL                 |
| 인프라          | Docker, AWS                                           |
| 문서화          | Markdown, Swagger/OpenAPI                             |

## 라이선스

이 프로젝트는 현재 개발 초기 단계이며, 라이선스 정보는 추후 정리할 예정입니다.
