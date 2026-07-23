# 센서 클라이언트

라즈베리파이에서 실제 센서를 제어하는 프로그램과 서버의 데이터 수집 흐름을 검증하는 가상 클라이언트를 관리합니다.

## PureThermal 열화상 카메라

`thermal_camera.py`는 PureThermal 3와 Lepton 3.5가 제공하는 Y16 영상을 받아 다음 정보를 실시간으로 표시합니다.

- 컬러 열화상
- 화면 중앙과 사용자가 선택한 지점의 온도
- 전체 화면의 최저, 최고, 평균 온도
- 최저 및 최고 온도 지점
- 카메라 프레임 속도와 현재 표시 온도 범위

이 프로그램은 Y16 값이 TLinear 방식의 0.01 K 단위로 전달되는 구성을 전제로 합니다. 화면 표시용 색상 영상은 Y16 원본의 복사본으로 만들며, 온도 계산과 저장에는 변환 전 원본을 사용합니다.

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

목록에 `Y16` 또는 `16-bit Greyscale` 형식이 있어야 온도를 계산할 수 있습니다. 실제 장치 번호가 `/dev/video1`이라면 아래 실행 명령의 경로도 바꿉니다.

### 실행

프로젝트 루트에서 다음 명령을 실행합니다.

```bash
python3 sensor-client/thermal_camera.py --device /dev/video0
```

V4L2 직접 연결이 되지 않을 경우 GStreamer 백엔드를 명시할 수 있습니다.

```bash
python3 sensor-client/thermal_camera.py \
  --device /dev/video0 \
  --backend gstreamer
```

장면이 바뀔 때마다 색상 범위가 달라지는 것을 막으려면 고정 온도 범위를 지정합니다.

```bash
python3 sensor-client/thermal_camera.py \
  --device /dev/video0 \
  --min-temp 15 \
  --max-temp 45
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
| `A` | 자동 표시 온도 범위 사용 |
| `S` | 열화상, 온도 CSV, Y16 원본 저장 |
| `Q` 또는 `Esc` | 프로그램 종료 |

저장 결과는 기본적으로 `outputs/thermal/`에 생성됩니다. 이 디렉터리는 Git 추적 대상에서 제외되어 있습니다.

### 문제 확인

- 장치를 열 수 없으면 `v4l2-ctl --list-devices`에서 실제 장치 번호를 다시 확인합니다.
- Y16 형식 오류가 발생하면 `--list-formats-ext` 결과에서 16비트 형식 지원 여부를 확인합니다.
- 온도가 비현실적인 범위로 표시되면 PureThermal의 Y16 및 TLinear 설정을 확인합니다.
- 화면 창을 사용하므로 Raspberry Pi OS 데스크톱 세션이나 연결된 디스플레이 환경에서 실행해야 합니다.
- 세로 고정 패턴이 두드러지면 센서 예열 후 자동 FFC가 수행되는지 확인합니다.

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
