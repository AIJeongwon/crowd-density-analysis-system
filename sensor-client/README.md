# 센서 클라이언트

이 폴더에는 CDAS 백엔드로 센서 데이터를 전송하는 세 가지 클라이언트가 있다.

- `mock_sensor_client.py`: 실제 장비 없이 열화상·LiDAR 데이터 흐름을 시험한다.
- `raspberry_pi_sensor_client.py`: 센서 프로그램이 전처리한 지표를 전송한다.
- `lepton_sensor_client.py`: PureThermal USB-UVC 보드에 연결된 Lepton 3.5의 Y16 프레임을 직접 수집하고 전송한다.

모든 클라이언트는 Python 표준 라이브러리만 사용한다. Lepton 클라이언트는 프레임 수집을 위해 시스템의 `v4l2-ctl`을 추가로 사용한다.

## 백엔드 서버 준비

라즈베리파이가 같은 네트워크에서 접근할 수 있도록 서버를 모든 네트워크 인터페이스에 바인딩한다.

```bash
python3 -m backend.app.server --host 0.0.0.0 --port 8000
```

정상 수신된 데이터는 서버 터미널에 한 줄로 기록된다. 수신한 Lepton 프레임을 열화상 PNG로 `.log_data/`에 저장하려면 `--logging` 옵션을 붙인다.

```bash
python3 -m backend.app.server --host 0.0.0.0 --port 8000 --logging
```

서버 PC의 방화벽에서 TCP 8000 포트 접근을 허용해야 한다. 외부 인터넷에 직접 노출하기 전에는 HTTPS와 센서 인증을 적용해야 한다.

## 가상 센서 클라이언트

실제 센서 장비가 준비되기 전에 서버의 데이터 수집 흐름을 검증할 때 사용한다.

```bash
python3 sensor-client/mock_sensor_client.py --sensor-type both --cycles 10
```

열화상 또는 LiDAR 중 하나만 전송할 수도 있다.

```bash
python3 sensor-client/mock_sensor_client.py --sensor-type thermal --cycles 5
python3 sensor-client/mock_sensor_client.py --sensor-type lidar --cycles 5
```

## 범용 Raspberry Pi 센서 클라이언트

`raspberry_pi_sensor_client.py`는 라즈베리파이에서 전처리한 열화상 또는 LiDAR 지표를 CDAS 백엔드로 전송한다. 센서 원천 데이터 판독 방식은 장비마다 다르므로, 이 클라이언트는 센서 드라이버가 만든 지표를 전송하는 역할만 담당한다.

### 단일 측정값 전송

서버 PC의 LAN IP를 `--server-url`에 지정한다.

```bash
python3 sensor-client/raspberry_pi_sensor_client.py \
  --server-url http://192.168.0.10:8000 \
  --location-id moran-market-gate-1 \
  --sensor-type thermal \
  --device-id thermal-pi-001 \
  --metrics-json '{"hotspot_count":12,"avg_temp":29.8,"max_temp":36.5,"valid":true}'
```

LiDAR 전송 예시:

```bash
python3 sensor-client/raspberry_pi_sensor_client.py \
  --server-url http://192.168.0.10:8000 \
  --location-id moran-market-gate-1 \
  --sensor-type lidar \
  --device-id lidar-pi-001 \
  --metrics-json '{"object_count":15,"avg_distance":2.1,"min_distance":0.8,"valid":true}'
```

`--device-id`를 생략하면 `<라즈베리파이 호스트명>-<센서 타입>`을 사용한다.

### 센서 코드에서 직접 사용

센서 드라이버 코드와 `raspberry_pi_sensor_client.py`를 같은 디렉터리에 두면 일반 Python 모듈처럼 불러올 수 있다.

```python
import time

from raspberry_pi_sensor_client import SensorClient


client = SensorClient(
    server_url="http://192.168.0.10:8000",
    device_id="thermal-pi-001",
    location_id="moran-market-gate-1",
    timeout=5,
    max_attempts=3,
)

while True:
    # 아래 값은 사용하는 센서 드라이버에서 읽고 계산한다.
    hotspot_count = 12
    avg_temp = 29.8
    max_temp = 36.5

    client.send(
        "thermal",
        {
            "hotspot_count": hotspot_count,
            "avg_temp": avg_temp,
            "max_temp": max_temp,
            "valid": True,
        },
    )
    time.sleep(5)
```

연결 실패, 타임아웃, HTTP 429 및 서버 오류는 지수 백오프로 재시도한다. 잘못된 요청을 의미하는 일반적인 HTTP 4xx 응답은 재시도하지 않는다.

### JSON Lines 연속 전송

기존 센서 프로그램이 한 줄에 하나씩 JSON 지표를 출력한다면 파이프로 연결할 수 있다.

```bash
python3 thermal_reader.py | python3 sensor-client/raspberry_pi_sensor_client.py \
  --server-url http://192.168.0.10:8000 \
  --location-id moran-market-gate-1 \
  --sensor-type thermal \
  --device-id thermal-pi-001
```

`thermal_reader.py`의 출력 형식:

```json
{"hotspot_count":12,"avg_temp":29.8,"max_temp":36.5,"valid":true}
```

성공한 요청마다 서버의 JSON 응답이 표준 출력에 한 줄씩 기록된다. 전송 실패 후 재시도도 모두 소진되면 종료 코드 1을 반환하므로 systemd의 `Restart=on-failure`와 함께 사용할 수 있다.

### 환경 변수

```bash
export CDAS_SERVER_URL=http://192.168.0.10:8000
export CDAS_API_KEY=replace-after-server-auth-is-added
```

현재 백엔드는 API 키를 검증하지 않는다. `CDAS_API_KEY`는 서버 인증 기능이 추가된 이후 사용할 수 있도록 전송 헤더만 미리 지원한다.

## Lepton 3.5 센서 클라이언트

`lepton_sensor_client.py`는 PureThermal 호환 USB-UVC 보드에 연결된 Lepton 3.5에서 160×120 Y16 프레임을 수집한다. 각 프레임은 `/tmp/rbp_client`에 임시 저장되고, 픽셀별 원시 센서값과 온도 요약 지표로 변환된 뒤 CDAS 백엔드로 전송된다.

### 요구 사항

- 32비트 또는 64비트 Linux가 설치된 Raspberry Pi
- Lepton 3.5 및 PureThermal 호환 USB-UVC 보드
- Python 3.9 이상
- `v4l2-ctl`

Raspberry Pi OS 또는 Debian/Ubuntu에서 V4L2 유틸리티를 설치한다.

```bash
sudo apt update
sudo apt install -y v4l-utils
```

사용할 장치가 160×120 해상도의 `Y16 ` 형식을 제공하는지 확인한다. `Y16` FOURCC에는 마지막 공백 문자가 포함된다.

```bash
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

Lepton은 다음과 같이 설정되어 있어야 한다.

- Radiometry: 활성화
- TLinear: 활성화
- TLinear 해상도: 0.01 K
- AGC: 비활성화

클라이언트는 각 Y16 값을 센티켈빈으로 간주하고 `섭씨 = 원시값 / 100 - 273.15` 공식을 사용한다.

### 실행

필수 옵션은 서버 주소(`--ip/-i`), V4L2 장치 경로(`--dev/-d`), 수집 간격(`--interval/-t`)이다. 실행 로그가 필요하면 값 없이 `--verbose/-v`를 추가한다.

```bash
python3 sensor-client/lepton_sensor_client.py \
  --ip 192.168.0.10 \
  --dev /dev/video0 \
  --interval 5 \
  --verbose
```

`--verbose`를 생략하면 실행 로그를 출력하지 않는다. 아래처럼 짧은 옵션도 사용할 수 있다.

```bash
python3 sensor-client/lepton_sensor_client.py \
  -i http://192.168.0.10:9000 \
  -d /dev/video0 \
  -t 1.5
```

스킴이나 포트를 생략한 서버 주소는 `http://<서버>:8000/api/sensor-readings`로 해석한다. 포트를 포함한 주소와 완전한 URL도 사용할 수 있다. 실행 로그는 `--verbose/-v` 옵션을 지정했을 때만 표준 오류로 출력된다.

장치 ID 기본값은 `<호스트명>-lepton-3.5`이며 위치 ID 기본값은 호스트명이다. 위치 ID는 환경 변수로 변경할 수 있다.

```bash
export CDAS_LOCATION_ID=moran-market-gate-1
```

### 임시 파일과 재전송

클라이언트는 먼저 `.part` 파일로 프레임을 수집하고 파일 크기가 정확히 38,400바이트인지 검사한다. 유효한 파일은 `.y16`으로 변경한 뒤 19,200개의 원시 픽셀값과 프레임 메타데이터를 포함한 JSON으로 전송한다.

전송에 성공하면 임시 파일을 삭제한다. 전송에 실패한 파일은 `/tmp/rbp_client`에 유지하고 다음 수집 주기 또는 프로세스 재시작 후 타임스탬프 순서로 다시 전송한다.

네트워크 장애가 길어지면 수집 주기마다 약 38.4KB가 누적된다. 운영 환경에서는 남은 공간을 감시하거나 `/tmp/rbp_client`를 충분한 크기의 임시 파일 시스템에 배치해야 한다.

## 수신 확인

서버의 최근 열화상 데이터는 다음 API로 확인할 수 있다.

```bash
curl "http://127.0.0.1:8000/api/readings/recent?limit=1&sensor_type=thermal"
```

서버를 `--logging` 옵션으로 실행했다면 `.log_data/`에 열화상 컬러 PNG가 생성된다. 서버를 재시작하면 메모리에 저장된 최근 측정값은 사라지지만 PNG 파일은 유지된다.
