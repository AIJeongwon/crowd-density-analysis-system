# Raspberry Pi 엣지 센서 클라이언트

CDAS의 현재 센서 노드는 Raspberry Pi에서 Lepton 3.5 열화상 프레임과 SLAMTEC RPLIDAR C1 스캔을 결합하고, 모델이 추론한 인원 수만 백엔드로 전송한다. 원시 픽셀과 LiDAR 포인트는 서버로 보내지 않는다.

## 스레드와 데이터 흐름

Main은 공유 큐와 종료 이벤트를 만들고 다음 5개 작업 스레드를 생성·감시·종료한다.

```text
ThermalSensor ─> thermal queue ─┐
                                ├─> Fusion ─> fused queue ─> ModelAdapter
LidarSensor   ─> lidar queue   ─┘                                 │
                                                                  ▼
                                      single-slot mailbox ─> Communication
```

- `ThermalSensor`: PureThermal USB-UVC 보드의 Lepton 3.5 Y16 프레임을 읽는다.
- `LidarSensor`: C++ 브리지를 실행하여 C1의 완성된 스캔을 읽는다.
- `Fusion`: `fusion.poll_interval_seconds`마다 두 센서 큐를 확인한다. 둘 다 있으면 가장 오래된 항목을 FIFO로 하나씩 결합한다. `flush_every_checks`번째 확인에서는 두 큐를 모두 비우고 결합을 건너뛴다. 기본 예시는 10회다.
- `ModelAdapter`: 결합 데이터를 모델 플러그인에 전달해 `people_count`와 `confidence`를 얻는다.
- `Communication`: 단일 슬롯 mailbox로 결과를 받아 서버에 전송하고 heartbeat를 확인한다. 슬롯이 빌 때까지 생산자가 대기하므로 전송 전 결과를 덮어쓰지 않는다.

복구할 수 없는 센서·모델·통신 오류는 Main에 전달되어 전체 프로세스를 종료한다. `Ctrl+C`도 모든 스레드와 공유 자원을 정상 종료한다.

## 하드웨어 준비

필요한 환경:

- Raspberry Pi의 32비트 또는 64비트 Linux와 Python 3.9 이상
- FLIR Lepton 3.5와 PureThermal 호환 USB-UVC 보드
- SLAMTEC RPLIDAR C1
- `v4l2-ctl`, C++ 빌드 도구, C1 지원 RPLIDAR SDK 2.1.0 이상

```bash
sudo apt update
sudo apt install -y v4l-utils build-essential
```

### Lepton 3.5

160×120 해상도의 `Y16 ` 형식을 제공하는지 확인한다. `Y16` FOURCC에는 마지막 공백 문자가 포함된다.

```bash
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

센서는 Radiometry 및 TLinear를 활성화하고, TLinear 해상도는 0.01 K, AGC는 비활성화해야 한다. Y16 프레임은 Raspberry Pi 내부 큐에서만 사용한다.

### RPLIDAR C1 브리지

공식 SDK를 받은 뒤 Raspberry Pi에서 네이티브 빌드한다. x86 라이브러리를 ARM/aarch64 실행 파일에 링크할 수 없다.

```bash
git clone https://github.com/Slamtec/rplidar_sdk.git
make
```

실행 파일은 `sensor-client/build/rplidar_c1_bridge`에 생성된다. C1은 보통 `/dev/ttyUSB0` 또는 `/dev/serial/by-id/...`로 나타난다. 단독 확인과 JSONL 계약은 [RPLIDAR_C1_BRIDGE.md](RPLIDAR_C1_BRIDGE.md)를 참고한다.

## 환경 설정

프로젝트 루트에서 예시를 클라이언트 디렉터리로 복사한다.

```bash
cp environment.example.json sensor-client/environment.json
```

PowerShell:

```powershell
Copy-Item environment.example.json sensor-client/environment.json
```

| 항목                                       | 설명                                                  |
| ------------------------------------------ | ----------------------------------------------------- |
| `node.*`                                   | 노드와 설치 위치 ID                                   |
| `thermal.*`                                | V4L2 장치, 수집 주기와 제한 시간                      |
| `lidar.*`                                  | C1 브리지·직렬 장치, baud rate, scan mode와 제한 시간 |
| `fusion.poll_interval_seconds`             | Fusion의 큐 확인 주기                                 |
| `fusion.flush_every_checks`                | 두 센서 큐를 비우고 해당 결합을 건너뛰는 회차         |
| `fusion.*_queue_size`                      | 공유 큐 최대 크기                                     |
| `fusion.debug_dir`                         | 디버그 이미지 폴더. 예시는 `/tmp/cdas`                |
| `model.adapter_module`, `model.model_path` | 모델 어댑터와 모델 파일                               |
| `server.*`                                 | 백엔드 주소, 요청·heartbeat 시간과 연속 실패 한도     |

상대 경로는 `environment.json` 위치를 기준으로 해석한다. 실제 장치, 서버 IP, ID, 모델 경로로 수정하며 운영 설정은 커밋하지 않는다.

## 모델 플러그인

`model.adapter_module`은 다음 계약의 `ModelAdapter`를 제공한다.

```python
class ModelAdapter:
    def __init__(self, model_path):
        # ONNX, TFLite, PyTorch 등 모델 초기화
        ...

    def infer(self, sensor_data):
        return {"people_count": 12, "confidence": 0.91}
```

입력은 `fused_at`, `thermal: {captured_at, width, height, pixels}`, `lidar: {captured_at, sequence, points}` 구조다. 각 LiDAR point는 `(angle_deg, distance_mm, quality_raw)`다. 출력은 0 이상의 정수 `people_count`와 0~1 숫자 `confidence`여야 한다.

adapter module 경로가 없거나 파일을 찾지 못하면 일반 모드는 오류로 종료한다. `--debug`에서는 0~50의 임의 인원 수와 신뢰도 0.0을 생성해 통신 스레드로 전달한다. `--verbose`를 함께 사용하면 생성한 값이 서버 전송용으로 큐잉되었음을 ModelAdapter 로그로 출력한다. adapter module은 있지만 모델 파일만 없는 경우에는 기존처럼 추론과 전송을 생략한다.

## 실행

프로젝트 루트에서 실행해도 클라이언트는 `sensor-client/environment.json`을 읽는다.

```bash
python3 sensor-client/sensor_client.py [--debug] [--verbose]
```

받는 옵션은 `--debug`, `--verbose`, 자동 제공되는 `--help`뿐이다.

- `--debug`: Fusion이 결합 데이터를 큐에 넣기 전에 컬러 열화상과 검은 배경·흰 점의 LiDAR PNG를 `fusion.debug_dir`에 저장한다.
- `--verbose`: 정상 센서 수집, Fusion 큐잉, 모델 추론, 정상 heartbeat 로그를 추가한다.
- 기본 로그: `yy-mm-dd hh:mm:ss.ms`, 스레드 이름, 레벨, Main과 통신의 시작·종료, warning/error.

```bash
python3 sensor-client/sensor_client.py --debug --verbose
```

통신은 `GET /health`와 `POST /api/inference-results`를 사용한다. 느린 요청과 통신 실패는 warning이며 연속 실패 한도에 도달하면 error로 전체를 종료한다. 원시 센서용 `/api/sensor-readings`와 서버 `--logging`은 제거되었다.

## 호환 래퍼

기존 센서 클라이언트 진입점은 과거 실행 명령과의 호환을 위한 deprecated wrapper로만 유지한다. 새 배포와 문서에서는 `sensor_client.py`를 사용한다. 호환 래퍼도 새 센서 파이프라인으로 라우팅하며 원시 센서 API를 사용하지 않는다.
