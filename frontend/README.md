# Crowd Now: Workers + D1

지도 웹과 API가 같은 Cloudflare Worker에서 실행됩니다. Python 백엔드는 함께 실행할 필요가 없습니다.
기존 Python 서버는 비교/로컬 대안으로 남겨 두었습니다. 센서 추론은 계속 Raspberry Pi에서 실행합니다.
이 구성 작업 자체는 Cloudflare 리소스 생성, 원격 DB 변경 또는 배포를 실행하지 않습니다.

## 로컬 실행 (Cloudflare 계정 불필요)

Node.js 22.13 이상이 필요합니다. 다음 명령은 `frontend` 디렉터리에서 실행합니다.

```bash
npm ci
# 아래 복사는 파일이 없을 때만 하세요. 기존 카카오 키를 덮어쓰지 마세요.
cp .env.example .env.local
cp .dev.vars.example .dev.vars
```

- `.env.local`: 카카오 **JavaScript 키**를 `NEXT_PUBLIC_KAKAO_MAP_APP_KEY`에 입력합니다.
- `NEXT_PUBLIC_API_BASE_URL`은 비워 두세요. 남아 있는 `http://127.0.0.1:8000` 값도 제거합니다.
- `.dev.vars`: `SENSOR_API_TOKEN`에 충분히 긴 무작위 토큰을 지정합니다.
  이 파일은 Git에서 제외되며, `NEXT_PUBLIC_` 변수에는 토큰을 넣으면 안 됩니다.
- 센서 토큰이 비어 있으면 쓰기 요청은 503으로 거부됩니다. 조회와 지도 확인은 가능합니다.
- 갱신 간격은 `NEXT_PUBLIC_STATUS_POLL_INTERVAL_MS=5000`, 데이터 유효 시간은
  `NEXT_PUBLIC_STATUS_WINDOW_SECONDS=30`입니다. 변경 후 개발 서버를 재시작합니다.

```bash
npm run db:migrate
npm run db:seed
npm run dev
```

터미널에 표시된 로컬 주소를 엽니다. 카카오 콘솔에도 해당 주소를 등록해야 합니다.
`db:seed`는 `backend/app/environment.example.json`에 있던 모란민속5일장 설정만 추가합니다.
**인원수 샘플은 삽입하지 않으며, 기존 장소 설정을 덮어쓰지 않습니다.**
실제 위치, 면적, 수용 인원은 `db/seed.sql`을 검토하거나 별도 SQL로 설정합니다.
위도와 경도는 둘 다 있어야 지도에 표시됩니다. 데이터가 아직 없으면 `NO_DATA`가 정상입니다.

`npm run db:migrate`, `npm run db:seed`는 항상 `--local`로 실행합니다.
로컬 D1은 `.wrangler/state`에 저장되며 재시작 후에도 유지됩니다. 개발 서버 역시 원격 바인딩을 사용하지 않습니다.
데모 자동 전환은 기본적으로 꺼져 있습니다. UI 데모가 필요할 때만 `NEXT_PUBLIC_ENABLE_DEMO_FALLBACK=true`로 설정하세요.

## 센서 연결

센서의 `environment.json`에서 `server.base_url`을 로컬 웹 주소 또는 나중에 배포한 HTTPS 주소로 지정합니다.
같은 PC 테스트라면 `http://127.0.0.1:3000`입니다. Raspberry Pi에서 `localhost`는 Pi 자체를 의미합니다.

센서 프로세스 환경에 `CDAS_SENSOR_API_TOKEN`을 설정합니다. 값은 Worker의 `SENSOR_API_TOKEN`과 같아야 합니다.
운영에서는 서비스 관리자 등의 비밀 환경 설정을 사용하고 저장소에 커밋하지 마세요.
토큰을 설정한 센서는 localhost 외의 평문 HTTP 전송을 거부합니다. Pi의 원격 연결에는 HTTPS가 필요합니다.
토큰이 없는 기존 Python 서버로 연결할 때는 해당 환경변수를 비웁니다.

로컬 수동 요청 예시입니다. `SENSOR_API_TOKEN`은 테스트 터미널에도 별도로 안전하게 설정해야 합니다.

```bash
curl http://localhost:3000/health
curl -X POST http://localhost:3000/api/inference-results \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $SENSOR_API_TOKEN" \
  --data '{"node_id":"pi-001","location_id":"moran-market-gate-1","timestamp":"2026-09-19T00:00:00Z","people_count":20,"confidence":0.8}'
curl http://localhost:3000/api/locations/statuses
```

## API 계약

| API | 동작 |
| --- | --- |
| `GET /health` | D1 연결과 테이블 존재 확인, 정상 200 / 장애 503 |
| `POST /api/inference-results` | Bearer 토큰 확인 후 저장, 성공 201 |
| `GET /api/inference-results/recent` | `limit`(1~100), `location_id`, `node_id` 필터 |
| `GET /api/locations/statuses` | 등록된 모든 장소, `window_seconds`(1~3600, 기본 30) |
| `GET /api/locations/{id}/status` | 장소별 조회, 데이터 없음/미등록 시 기존과 같이 404 |

응답 구조와 혼잡도 기준은 Python API와 같습니다. 최신 값 선택은 센서의 `timestamp`가 아니라
서버의 `received_at` 기준입니다. 센서가 전송한 `received_at`은 신뢰하지 않습니다.
시각은 UTC ISO 문자열로 응답하며 밀리초 정밀도로 저장합니다. 동일 수신 시각에서는 나중에 삽입한 값이 우선합니다.
인원수는 JavaScript의 안전한 정수 범위, 신뢰도는 0~1입니다.
JSON이 아닌 요청은 415, 잘못된 값은 400, 1MB 초과 본문은 413, 인증 실패는 401입니다.
누락된 인증 설정 또는 DB 장애는 503입니다. 조회는 공개 API이며 개인정보는 보내지 마세요.

측정값이 없거나 만료된 장소는 `people_count: null`, `status: NO_DATA`를 반환하고 좌표는 유지합니다.
새 장소 ID의 측정값도 저장되지만 `locations`에 장소를 등록하기 전에는 지도 목록에 나오지 않습니다.
재전송은 기존처럼 별도 기록으로 저장됩니다. 집계는 합계가 아니라 최신 값이므로 인원이 중복 합산되지 않습니다.

## 구성과 변경 지점

- `wrangler.jsonc` 전역: Worker 이름, D1 연결과 마이그레이션 경로. 현재 ID는 **로컬용 placeholder**입니다.
- `vite.config.ts`의 `defineConfig()`: Cloudflare 플러그인이 Wrangler 설정을 읽습니다.
- `app/api/**/route.ts`의 `GET()` / `POST()`: Workers API 진입점.
- `lib/server/api.ts`: 인증, 본문 크기 제한, 응답 및 오류 처리.
- `lib/server/inferenceRepository.ts`: D1 prepared statement 기반 저장/조회.
- `lib/server/scoring.ts`의 `buildLocationStatus()`: 밀도, 점유율, 혼잡도 계산.
- `lib/config.ts` 전역: 동일 출처 API와 폴링 설정.
- `migrations/0001_crowd.sql` 전역: 테이블과 인덱스. 적용한 파일은 수정하지 말고 새 마이그레이션을 추가하세요.

기존 `.openai/hosting.json`과 의존성은 기록 보존을 위해 남겼지만, 현재 빌드는 Sites 플러그인이나
Sites의 임시 DB 연결을 사용하지 않습니다. 직접 관리하는 Cloudflare Workers 설정이 기준입니다.

## 검증

```bash
npm run typecheck
npm run test:api
npm run build
npm run preview:worker
```

`test:api`는 설치된 Miniflare/workerd의 별도 임시 D1에서 테스트하며 개발 DB를 초기화하지 않습니다.
`preview:worker`는 빌드 결과를 로컬 Workers에서 실행합니다. Vite 빌드 후 생성된
`dist/server/wrangler.json`이 배포/미리보기용 설정입니다.
`npm run dev`를 중지한 뒤 미리보기를 실행하세요. 같은 로컬 SQLite 저장소를 쓰는
개발 서버와 빌드 미리보기를 동시에 실행하면 로컬 데이터베이스 연결이 충돌할 수 있습니다.
미리보기에는 프로젝트 루트의 `.wrangler/state`를 명시적으로 사용하므로 빈 DB가 따로 생성되지 않습니다.
Cloudflare 빌드 도구는 로컬 비밀 설정을 `dist/server/.dev.vars`에 복사합니다.
**루트 `.dev.vars`의 토큰을 바꾼 뒤에는 다시 빌드하고 미리보기를 재시작하세요.**
`dist` 전체는 Git에서 제외됩니다. 비밀 설정 사본을 공유하거나 정적 파일로 공개하지 마세요.

## 나중에 배포할 때만

1. Cloudflare에서 본인 계정의 `crowd-now-db`를 생성합니다.
2. `wrangler.jsonc` 전역의 `database_id`를 실제 ID로 교체합니다. DB 이름이 다르면 패키지의 DB 명령도 맞춥니다.
3. 본인 계정으로 Wrangler에 로그인하고 원격 DB 대상을 확인합니다.
4. 별도로 원격 마이그레이션을 적용하고, 검토한 실제 장소 설정을 넣습니다. 로컬 데이터는 자동 복사되지 않습니다.
5. 서버 비밀 설정 `SENSOR_API_TOKEN`과 클라이언트 빌드 환경변수를 준비합니다.
   카카오 키/5초 갱신 설정은 빌드 시 반영됩니다. Cloudflare 자동 빌드라면 루트 디렉터리는 `frontend`입니다.
6. 빌드 결과의 Wrangler 설정으로 배포합니다. 이 문서의 로컬 명령은 배포하지 않습니다.
7. 배포된 주소를 카카오 JavaScript SDK 도메인에 등록하고 센서 서버 주소를 바꿉니다.

Workers와 D1은 무료 사용량 한도가 있습니다. 5초 폴링은 화면 하나가 하루 종일 활성 상태이면
약 17,280회의 API 요청을 만들며 센서 전송과 heartbeat는 별도입니다.
조회는 장소별 최신 값 인덱스를 사용하지만, D1 기록은 메모리 저장소와 달리 계속 남습니다.
자동 삭제는 구현하지 않았으므로 실제 운영 전 보관 기간과 정리 정책을 정하고 사용량을 확인하세요.

공식 자료: [D1](https://developers.cloudflare.com/d1/get-started/),
[마이그레이션](https://developers.cloudflare.com/d1/reference/migrations/),
[Workers Secrets](https://developers.cloudflare.com/workers/configuration/secrets/).
