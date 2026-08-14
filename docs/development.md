# 개발 가이드

CDAS는 Raspberry Pi에서 센서 데이터를 결합·추론하고 중앙 서버에는 최소 추론 결과만 보내는 구조를 기준으로 개발한다. 실행 가능 상태, 스레드 종료 안전성과 데이터 계약의 명확성을 우선한다.

## 커밋과 브랜치

커밋은 Conventional Commits 형식을 따른다.

```text
<type>: <summary>
```

주요 타입은 `feat`, `fix`, `docs`, `test`, `refactor`, `chore`다.

```text
feat: add sensor fusion pipeline
fix: stop lidar bridge when main thread exits
docs: update inference result contract
```

기본 브랜치는 `main`이며 큰 기능은 별도 브랜치에서 작업한다.

## 코드 책임

- `sensor_client.py`: Main 스레드 수명 주기, 공유 객체와 CLI
- `thermal_sensor_thread.py`: Lepton 열화상 센서 스레드
- `lidar_sensor_thread.py`: C1 라이다 센서 스레드
- `fusion_thread.py`: 센서 데이터 중합 스레드
- `model_adapter_thread.py`: 모델 어댑터 스레드와 모델 계약
- `communication_thread.py`: 서버 통신과 heartbeat 스레드
- `shared_runtime.py`: 공통 데이터 구조와 단일 슬롯 mailbox
- `environment_config.py`: `environment.json` 로딩과 검증
- `debug_images.py`: 디버그 PNG
- `rplidar_c1_bridge.cpp`: SLAMTEC SDK와 Python 사이 JSONL 브리지
- `backend/app/`: 결과 검증·저장·혼잡도·HTTP 처리

작업 스레드는 예외를 Main의 failure queue로 전달한다. block 동작은 종료 이벤트를 확인해야 하며 `Ctrl+C` 이후 센서 프로세스나 스레드가 남지 않아야 한다.

## 데이터와 큐 규칙

- 원시 데이터는 Raspberry Pi 내부 큐에만 둔다.
- Fusion은 설정 주기로 두 큐를 확인하고 둘 다 있으면 FIFO로 하나씩 결합한다.
- `flush_every_checks`번째 확인은 두 센서 큐를 비우고 건너뛴다.
- 디버그 이미지는 fused queue에 넣기 전에 저장한다.
- ModelAdapter와 Communication 사이는 손실 없는 단일 슬롯 mailbox를 쓴다.
- 서버 payload는 [데이터 계약](data-contract.md)을 따른다.
- 원시 픽셀과 LiDAR 포인트를 백엔드 API에 추가하지 않는다.

## 모델 플러그인

모델 런타임은 코어에 고정하지 않는다. `ModelAdapter(model_path)`와 `infer(sensor_data)`를 구현하고 다음을 테스트한다.

- 어댑터 및 모델 파일 로딩 실패
- 입력 전처리
- 0 이상의 정수 `people_count`
- 0~1의 유한한 `confidence`
- 모델 예외가 Main까지 전파되는지
- `--debug`에서 adapter module 누락 시 임의 인원 수를 전송하는지

## 테스트

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile sensor-client/*.py backend/app/*.py
git diff --check
```

하드웨어 테스트는 단위 테스트와 분리한다.

```bash
v4l2-ctl --device=/dev/video0 --list-formats-ext
sensor-client/build/rplidar_c1_bridge --port /dev/ttyUSB0
python3 sensor-client/sensor_client.py --debug --verbose
```

## 설정과 스타일

`environment.example.json`은 실행 가능한 스키마 예시다. 로컬에서는 복사 후 장치·서버·모델 경로를 수정한다.

```bash
cp environment.example.json sensor-client/environment.json
cp environment.example.json backend/app/environment.json
```

센서 클라이언트는 `sensor-client/environment.json`을, 백엔드는 `backend/app/environment.json`을 읽는다.

환경별 IP, 비밀값과 운영 모델은 커밋하지 않는다. 설정 의미를 바꾸면 예시, 검증 코드, README와 테스트를 함께 갱신한다.

- 요청 검증, 저장, 혼잡도와 HTTP 처리를 분리한다.
- 센서 I/O, 중합, 추론, 통신 책임을 스레드 경계와 맞춘다.
- 정상 반복 로그는 `--verbose`에서만 출력한다.
- 혼잡도 산식은 순수 함수로 두고 경계값을 테스트한다.
