# 가상 센서 클라이언트

실제 센서 장비가 준비되기 전까지 서버의 데이터 수집 흐름을 검증하기 위한 가상 클라이언트입니다.

## 실행

백엔드 서버를 먼저 실행한 뒤 다음 명령을 실행합니다.

```bash
python sensor-client/mock_sensor_client.py --sensor-type both --cycles 10
```

열화상 또는 LiDAR 중 하나만 보낼 수도 있습니다.

```bash
python sensor-client/mock_sensor_client.py --sensor-type thermal --cycles 5
python sensor-client/mock_sensor_client.py --sensor-type lidar --cycles 5
```
