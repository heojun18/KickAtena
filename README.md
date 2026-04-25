# claude-resume-later

Claude Code 토큰 블록 리셋 후 세션을 자동으로 재개하는 스케줄러.

5시간 과금 블록에서 토큰이 소진되면 작업이 중단된다.
`claude-resume-later add`로 재개할 세션과 프롬프트를 등록하면, 블록 리셋 시각에 `claude --resume`이 자동 실행된다.

## 요구사항

- Python >= 3.9
- `ccusage` (npm): `npm i -g ccusage@18.0.11`
- Claude Code CLI (>= v2.1.116): `--resume`, `-p`, `--permission-mode bypassPermissions` 지원 필요
- systemd (Linux user service)

## 설치

```bash
cd /path/to/kickatena
pip install --user -e .
which claude-resume-later           # ~/.local/bin/claude-resume-later

npm i -g ccusage@18.0.11

# systemd 등록
mkdir -p ~/.config/systemd/user
ln -sf "$PWD/systemd/claude-resume-later.service" ~/.config/systemd/user/
ln -sf "$PWD/systemd/claude-resume-later.timer"   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-resume-later.timer
```

pipx 사용자: `pipx install -e .`로 대체 가능.

## 사용법

```bash
# 세션 등록
claude-resume-later add --session <uuid> --prompt "이어서 해줘"
claude-resume-later add --latest --prompt-file ./continue.txt

# 디버그/E2E용: run_after 직접 지정
claude-resume-later add --latest --prompt "계속해줘" --run-after "2026-04-25T14:00:00Z"

# 조회
claude-resume-later list [--status pending|running|failed|completed] [--json]
claude-resume-later status <job-id> [--json]

# 취소 (PENDING만 가능, RUNNING은 거부)
claude-resume-later cancel <job-id>

# 수동 실행 (systemd timer가 자동 호출)
claude-resume-later run-due
```

## 동작 원리

1. `add`: ccusage로 현재 활성 블록의 `endTime`을 조회해 `run_after`로 저장
2. systemd timer가 5분마다 `run-due` 실행
3. `run-due`: `run_after <= now`인 PENDING job 중 블록이 리셋된 것을 감지해 `claude --resume -p` 실행
4. 성공 시 COMPLETED, 실패 시 최대 3회 재시도 후 FAILED

### 블록 리셋 재확인 가드

`run_after` 시각이 지나도 바로 실행하지 않고, `block.start_time > job.created_at`를 확인해서
같은 블록 내에서 재실행되는 것을 방지한다.

## 런타임 상태

모든 파일은 `~/.local/state/claude-resume-later/` 하위에 생성 (XDG_STATE_HOME 준수).
디렉토리는 `0700`, 파일은 `0600` 권한.

- `queue.json`: 작업 큐
- `logs/<job-id>.log`: 실행 로그 (완료 후 7일 경과 시 자동 삭제)
- `.lock`: flock 기반 프로세스 잠금
- `ccusage-cache.json`: ccusage 응답 캐시 (30초 TTL)
- `prompts/<job-id>.txt`: 1KB 초과 프롬프트 분리 저장

## systemd timer

- `OnUnitActiveSec=5min`: 마지막 실행 기준 5분 간격
- `Persistent=true`: 머신 suspend/resume 후 놓친 실행을 1회 보상
- `TimeoutStartSec=5h`: runner의 4시간 타임아웃보다 여유 있는 상한

## 제거

```bash
systemctl --user disable --now claude-resume-later.timer
rm ~/.config/systemd/user/claude-resume-later.{service,timer}
systemctl --user daemon-reload
pip uninstall claude-resume-later
rm -rf ~/.local/state/claude-resume-later
```

## 테스트

```bash
pip install pytest
python3 -m pytest tests/ -v
```
