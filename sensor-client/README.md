# 센서 클라이언트

라즈베리파이에서 실제 센서를 제어하는 프로그램과 서버의 데이터 수집 흐름을 검증하는 가상 클라이언트를 관리합니다.

## PureThermal 열화상 카메라

`thermal_camera.py`는 PureThermal 3와 Lepton 3.5가 제공하는 Y16 영상을 받아 다음 정보를 실시간으로 표시합니다.

- 컬러 열화상
- 화면 중앙과 사용자가 선택한 지점의 온도
- 전체 화면의 최저, 최고, 평균 온도
- 선택, 최저 및 최고 온도 지점 옆의 실시간 온도
- 카메라 프레임 속도와 현재 표시 온도 범위

이 프로그램은 Y16 값이 TLinear 방식의 0.01 K 단위로 전달되는 구성을 전제로 합니다. 기본 화면은 이상치를 일부 제외한 장면 온도에 맞춰 색상 범위를 자동으로 조정합니다. 작은 온도 흔들림과 고정 패턴이 과장되지 않도록 시간 필터와 경계 보존 필터도 적용합니다. 온도 계산과 CSV 및 Y16 저장에는 필터를 적용하지 않은 원본을 사용합니다.

### 라즈베리파이 준비

Raspberry Pi OS에서 다음 패키지를 설치합니다.

```bash
sudo apt update
sudo apt install -y python3-opencv python3-numpy v4l-utils \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
```

PureThermal 3를 USB 데이터 케이블로 연결한 뒤 장치와 지원 형식을 확인합니다.

```bash
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 --list-formats-ext
```

목록에 `Y16` 또는 `16-bit Greyscale` 형식이 있어야 온도를 계산할 수 있습니다. 프로그램은 목록에서 텔레메트리 행이 없는 Y16 해상도를 자동으로 선택합니다. 실제 장치 번호가 `/dev/video1`이라면 아래 실행 명령의 경로도 바꿉니다.

### 실행

프로젝트 루트에서 다음 명령을 실행합니다.

```bash
python3 sensor-client/thermal_camera.py --device /dev/video0
```

해상도 자동 감지가 되지 않을 때만 장치가 지원하는 크기를 직접 지정합니다.

```bash
python3 sensor-client/thermal_camera.py \
  --device /dev/video0 \
  --width 80 \
  --height 60
```

V4L2 직접 연결이 되지 않을 경우 GStreamer 백엔드를 명시할 수 있습니다.

```bash
python3 sensor-client/thermal_camera.py \
  --device /dev/video0 \
  --backend gstreamer
```

기본값은 화면의 이상치를 일부 제외한 온도 범위를 자동으로 사용합니다. 자동 범위가 지나치게 좁어지는 것을 막아야 할 때만 절대 기준 범위를 지정합니다.

```bash
python3 sensor-client/thermal_camera.py \
  --device /dev/video0 \
  --anchor-min-temp 20 \
  --anchor-max-temp 40
```

서로 다른 장면을 같은 색상 기준으로 비교하려면 고정 온도 범위를 지정합니다.

```bash
python3 sensor-client/thermal_camera.py \
  --device /dev/video0 \
  --min-temp 15 \
  --max-temp 45
```

필터 적용 전 화면을 직접 확인하려면 원본 표시 방식을 선택합니다.

```bash
python3 sensor-client/thermal_camera.py \
  --device /dev/video0 \
  --display-mode raw
```

카메라 설치 방향에 따라 영상을 회전할 수 있습니다.

```bash
python3 sensor-client/thermal_camera.py --rotate 90
```

### 조작

| 입력 | 동작 |
| --- | --- |
| 마우스 왼쪽 클릭 | 해당 픽셀의 온도 선택 |
| `C` | 선택 지점을 화면 중앙으로 초기화 |
| `P` | 색상 팔레트 변경 |
| `D` | 부드러운 화면과 원본 화면 전환 |
| `A` | 자동 표시 온도 범위 사용 |
| `S` | 열화상, 온도 CSV, Y16 원본 저장 |
| `Q`, `Esc` 또는 창 닫기 버튼 | 프로그램 종료 |

저장 결과는 기본적으로 `outputs/thermal/`에 생성됩니다. 이 디렉터리는 Git 추적 대상에서 제외되어 있습니다.

### 문제 확인

- 장치를 열 수 없으면 `v4l2-ctl --list-devices`에서 실제 장치 번호를 다시 확인합니다.
- Y16 형식 오류가 발생하면 `--list-formats-ext` 결과에서 16비트 형식 지원 여부를 확인합니다.
- 온도가 비현실적인 범위로 표시되면 PureThermal의 Y16 및 TLinear 설정을 확인합니다.
- 화면 창을 사용하므로 Raspberry Pi OS 데스크톱 세션이나 연결된 디스플레이 환경에서 실행해야 합니다.
- 세로 고정 패턴이 두드러지면 센서 예열 후 자동 FFC가 수행되는지 확인합니다.
- 부드러운 화면에서도 고정 패턴이 남으면 `D` 키로 원본 화면과 비교합니다.
- 센서 모듈에 직접 닿는 바람은 본체 온도를 빠르게 바꿔 측정값을 흔들 수 있으므로 차단합니다.

현재 버전은 실시간 확인과 로컬 저장까지만 담당합니다. 수동 FFC 제어, 방사율 보정, 백엔드 전송은 실제 장비 동작을 확인한 후 추가합니다.

## 공개 LLVIP 모델 테스트

`thermal_person_detector.py`는 LLVIP 적외선 영상으로 학습된 공개 YOLOv5 모델을 이용해 열화상에서 사람을 시험 검출합니다. PureThermal의 컬러 팔레트 화면이 아니라 Y16 온도 프레임을 흑백 적외선 영상으로 변환해 모델에 입력하며, 화면에는 사람 영역과 신뢰도, 검출 인원수, 추론 시간이 표시됩니다. 영상은 저장하거나 서버로 전송하지 않습니다.

LLVIP에서 공개한 모델은 YOLOv5l 기반 PyTorch 가중치입니다. Raspberry Pi 5 2GB에서 PyTorch를 함께 실행하는 부담을 줄이기 위해 PC에서 ONNX로 한 번 변환하고, 라즈베리파이에서는 CPU용 ONNX Runtime으로 실행합니다. 모델 파일과 원본 압축 파일은 크기와 배포 조건 때문에 저장소에 커밋하지 않고 `models/`에 둡니다.

### 모델 준비

1. [LLVIP 공식 저장소](https://github.com/bupt-ai-cz/LLVIP)의 `Google-Drive-Yolov5-model` 링크에서 `yolov5_trained_model.rar`를 내려받아 압축을 풉니다. 압축 파일에 포함된 두 가중치 중 열화상용 `yolov5_infrared.pt`를 사용합니다.
2. PC에 Python 3.12 일회성 가상 환경을 만들고 공식 저장소의 YOLOv5 코드를 준비합니다. 아래 조합은 실제 ONNX 변환을 확인한 버전입니다.

```bash
git clone https://github.com/bupt-ai-cz/LLVIP.git
cd LLVIP/yolov5
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch==2.5.1 torchvision==0.20.1
python -m pip install -r requirements.txt onnx==1.17.0 "numpy<2"
```

3. 압축에서 나온 학습 가중치 경로를 지정해 고정 크기 ONNX 모델을 만듭니다.

```bash
python export.py \
  --weights /path/to/yolov5_infrared.pt \
  --img-size 320 320 \
  --include onnx \
  --opset 13
```

4. 생성된 `.onnx` 파일을 이 프로젝트의 `models/llvip-yolov5l-320.onnx`로 옮깁니다. 640 입력과 비교하려면 `--img-size 640 640`으로 별도 변환합니다.

PyTorch 가중치는 로딩 과정에서 코드를 실행할 수 있으므로 공식 LLVIP 링크에서 받은 파일만 변환하고, 변환 후 일회성 가상 환경은 제거합니다. LLVIP 공식 저장소는 데이터 사용을 비상업적 연구, 교육, 개인 실험 용도로 제한합니다. 이 테스트도 같은 범위에서 사용하고, 외부 발표나 결과물에는 LLVIP 논문과 공식 저장소를 출처로 표시합니다.

### 라즈베리파이 실행 환경

CPU용 ONNX Runtime은 프로젝트 가상 환경에 설치하고, 기존에 운영체제 패키지로 설치한 OpenCV와 NumPy는 함께 사용합니다.

```bash
uname -m
sudo apt install -y python3-venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install onnxruntime
```

`uname -m` 결과가 `aarch64`인 64비트 Raspberry Pi OS를 기준으로 합니다.

### 실행

프로젝트 루트에서 다음 명령을 실행합니다.

```bash
python sensor-client/thermal_person_detector.py \
  --device /dev/video0 \
  --model models/llvip-yolov5l-320.onnx \
  --input-size 320
```

기본 `synchronized` 표시 모드는 검출에 사용한 프레임과 박스를 함께 표시합니다. 추론은 CPU 부하를 줄이기 위해 기본 초당 4회로 제한하며, 상단 `FPS`도 실제 추론 주기를 표시합니다. 다른 주기를 시험할 때는 `--inference-fps`를 지정합니다.

```bash
python sensor-client/thermal_person_detector.py \
  --device /dev/video0 \
  --model models/llvip-yolov5l-320.onnx \
  --input-size 320 \
  --inference-fps 4
```

박스가 약간 늦게 따라와도 카메라 영상을 계속 보고 싶을 때만 `--display-mode live`를 사용합니다.

```bash
python sensor-client/thermal_person_detector.py \
  --device /dev/video0 \
  --model models/llvip-yolov5l-320.onnx \
  --input-size 320 \
  --display-mode live
```

카메라 설치 방향이나 백엔드가 다르면 기존 열화상 프로그램과 같은 옵션을 사용합니다.

```bash
python sensor-client/thermal_person_detector.py \
  --device /dev/video0 \
  --model models/llvip-yolov5l-320.onnx \
  --input-size 320 \
  --rotate 90 \
  --backend gstreamer
```

검출이 지나치게 적거나 잘못된 박스가 많을 때는 신뢰도 기준을 조절해 비교합니다.

```bash
python sensor-client/thermal_person_detector.py \
  --device /dev/video0 \
  --model models/llvip-yolov5l-320.onnx \
  --input-size 320 \
  --confidence 0.20
```

ONNX 모델의 고정 입력 크기와 `--input-size`는 같아야 합니다. 640 모델을 비교할 때는 `models/llvip-yolov5l-640.onnx`와 `--input-size 640`을 함께 사용합니다.

Raspberry Pi 5 2GB에서 같은 160x120 프레임을 5회 추론한 1차 측정 평균은 320 입력 약 408ms, 640 입력 약 1782ms였습니다. 320 입력을 우선 사용하되, 실제 설치 거리에서 작은 사람의 미탐이 늘어나는지 별도로 확인합니다.

`Q`, `Esc` 또는 창 닫기 버튼으로 종료합니다.

Lepton 3.5의 원본 해상도와 현재 연결에서 확인된 영상은 모두 160x120입니다. 공개 모델의 정확도 평가는 먼저 검출 가능성을 확인하는 수준으로 해석하고, 실제 설치 거리와 각도에서 별도 촬영 데이터를 모은 뒤 오탐과 미탐을 다시 측정합니다.

## 가상 센서 클라이언트

백엔드 서버를 먼저 실행한 뒤 다음 명령을 실행합니다.

```bash
python sensor-client/mock_sensor_client.py --sensor-type both --cycles 10
```

열화상 또는 LiDAR 중 하나만 보낼 수도 있습니다.

```bash
python sensor-client/mock_sensor_client.py --sensor-type thermal --cycles 5
python sensor-client/mock_sensor_client.py --sensor-type lidar --cycles 5
```
