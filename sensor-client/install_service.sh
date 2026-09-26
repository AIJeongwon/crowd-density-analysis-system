#!/usr/bin/env bash
# Install on the Raspberry Pi: sudo bash sensor-client/install_service.sh
set -euo pipefail

SERVICE_NAME='cdas-sensor-client.service'
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"
MANAGED_MARKER='# Managed by CDAS sensor-client/install_service.sh'
temporary_unit=''

fail() {
    printf '오류: %s\n' "$*" >&2
    exit 1
}

usage() {
    printf '%s\n' \
        '사용법: sudo bash sensor-client/install_service.sh [--user 사용자]' \
        '        bash sensor-client/install_service.sh --dry-run [--user 사용자]' \
        '' \
        '옵션 없이 sensor_client.py를 실행하는 부팅 서비스를 설치하고 즉시 시작합니다.' \
        '--user     서비스 실행 계정. 기본값은 sudo를 실행한 사용자입니다.' \
        '--dry-run  서비스 파일 내용만 출력합니다. 설치하거나 실행하지 않습니다.' \
        '--help     이 도움말을 출력합니다.'
}

cleanup() {
    if [[ -n "$temporary_unit" ]]; then
        rm -f -- "$temporary_unit"
    fi
}

systemd_quote() {
    local value="$1"
    # Reject control characters rather than allowing additional unit directives.
    if [[ "$value" =~ [[:cntrl:]] ]]; then
        fail '프로젝트 경로에 제어 문자를 사용할 수 없습니다.'
    fi
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//%/%%}"
    if [[ "${2:-}" == 'exec' ]]; then
        value="${value//\$/\$\$}"
    fi
    printf '"%s"' "$value"
}

render_service() {
    local working_directory quoted_python quoted_client
    # Unlike ExecStart, WorkingDirectory is one raw path, not a quoted word list.
    working_directory="${project_dir//%/%%}"
    quoted_python="$(systemd_quote "$python_path" exec)"
    quoted_client="$(systemd_quote "$client_path" exec)"
    printf '%s\n' \
        "$MANAGED_MARKER" \
        '[Unit]' \
        'Description=CDAS Raspberry Pi sensor client' \
        'Wants=network-online.target' \
        'After=network-online.target' \
        '' \
        '[Service]' \
        'Type=simple' \
        "User=${service_user}" \
        'SupplementaryGroups=video dialout' \
        "WorkingDirectory=$working_directory" \
        "ExecStart=$quoted_python $quoted_client" \
        'Environment=PYTHONUNBUFFERED=1' \
        'Restart=no' \
        'KillSignal=SIGINT' \
        'KillMode=mixed' \
        'TimeoutStopSec=90s' \
        'StandardOutput=journal' \
        'StandardError=journal' \
        'SyslogIdentifier=cdas-sensor-client' \
        'UMask=0077' \
        '' \
        '[Install]' \
        'WantedBy=multi-user.target'
}

main() {
    local dry_run=false
    local service_user="${SUDO_USER:-$(id -un)}"
    while (( $# > 0 )); do
        case "$1" in
            --user)
                (( $# >= 2 )) || fail '--user 뒤에 사용자 이름을 지정하세요.'
                service_user="$2"
                shift 2
                ;;
            --dry-run) dry_run=true; shift ;;
            --help|-h) usage; return 0 ;;
            *) fail "지원하지 않는 인자: $1" ;;
        esac
    done

    [[ "$service_user" =~ ^[a-zA-Z_][a-zA-Z0-9_.-]*\$?$ ]] || fail '잘못된 사용자 이름입니다.'
    local service_uid
    service_uid="$(id -u -- "$service_user")" || fail "사용자를 찾을 수 없습니다: $service_user"
    [[ "$service_uid" != '0' ]] || fail '--user에 Raspberry Pi의 일반 사용자 계정을 지정하세요.'

    local script_dir project_dir python_path client_path config_path
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
    project_dir="$(cd -- "$script_dir/.." && pwd -P)"
    if [[ "$project_dir" == *\\* || "$project_dir" =~ [[:space:]]$ ]]; then
        fail '프로젝트 경로에는 역슬래시나 마지막 공백을 사용할 수 없습니다.'
    fi
    python_path="$project_dir/.venv/bin/python"
    client_path="$script_dir/sensor_client.py"
    config_path="$script_dir/environment.json"
    [[ -x "$python_path" ]] || fail "가상환경 Python이 없습니다. 먼저 README의 가상환경 설치를 완료하세요: $python_path"
    [[ -r "$client_path" ]] || fail "클라이언트 파일을 읽을 수 없습니다: $client_path"
    [[ -r "$config_path" ]] || fail "설정 파일을 먼저 준비하세요: $config_path"

    if [[ "$dry_run" == true ]]; then
        render_service
        return 0
    fi

    (( EUID == 0 )) || fail '서비스 설치는 sudo bash sensor-client/install_service.sh로 실행하세요.'
    command -v systemctl >/dev/null || fail 'systemctl을 찾을 수 없습니다.'
    command -v runuser >/dev/null || fail 'runuser를 찾을 수 없습니다.'
    [[ -d /run/systemd/system ]] || fail 'systemd로 부팅된 Raspberry Pi에서 실행하세요.'
    getent group video >/dev/null || fail 'video 그룹이 없습니다.'
    getent group dialout >/dev/null || fail 'dialout 그룹이 없습니다.'
    runuser -u "$service_user" -- test -x "$python_path" || fail '서비스 계정이 가상환경 Python을 실행할 수 없습니다.'
    runuser -u "$service_user" -- test -r "$client_path" || fail '서비스 계정이 클라이언트 파일을 읽을 수 없습니다.'
    runuser -u "$service_user" -- test -r "$config_path" || fail '서비스 계정이 environment.json을 읽을 수 없습니다.'

    if [[ -e "$UNIT_PATH" || -L "$UNIT_PATH" ]]; then
        [[ -f "$UNIT_PATH" && ! -L "$UNIT_PATH" ]] || fail "일반 파일이 아닌 서비스 경로입니다: $UNIT_PATH"
        grep -qxF "$MANAGED_MARKER" "$UNIT_PATH" || fail "기존 수동 관리 서비스를 먼저 확인하세요: $UNIT_PATH"
    fi

    temporary_unit="$(mktemp --suffix=.service)"
    trap cleanup EXIT
    render_service > "$temporary_unit"
    if command -v systemd-analyze >/dev/null; then
        systemd-analyze verify "$temporary_unit"
    fi
    install -m 0644 -- "$temporary_unit" "$UNIT_PATH"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    # Also apply changes when this installer is run again on an existing service.
    systemctl restart "$SERVICE_NAME"
    printf '%s\n' \
        "설치 완료: $SERVICE_NAME (실행 계정: $service_user)" \
        '현재 실행 및 다음 부팅부터 자동 실행이 활성화되었습니다.' \
        "상태: sudo systemctl status $SERVICE_NAME" \
        "로그: sudo journalctl -u $SERVICE_NAME -f"
}

main "$@"
