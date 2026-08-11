# 인구 밀집 구역 혼잡도 분석 시스템 (CDAS)

CDAS는 열화상 카메라와 LiDAR 센서로 인원 수를 엣지에서 추론하고 장소별 혼잡도를 제공하는 IoT/AI 프로젝트다. 전통시장, 팝업스토어, 행사장처럼 방문 전 혼잡도를 파악하기 어려운 공간을 대상으로 한다.

## 현재 구조

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
- Communication: 결과 전송과 heartbeat
- Main: 공유 자원, 생성·감시·정상 종료

## 주요 기능

- PureThermal USB-UVC 기반 Lepton 3.5 수집
- 공식 SLAMTEC SDK 기반 RPLIDAR C1 C++ 브리지
- 두 센서 FIFO 중합과 주기적 오래된 큐 정리
- 프레임워크 독립 Python 모델 어댑터
- 손실 없는 단일 슬롯 결과 전달
- heartbeat, 지연 warning과 연속 실패 처리
- 디버그용 컬러 열화상 및 흑백 LiDAR 이미지
- 위치별 밀도, 점유율과 혼잡도 API

## 빠른 시작

클라이언트와 서버 설정 파일을 각각 생성한다.

```bash
cp environment.example.json sensor-client/environment.json
cp environment.example.json backend/app/environment.json
```

장치 경로, 서버 주소, 모델과 위치 설정을 수정한다. C1 브리지는 Raspberry Pi에서 네이티브 빌드한다.

```bash
git clone https://github.com/Slamtec/rplidar_sdk.git
make -C sensor-client -f Makefile.rplidar-c1 \
  RPLIDAR_SDK_DIR="$PWD/rplidar_sdk"
```

서버:

```bash
python3 -m backend.app.server
```

서버는 실행 위치와 관계없이 `backend/app/environment.json`을 읽는다.

Raspberry Pi 클라이언트:

```bash
python3 sensor-client/sensor_client.py
python3 sensor-client/sensor_client.py --debug --verbose
```

클라이언트 옵션은 `--debug`, `--verbose`, `--help`뿐이다. IP, 장치, 주기와 모델 경로는 `sensor-client/environment.json`에서 읽는다. `--debug`는 모델이 없어도 센서 수집·중합·heartbeat를 실행하고 결과 전송은 생략한다.

```bash
curl http://127.0.0.1:8000/api/locations/moran-market-gate-1/status
```

## 데이터 원칙

- 원시 열화상과 LiDAR 데이터는 서버로 전송하지 않는다.
- payload는 `node_id`, `location_id`, `timestamp`, `people_count`, `confidence`다.
- 서버는 `area_m2`와 `capacity`로 혼잡도를 계산한다.
- 디버그 이미지는 엣지의 `/tmp/cdas`에 저장한다.
- 초기 `/api/sensor-readings`와 서버 `--logging`은 제거되었다.

## 문서

- [센서 클라이언트](sensor-client/README.md)
- [데이터 계약](docs/data-contract.md)
- [개발 가이드](docs/development.md)
- [개선 예정 사항](docs/improvements.md)
- [백엔드](backend/README.md)

## 라이선스

현재 개발 초기 단계이며 라이선스는 추후 정리한다.
