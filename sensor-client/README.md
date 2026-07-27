# 센서 클라이언트

라즈베리파이에서 실제 센서를 제어하는 프로그램과 서버의 데이터 수집 흐름을 검증하는 가상 클라이언트를 관리합니다.

## PureThermal 열화상 카메라

`thermal_camera.py`는 PureThermal 3와 Lepton 3.5가 제공하는 Y16 영상을 받아 다음 정보를 실시간으로 표시합니다.

- 컬러 열화상
- 화면 중앙과 사용자가 선택한 지점의 온도
- 전체 화면의 최저, 최고, 평균 온도
- 최저 및 최고 온도 지점
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
