# 개발 가이드

이 문서는 CDAS 프로젝트를 진행할 때 사용할 최소한의 개발 규칙을 정리한다. 초기 단계에서는 과한 프로세스보다, 실행 가능한 상태를 유지하고 변경 기록을 명확히 남기는 것을 우선한다.

## 커밋 메시지

커밋 메시지는 Conventional Commits 형식을 가볍게 따른다.

```txt
<type>: <summary>
```

주로 사용하는 타입은 다음과 같다.

| 타입 | 용도 |
| --- | --- |
| `feat` | 기능 추가 |
| `fix` | 버그 수정 |
| `docs` | 문서 수정 |
| `test` | 테스트 추가 또는 수정 |
| `refactor` | 동작 변화 없는 코드 구조 개선 |
| `chore` | 설정, 정리, 기타 작업 |

예시:

```txt
feat: add initial sensor data pipeline prototype
docs: update sensor data contract
test: add congestion scoring tests
```

## 브랜치

기본 브랜치는 `main`을 사용한다. 큰 기능은 별도 브랜치에서 작업하고, 작은 문서 수정이나 정리는 필요할 때 `main`에 직접 커밋할 수 있다.

브랜치 이름 예시:

```txt
feature/mock-sensor-client
feature/hardware-thermal-test
fix/invalid-sensor-payload
docs/hardware-setup
```

## 테스트

커밋 전에는 최소한 다음 테스트를 실행한다.

```bash
python -m unittest discover -s tests
```

## 코드 스타일

- 센서 데이터 계약은 `docs/`에 문서화한다.
- 요청 검증, 저장, 혼잡도 계산, HTTP 처리는 가능한 한 분리한다.
- 실제 센서 연동 전에는 가상 클라이언트로 같은 payload 형식을 먼저 검증한다.
- 혼잡도 계산 로직은 테스트 가능한 함수로 유지한다.
- 외부 패키지나 프레임워크는 필요성이 명확해질 때 도입한다.

## 버전 태그

초기에는 Git tag를 남발하지 않는다. 가상 데이터 파이프라인, 실제 센서 연동, 웹 대시보드처럼 동작 가능한 단위가 완성될 때 `v0.x.0` 형태로 태그를 검토한다.
