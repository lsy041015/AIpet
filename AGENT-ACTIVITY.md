# 에이전트 작업 말풍선

## 작업 알림과 창 이동

- 완료·최종 실패가 확인되면 데스크톱 알림을 한 번 보냅니다. 처음 실행할 때 과거 결과는 알리지 않습니다. 기록 누락·시간 초과·중단은 완료로 취급하지 않습니다. 알림 표시는 OS의 방해 금지 설정을 따릅니다.
- Copilot의 permission.requested/completed, Codex의 사용자 입력·권한 요청 도구 이벤트를 읽어 대기를 강조합니다. 로그에 남지 않는 승인 화면은 감지할 수 없습니다.
- Antigravity는 현재 설치 버전의 단계 상태(WAITING=9, ERROR=7)를 확인합니다. 일반 대기는 '입력·작업 대기'로 표시하고, 도구 오류가 발생해도 전체 작업이 진행 중이면 최종 실패로 알리지 않습니다.
- 말풍선의 로고·이름·작업 내용을 클릭하면 연결된 창으로 이동합니다. Linux X11에서 PID·부모 프로세스 관계로 찾으며, 후보가 여럿이면 선택 메뉴를 표시합니다. 터미널 창 내부 탭·분할 패널까지 자동 선택하지는 않습니다. 연결할 창이 없으면 안내합니다.
- 작업 시작 기록부터 경과 시간을 표시합니다. 시작 기록을 읽지 못한 경우에는 '감지 후' 시간을 표시합니다. 5초마다 갱신합니다.
- 동시에 4개까지 표시하고, 나머지는 '전체 작업 보기'에서 선택할 수 있습니다.

펫 모드에서 로컬 작업 기록을 5초마다 읽고 로고·이름·작업 상태를 자동 표시합니다.
일시적으로 기록이 누락되면 마지막 확인 후 15초 동안 표시를 유지합니다. 명시적인 완료·실패·중단은 즉시 반영합니다. 작업 순서와 말풍선 위치를 유지하며, 펫 이동·모니터 변경·화면 경계에 필요한 경우에만 위치를 보정합니다.
여러 에이전트가 작업하면 함께 표시합니다. 커서를 올리면 기존 일정·한도 요약으로 전환합니다.
위젯을 펼치거나 드래그할 때 말풍선은 숨깁니다. 라이트·다크 테마를 따릅니다.

- Codex: `.codex/sessions` 작업 시작·도구 호출·완료 이벤트. 하위 에이전트·guardian 검토 기록은 별도 작업으로 세지 않습니다.
- GitHub Copilot: `.copilot/session-state`의 로컬 세션 이벤트. 해당 기록을 남기지 않는 IDE 작업은 감지하지 못합니다.
- Antigravity: CLI의 `.gemini/antigravity-cli/conversation_summaries.db`와 데스크톱의 `.gemini/antigravity/conversation_summaries.db` 실행 상태·작업 제목. 세부 도구 상태는 추측하지 않습니다.
- Claude Code: `.claude/projects/**/*.jsonl`과 Max 프로필(`~/.config/wego/check-ai-claude/max/projects`)의 사용자 입력·도구 호출·응답 완료를 읽습니다. 도구 이름으로 작업 종류를 표시합니다. 슬래시 명령 기록은 작업으로 세지 않고, Esc 중단은 작업 중단으로 표시합니다. 서브에이전트 기록은 부모 세션에 포함합니다. 도구 호출 후 30초 넘게 기록이 없으면 `승인 대기 또는 실행 중`으로 표시합니다. 로그에 승인 이벤트가 없어 둘을 구분하지 않습니다.

## 세션 상세 정보 (`read_activity(detailed=True)`)

각 기록에 `title`, `model`, `effort`, `todos`(`{text, status}` 목록), `current_task`(진행 중 항목), `subagents`(`{type, description, model, effort, status}` 목록)를 함께 담습니다. 알 수 없으면 `None`(서브에이전트는 빈 목록)입니다. 모델 ID는 `opus 5.5`처럼 줄여 표시하고, 다른 회사 모델은 기록된 이름 그대로 둡니다.

- Claude Code: 이름은 `custom-title`·`agent-name`(사용자 지정)이 `ai-title`보다 우선하고, 기록 끝부분에 없으면 세션 폴더의 `custom-title.json`을 읽습니다. 모델은 assistant `message.model`, effort는 같은 기록의 `effort`입니다. 할 일은 `TodoWrite`와 데스크톱 앱의 `update_plan`(items·advance·done·start)을 따릅니다. 서브에이전트는 결과가 없는 `Agent`/`Task` 호출, 또는 백그라운드 실행 후 완료 알림(`task-notification`)·`TaskStop`이 없는 호출입니다. 모델·effort는 호출 입력 → `subagents/agent-*.jsonl` 첫 응답 → `~/.claude/agents/<type>.md` 순으로 찾습니다. 오래 실행되어 호출 기록이 끝부분(256 KB)을 벗어난 백그라운드 에이전트는 `finished`가 없는 `*.meta.json`과 15분 안에 갱신된 로그로 찾습니다. 서브에이전트는 부모 세션의 `claude` 프로세스 안에서 돌므로, 그 프로세스가 없거나 호출·마지막 기록이 프로세스 시작 전이면(재시작으로 종료됨) 바로 뺍니다. 세션을 특정할 수 없는 프로세스(아래)나 Windows에서는 15분 기준만 씁니다.
- Codex: 이름은 `~/.codex/session_index.jsonl`의 `thread_name`, 모델·effort는 `turn_context`, 할 일은 `update_plan` 호출입니다. 하위 에이전트 rollout(`thread_spawn`)은 `parent_thread_id`로 부모 세션의 `subagents`에 붙고, 자체 `turn_context`의 모델·effort와 진행 상태를 씁니다. guardian 검토는 계속 제외합니다.
- Copilot: 모델은 `assistant.message`, effort는 `session.model_change`의 `reasoningEffort`입니다. 세션 이름·할 일은 로그에 없어 표시하지 않습니다.
- Antigravity: 대화 제목만 `title`로 넣습니다. 모델은 바이너리 기록이라 읽지 않습니다.
- opencode: `extra_agents=True`일 때만 포함합니다(화면에 opencode 항목이 생긴 뒤 켭니다). 실행 중인 `opencode` 프로세스가 `~/.local/share/opencode/opencode.db`를 열고 있을 때 읽기 전용으로 세션 제목·모델(variant를 effort로)·`todo` 표·하위 세션(서브에이전트)을 읽습니다. 계정·자격 증명 표는 읽지 않습니다.
- Gemini CLI·Cursor(cursor-agent)는 설치되어 있지 않아 지원하지 않습니다.

파일 수정·자료 확인·명령 실행·응답 준비·승인 대기 또는 실행 중으로 구분합니다. 명령 실행이 실제 테스트인지 임의로 판단하지 않습니다.
완료 이벤트가 오거나 기록이 2분간 갱신되지 않으면 숨깁니다. Antigravity CLI는 실행 중인 프로세스가 해당 대화 DB를 열고 있고 DB 상태도 작업 중이면 2분 제한을 적용하지 않습니다. Claude Code는 `claude` 프로세스 명령줄의 `--resume`/`--session-id` 세션, 그런 인자가 없으면 같은 프로젝트 폴더의 최근 세션 중 프로세스 시작 후 기록된 것에 2분 제한을 적용하지 않습니다. 다른 기록 없는 장시간 작업은 표시가 사라질 수 있습니다.
로컬 형식이 바뀌면 감지가 제한될 수 있습니다. 프로세스가 켜졌다는 이유만으로 작업 중으로 표시하지 않습니다.
자격 증명은 읽지 않고, 작업 기록을 외부로 전송하지 않습니다.
