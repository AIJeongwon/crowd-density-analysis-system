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

- `ThermalSensor`: PureThermal USB-UVC 보드의 Lepton 3.5 Y16 프레임을 읽고, 시계 방향으로 90도 회전한 120×160 프레임을 센서 큐에 저장한다.
- `LidarSensor`: C++ 브리지를 실행하여 C1의 완성된 스캔을 읽는다.
- `Fusion`: 기본 사진 모드에서는 `fusion.poll_interval_seconds`마다 두 센서 큐의 FIFO 항목을 하나씩 결합하며, `flush_every_checks`번째 확인에 오래된 큐를 비운다.
  영상 모드에서는 `model.video_inference_fps` 주기로 최신 열화상 프레임과 최신 LiDAR 스캔만 결합하여 지연 누적을 막는다.
- `ModelAdapter`: 결합 데이터를 모델 플러그인에 전달해 `people_count`와 `confidence`를 얻는다.
- `Communication`: 단일 슬롯 mailbox의 결과와 heartbeat를 하나의 HTTP/1.1 연결로 순차 전송한다. 슬롯이 빌 때까지 생산자가 대기하므로 전송 전 결과를 덮어쓰지 않는다.

센서·모델·통신 등 작업 스레드의 오류는 Main에 전달됩니다. Main은 계속 실행되며,
`runtime.worker_restart_delay_seconds`(기본 5초) 후 오류가 난 스레드만 새 객체로 생성합니다.
다시 실패해도 횟수 제한 없이 같은 간격으로 재시도합니다. 오류 메시지와 재시도 대기는
ERROR, 스레드를 다시 시작한 사실은 INFO로 출력하며 `--verbose` 없이도 확인할 수 있습니다.

기존 스레드가 센서 프로세스·모델·연결 정리를 마치고 완전히 종료한 뒤에만 새 스레드를
시작하므로 같은 장치를 중복으로 열지 않습니다. 다른 스레드와 크기가 제한된 공유 큐는
유지되며, 데이터가 없거나 큐가 가득 차면 해당 단계가 대기합니다. 스레드 생성·시작 실패와
종료 요청 없는 예기치 않은 스레드 종료도 재시도합니다. 재시작 직전 처리 중이던 프레임이나
전송 결과의 재처리를 보장하지는 않습니다.

`Ctrl+C` 또는 영상 GUI의 정상 종료 요청 시에는 재시도를 취소하고 모든 스레드와 공유
자원을 종료합니다. 대기 중에도 `Ctrl+C`로 종료할 수 있습니다. 시작 시 설정 JSON을 읽거나
검증하지 못한 오류는 실행 전에 종료되며, 실행 중 설정 파일을 자동으로 다시 읽지는 않습니다.
부팅 시 자동 실행은 아래의 `install_service.sh`로 서비스에 등록할 수 있습니다.

구현 위치는 `sensor-client/sensor_client.py`의 `SensorClientApplication.run()`,
`_start_worker()`, `_schedule_restart()`, `_supervise_workers()`, `shutdown()`과
`sensor-client/shared_runtime.py`의 `ManagedWorker.run()`입니다.

## 하드웨어 준비

필요한 환경:

- Python 3.9 이상을 제공하는 Raspberry Pi Linux. LLVIP ONNX 추론은 `aarch64` 64비트 Raspberry Pi OS를 기준으로 한다.
- FLIR Lepton 3.5와 PureThermal 호환 USB-UVC 보드
- SLAMTEC RPLIDAR C1
- `v4l2-ctl`, C++ 빌드 도구, C1 지원 RPLIDAR SDK 2.1.0 이상

```bash
sudo apt update
sudo apt install -y \
  v4l-utils build-essential curl \
  python3-venv python3-numpy python3-opencv
```

### Python 가상 환경

Raspberry Pi OS Bookworm에서는 ONNX Runtime을 시스템 Python에 직접 설치하지 않고 프로젝트 가상 환경에 설치한다. OpenCV와 NumPy는 위의 운영체제 패키지를 재사용하도록 `--system-site-packages`를 지정한다.

```bash
uname -m
python3 --version
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install onnxruntime
```

LLVIP 모델을 사용하려면 `uname -m` 결과가 `aarch64`인지 확인한다. 설치 후 다음 명령이 버전을 모두 출력해야 한다.

```bash
.venv/bin/python -c "import cv2, numpy, onnxruntime; print(cv2.__version__, numpy.__version__, onnxruntime.__version__)"
```

`sudo pip install`이나 `--break-system-packages`는 사용하지 않는다. 모델을 사용하지 않는 디버그 임의값 모드만 실행한다면 ONNX Runtime은 필요하지 않다.

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

### Workers + D1 연결

Workers 구성에서는 `server.base_url`을 지도 웹과 동일한 HTTPS 주소로 설정합니다.
환경 설정의 `server.api_token`에 Worker의 `SENSOR_API_TOKEN`과
동일한 값을 지정하면 `CommunicationWorker._send_result_until_complete()`가
측정값 전송에만 Bearer 인증 헤더를 추가합니다. `Bearer` 접두사는 붙이지 않습니다.
토큰을 설정한 상태에서 localhost 외의 평문 HTTP 주소를 사용하면 통신 스레드 시작 시
오류를 기록하고 설정된 간격으로 재시도합니다. 연결하려면 올바른 HTTPS 주소를 설정해야 합니다.
기존 Python 서버를 사용할 때는 `server.api_token`을 `null`로 두어 기존 동작을 유지할 수 있습니다.
자세한 초기화 절차는 [Workers 웹 실행 안내](../frontend/README.md)를 참고하세요.

#### 토큰 설정

`sensor-client/environment.json`은 Git에서 제외되므로 장치별 실제 토큰을 이 파일에만
저장합니다. 예시 파일을 복사한 뒤 `server` 항목을 다음과 같이 수정합니다.

```json
"server": {
  "base_url": "https://sensor-api.example.com",
  "api_token": "YOUR_SENSOR_API_TOKEN"
}
```

- 실제 `environment.json`은 커밋하지 않습니다. `environment.example.json`에는 항상
  `"api_token": null`만 유지합니다.
- 토큰에는 공백이나 줄바꿈을 넣을 수 없으며 출력 가능한 ASCII 문자만 사용합니다.
- 센서 모듈 3대에 같은 값을 설정하고, 각 모듈의 `node_id`는 서로 다르게 지정합니다.

구현 위치는 `environment_config.py`의 `load_environment()`와
`_optional_api_token()`, `communication_thread.py`의
`CommunicationWorker.__init__()` 및 `_send_result_until_complete()`입니다.

프로젝트 루트에서 예시를 클라이언트 디렉터리로 복사한다.

```bash
cp sensor-client/environment.example.json sensor-client/environment.json
```

PowerShell:

```powershell
Copy-Item sensor-client/environment.example.json sensor-client/environment.json
```

| 항목                                       | 설명                                                  |
| ------------------------------------------ | ----------------------------------------------------- |
| `node.*`                                   | 노드와 설치 위치 ID                                   |
| `runtime.worker_restart_delay_seconds`     | 자식 스레드 오류 후 재시작 대기 시간(초). 양수, 기본 `5.0`, 재시도 횟수 제한 없음 |
| `thermal.*`                                | V4L2 장치, 수집 주기와 제한 시간                      |
| `lidar.*`                                  | C1 브리지·직렬 장치, baud rate, scan mode와 제한 시간 |
| `fusion.poll_interval_seconds`             | Fusion의 큐 확인 주기                                 |
| `fusion.flush_every_checks`                | 두 센서 큐를 비우고 해당 결합을 건너뛰는 회차         |
| `fusion.*_queue_size`                      | 공유 큐 최대 크기                                     |
| `fusion.debug_dir`                         | 디버그 이미지 폴더. 예시는 `/tmp/cdas`                |
| `model.adapter_module`, `model.model_path` | 모델 어댑터와 모델 파일                               |
| `model.video_inference_fps`                | 영상 모드에서 초당 수행할 추론 횟수. 기본값은 `4.0`   |
| `server.api_token`                         | Workers 전송 인증 토큰. 미사용 시 `null`              |
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

입력은 `inference_mode`(`image` 또는 `video`), `fused_at`, `thermal: {captured_at, width, height, pixels}`, `lidar: {captured_at, sequence, points}`, `debug: {enabled, output_dir, lidar_image_size, lidar_max_distance_m}` 구조다. 각 LiDAR point는 `(angle_deg, distance_mm, quality_raw)`다. 출력은 0 이상의 정수 `people_count`와 0~1 숫자 `confidence`여야 한다.

### LLVIP ONNX 어댑터

`llvip_model_adapter.py`는 LLVIP 적외선 영상으로 학습된 YOLOv5l ONNX 모델을 CPU에서 실행한다. 현재 기준선 구현은 중합 데이터 중 열화상만 이용해 사람을 검출하고, NMS 이후 검출 수를 `people_count`, 검출 신뢰도의 평균을 `confidence`로 반환한다. LiDAR 결합 추론은 후속 모델에서 추가할 수 있다.

Release 모델은 저장소에 커밋하지 않고 프로젝트 루트의 `models/`에 내려받는다.

```bash
mkdir -p models
curl -L \
  https://github.com/AIJeongwon/crowd-density-analysis-system/releases/download/llvip-yolov5l-160-v1/llvip-yolov5l-160.onnx \
  -o models/llvip-yolov5l-160.onnx
echo "f553ac510ee4cfe50adc618c86403c4f8a0dfb07e7032b709c44369473285d2a  models/llvip-yolov5l-160.onnx" | sha256sum --check
```

`sensor-client/environment.json`의 모델 설정은 다음과 같이 지정한다. 상대 경로는 해당 JSON 파일의 위치를 기준으로 한다.

```json
"model": {
  "adapter_module": "llvip_model_adapter.py",
  "model_path": "../models/llvip-yolov5l-160.onnx",
  "video_inference_fps": 4.0
}
```

열화상 프레임은 `ThermalSensorWorker.capture_once()`에서 이미 시계 방향 90도로 회전된다. `llvip_model_adapter.py`의 `thermal_frame_to_image()`는 게시된 120×160 방향을 그대로 사용하므로 모델 입력에서 다시 회전하지 않는다.

기본 사진 모드의 `--debug`에서는 `ModelAdapter.infer()`가 NMS 이후 bounding box를 원본 열화상 좌표로 복원하고 컬러 열화상 위에 사람 영역과 신뢰도를 표시하여 `fusion.debug_dir`에 `*_inference.png`로 저장한다. 사람이 검출되지 않은 프레임도 박스 없는 추론 이미지로 저장한다.

`--video --debug`에서는 디버그 이미지나 영상 파일을 저장하지 않는다. 대신 하나의 GUI 창에 현재 컬러 열화상·사람 bounding box·신뢰도·인원 수를 왼쪽에, 검은 배경의 최신 LiDAR 점군과 scan sequence를 오른쪽에 계속 갱신한다. LiDAR는 이 화면에 표시되지만 현재 LLVIP 추론 연산에는 사용되지 않는다.

공개 LLVIP 모델은 검출 가능성을 확인하기 위한 기준선이다. Lepton 3.5의 160×120 영상은 LLVIP 학습 영상보다 정보량이 적으므로 실제 설치 거리·각도·가림 조건에서 오탐과 미탐을 별도로 측정해야 한다. LLVIP 데이터와 공개 가중치의 사용 조건도 배포 전에 확인한다.

adapter module 경로가 없거나 파일을 찾지 못하면 일반 모드의 ModelAdapter 스레드는 오류를 알리고 종료하며, Main이 설정된 간격으로 다시 시작한다. `--debug`에서는 0~50의 임의 인원 수와 신뢰도 0.0을 생성해 통신 스레드로 전달한다. `--verbose`를 함께 사용하면 생성한 값이 서버 전송용으로 큐잉되었음을 ModelAdapter 로그로 출력한다. adapter module은 있지만 모델 파일만 없는 경우에는 기존처럼 추론과 전송을 생략한다.

## 실행

프로젝트 루트에서 실행해도 클라이언트는 `sensor-client/environment.json`을 읽는다. 다음 두 방법 중 하나를 사용한다.

가상 환경을 활성화해서 실행:

```bash
source .venv/bin/activate
python sensor-client/sensor_client.py [--video] [--debug] [--verbose]
deactivate
```

가상 환경을 활성화하지 않고 인터프리터를 직접 지정해서 실행:

```bash
.venv/bin/python sensor-client/sensor_client.py [--video] [--debug] [--verbose]
```

systemd에서는 셸 활성화 명령을 사용하지 않고 서비스 파일 전역 `[Service]`의 `ExecStart`에 프로젝트의 절대 경로인 `/absolute/path/to/cdas/.venv/bin/python`과 `sensor-client/sensor_client.py`를 지정한다.

받는 옵션은 `--video/-v`, `--debug`, `--verbose`, 자동 제공되는 `--help`뿐이다.

- `--video`, `-v`: `v4l2-ctl`의 연속 Y16 스트림으로 최신 프레임을 추론한다. 지정하지 않으면 기존 사진 모드다.
- `--debug`: 사진 모드에서는 Fusion·LiDAR·추론 PNG를 저장하고, 영상 모드에서는 파일 저장 없이 열화상 추론과 LiDAR 점군을 좌우 GUI로 표시한다.
- `--verbose`: 정상 센서 수집, Fusion 큐잉, 모델 추론, 정상 heartbeat 로그를 추가한다.
- 기본 로그: `yy-mm-dd hh:mm:ss.ms`, 스레드 이름, 레벨, Main과 통신의 시작·종료, warning/error.

```bash
.venv/bin/python sensor-client/sensor_client.py --debug --verbose
.venv/bin/python sensor-client/sensor_client.py --video --debug --verbose
```

영상 디버그 GUI는 X11 또는 Wayland 화면이 필요하다. 창에서 `q`, `Q`, `Esc`를 누르거나 창을 닫으면 클라이언트 전체가 정상 종료된다. SSH로 실행할 때는 X11 forwarding을 활성화해야 한다.

통신은 `GET /health`와 `POST /api/inference-results`에 연결 하나를 재사용한다. 연결 오류가 나면 기존 연결을 닫고 새 연결로 한 번 즉시 재시도하며, 두 시도가 모두 실패한 논리 요청만 연속 실패 1회로 계산한다. 느린 요청과 통신 실패는 warning이며 연속 실패 한도에 도달하면 통신 스레드가 error를 알리고 종료한다. Main이 설정된 간격으로 통신 스레드를 다시 시작한다. 원시 센서용 `/api/sensor-readings`와 서버 `--logging`은 제거되었다.

### Raspberry Pi 부팅 시 자동 실행

먼저 가상환경과 의존성, RPLIDAR 브리지, 실제 모델 파일 및
`sensor-client/environment.json`을 준비하세요. 일반 모드이므로 모델 경로를 올바르게
설정해야 실제 추론이 수행됩니다. 수동으로 실행 중인 클라이언트가 있다면 먼저 종료하세요.

Raspberry Pi의 프로젝트 루트에서 다음 명령을 한 번 실행합니다.

```bash
sudo bash sensor-client/install_service.sh
```

`cdas-sensor-client.service`를 설치하고 즉시 시작하며, 이후 부팅 때마다 자동으로 실행합니다.
실행 계정은 `sudo`를 호출한 일반 사용자입니다. root 셸에서 설치하거나 다른 계정을 쓰려면
`sudo bash sensor-client/install_service.sh --user 사용자이름`으로 지정하세요.
서비스에만 `video`, `dialout` 보조 그룹 권한을 적용합니다.

서비스는 현재 프로젝트의 절대 경로를 사용하여 아래와 같이 실행합니다.
가상환경 활성화는 필요하지 않으며, `--debug`, `--video`, `--verbose`는 모두 사용하지 않습니다.
따라서 기본 사진 모드로 동작하며 GUI나 디버그 이미지 저장은 활성화하지 않습니다.

```bash
.venv/bin/python sensor-client/sensor_client.py
```

자식 스레드 오류는 Main이 복구합니다. systemd의 프로세스 자동 재시작은 사용하지 않습니다
(`Restart=no`). 프로세스 전체가 종료되면 다음 부팅 또는 수동 시작까지 중지된 상태로
유지됩니다. 중지 시 Main에 SIGINT를 보내 정리를 요청하고, 90초 내 종료하지 못하면 남은
프로세스도 정리합니다. 네트워크 준비 이후 실행하도록 설정하지만 센서·서버 연결이 늦게
준비되는 경우에는 기존 재시도 기능이 처리합니다.

```bash
# 상태와 실시간 로그
sudo systemctl status cdas-sensor-client.service
sudo journalctl -u cdas-sensor-client.service -f

# 설정 변경 후 재시작 / 일시 중지
sudo systemctl restart cdas-sensor-client.service
sudo systemctl stop cdas-sensor-client.service

# 현재 실행을 중지하고 부팅 시 자동 실행도 해제
sudo systemctl disable --now cdas-sensor-client.service

# 자동 실행을 다시 활성화하고 즉시 실행
sudo systemctl enable --now cdas-sensor-client.service
```

설치 전에 서비스 내용만 보려면 `bash sensor-client/install_service.sh --dry-run`을
사용하세요. 프로젝트를 옮겼다면 새 위치에서 설치 스크립트를 다시 실행해야 합니다.
스크립트는 자신이 생성한 서비스만 갱신하며 기존의 수동 관리 서비스는 덮어쓰지 않습니다.
이미 서비스를 설치했다면 스크립트를 다시 실행하여 변경된 설정을 적용하세요.
이때 설정 적용을 위해 서비스가 한 번 재시작되며, 프로세스 종료 후 자동 재시작과는 별개입니다.

구현은 `sensor-client/install_service.sh`의 `main()`(설치·등록·시작),
`render_service()`(서비스 정의), `systemd_quote()`(경로 처리)에 있습니다.

## 호환 래퍼

기존 센서 클라이언트 진입점은 과거 실행 명령과의 호환을 위한 deprecated wrapper로만 유지한다. 새 배포와 문서에서는 `sensor_client.py`를 사용한다. 호환 래퍼도 새 센서 파이프라인으로 라우팅하며 원시 센서 API를 사용하지 않는다.
