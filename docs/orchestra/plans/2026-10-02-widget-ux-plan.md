# check AI 위젯 기능·디자인 개선 계획 (2026-10-02)

## 1. Context and goal

`check AI` (PySide6, `widget.py` compact widget + `desktop.py` DesktopWindow subclass) shows AI quota cards, calendar, agent activity and billing. User-selected scope (2026-10-02):

- Feature A: time left until each quota resets ("1일 6시간 후").
- Feature B: desktop notification when an agent enters an approval/input-wait state.
- Design C: low-quota risk colors (≤20% orange, ≤10% red) on bars and numbers.
- Design D: compact widget "목록" (list) view of quotas, one row per service.
- Design E: desktop app dashboard metrics and 구독·결제 page redesign.

Non-goals: usage history/forecast, cost totals, card billing-row cleanup, new dependencies, changes to calendar features, README rewrite (main session updates docs at the end).

## 2. Global constraints

- Worktree: `/home/wego/ai-quota-widget/.worktrees/feature/widget-ux` (branch `feature/widget-ux`, base `f7c810b`). Never edit `/home/wego/ai-quota-widget` itself; never touch `~/.config/wego/*`; never kill or restart the running widget.
- Python 3.10, PySide6-Essentials 6.6.3 from `/home/wego/ai-quota-widget/.venv`. No new dependencies.
- Test command (from the worktree): `rtk proxy env QT_QPA_PLATFORM=offscreen /home/wego/ai-quota-widget/.venv/bin/python -E -m unittest discover -s .` — baseline `Ran 112 tests ... OK`. Focused: `... -m unittest test_<module>`.
- Tests stay offscreen with isolated QSettings and mocked providers/network, following existing `test_widget.py` / `test_desktop.py` patterns.
- UI text Korean, matching existing tone. Every new color is a `THEMES` token defined for both `light` and `dark`; text colors keep ≥ 4.5:1 contrast on the card background.
- New interactive controls get `setAccessibleName`, are keyboard reachable, and respect `reduce_motion` (no new animations required).
- Smoke mode (`smoke=True`) never writes settings.
- Workers do not commit; the main session commits each task after review.
- Match surrounding style: comment density, naming, minimal diffs. TDD: write the failing test first and record RED/GREEN.

## 3. Interfaces and dependencies

- Quota row dicts (providers → `Widget.received` → `Card.update_result`) gain key `"reset_at"`: `float` POSIX seconds when an exact reset moment is known, else `None`. Every row from `codex`, `antigravity`, `copilot`, `claude`, `claude_max` providers has the key. Existing `"reset"` strings are unchanged (notifications dedupe on them).
- `widget.time_left(reset_at, now=None) -> str` (Task 3) formats countdowns; `widget.risk_level(remaining) -> str` (Task 3) returns `"critical"` (≤10), `"low"` (≤20) or `""` (above 20 or `None`). Tasks 4 and 5 consume both.
- Risk styling (Task 3): widgets carry dynamic property `risk` with the `risk_level` value; the stylesheet colors `QProgressBar::chunk` and `QLabel` text with `THEMES[theme]["warning"]` (low) / `THEMES[theme]["danger"]` (critical), overriding the service color. New tokens: `danger` = light `#c62828`, dark `#ff8a80`.
- `ActivityTracker.update` (Task 2) also returns notices with `status == "waiting"`; `Widget.received_activity` (Task 3) sends them. Setting key `notify/agents/waiting` (bool, default `True`).
- Countdown refresh (Task 3): `Widget.update_clock` calls `self.update_countdowns()` once per minute change; Tasks 4/5 hook their own countdown/risk updates into `update_countdowns` / `received`.
- Quota view (Task 4): setting key `quota_view` = `"cards"` (default) | `"list"`; `Widget.set_quota_view(view)`; attributes `self.quota_view_switch`, `self.quota_list`. DesktopWindow always shows cards and hides the switch (Task 4 owns that line in `desktop.py`).
- Order: Task 1 ∥ Task 2 → Task 3 → Task 4 → Task 5.

## 4. Tasks

### Task 1: 한도 초기화 시각 값(reset_at) 제공

Files: `providers.py`, `test_providers.py`.

- Add `reset_moment(value) -> datetime | None` (aware local datetime) used by `reset_text`; epoch numbers and ISO strings give a moment, a 10-char `YYYY-MM-DD` date-only string gives `None` (no exact time), invalid values give `None`. `reset_text` output unchanged.
- Split `claude_reset` into `claude_reset_moment(text, now=None) -> datetime | None` and `claude_reset(text, now=None) -> str` built on it (output unchanged).
- Each row gets `"reset_at": moment.timestamp() if moment else None` (codex `resetsAt`, antigravity reset, copilot `reset_date`, claude/claude_max parsed reset; rows without reset → `None`).
- Tests (`test_providers.py`): `test_rows_carry_exact_reset_timestamp` — codex epoch `1790000000` → `reset_at == 1790000000.0`; antigravity ISO `2026-10-07T02:26:00Z` → equals `datetime(2026,10,7,2,26,tzinfo=timezone.utc).timestamp()`; copilot date-only `2026-11-01` → `None`; claude `"Oct 6, 2pm (Asia/Seoul)"` with fixed `now` → timestamp of 2026-10-06 14:00 KST; unparseable claude text → `None`.

### Task 2: 승인 대기 알림 이벤트

Files: `agent_activity.py`, `test_agent_activity.py`.

- In `ActivityTracker.update`, after the first scan (`self.ready`), append the item to `notices` when `status == "waiting"` and the previous record for that id was missing or not `"waiting"`. One notice per waiting episode; waiting → running → waiting notifies again; the 15-second grace re-append of an old waiting record must not notify; the first scan stays quiet.
- Tests: `test_waiting_state_notifies_once_per_episode` (first scan waiting → no notice; running→waiting → 1 notice; waiting again → 0; waiting→running→waiting → 1; record missing within grace → 0), `test_done_notices_unchanged_with_waiting` (existing done/failed behaviour intact).

### Task 3: 위험 색상 · 남은 시간 · 승인 대기 알림 연결 (compact cards)

Files: `widget.py`, `test_widget.py`. Consumes Tasks 1–2.

- Add `THEMES` token `danger` (values in §3). Add module functions `risk_level(remaining)` and `time_left(reset_at, now=None)`:
  - minutes = ceil(seconds / 60); `None` → `""`; seconds ≤ 0 → `"곧 초기화"`; d = minutes // 1440, h = minutes % 1440 // 60, m = minutes % 60; days: `"{d}일 {h}시간 후"` (`"{d}일 후"` if h == 0); hours: `"{h}시간 {m}분 후"` (`"{h}시간 후"` if m == 0); else `"{m}분 후"`.
- `Card`: set `risk` on the primary `amount` label and `bar` from the primary row, and on each detail row's value label and bar; restyle (unpolish/polish) on change. Primary reset label text becomes `"<detail> · <reset> · <time_left>"` with empty parts skipped; detail rows the same. Add `Card.refresh_countdown(now=None)` that rewrites only those texts.
- `Widget.update_countdowns()` calls every card's `refresh_countdown`; `update_clock` calls it when the minute changes (also while collapsed — the clock still ticks once per minute then).
- Stylesheet: risk rules for `QProgressBar[risk='low'|'critical']::chunk` and `QLabel[risk=...]` that win over `QWidget[service=...] QProgressBar::chunk`.
- `received_activity`: for a `"waiting"` notice, if `notify/agents/waiting` is true, `send_notification(SERVICES[agent][0] + " · 승인·입력 대기", item["label"])`; done/failed handling unchanged. Add checkable action "에이전트 승인 대기" in the 설정 → 알림 menu (default checked, toggles the setting).
- Tests: `test_time_left_formats` (exact cases: 30h5m → "1일 6시간 후", 48h → "2일 후", 3h → "3시간 후", 3h20m → "3시간 20분 후", 45m → "45분 후", 20s → "1분 후", -5s → "곧 초기화", None → ""); `test_risk_colors_on_bars_and_amounts` (light theme: remaining 7 → bar chunk pixel equals `danger`, 15 → `warning`, 50 → service color; amount `property("risk")` matches; same for a detail row); `test_card_countdown_updates_each_minute` (reset_at = now+2h → text ends "2시간 후"; advance mocked now 61 min → "59분 후"); `test_waiting_notification_respects_setting` (notice sent with title "Claude Code · 승인·입력 대기"; setting false → not sent).

### Task 4: 작은 위젯 목록 보기

Files: `widget.py`, `test_widget.py`, `desktop.py` (only: hide `quota_view_switch` and force cards in DesktopWindow), `test_desktop.py` (one assertion test). Consumes Task 3.

- Quota page top: segmented switch `self.quota_view_switch` with checkable buttons "카드" / "목록" (accessible names "카드 보기" / "목록 보기"), styled like the existing light/dark segment.
- `self.quota_list`: one row per service in `SERVICES` order — logo (20 px, same assets as `Card`), service name, right-aligned primary amount (`"43%"`, `"무제한"`, `"—"` before data, `"조회 실패"` on error with no data), caption line `"<primary label> · <time_left or reset>"`, 4 px bar; amount and bar carry `risk`. Row tooltip lists all rows of that service. Clicking a row (or Enter/Space when focused) switches to cards and scrolls that card into view.
- All 5 rows fit without scrolling at 430×780.
- `set_quota_view(view)` swaps the cards scroll and the list, checks the right button, persists `quota_view` unless smoke; the stored value is restored on start. List updates from `received` and `update_countdowns`.
- Tests: `test_quota_list_view_rows_and_persistence` (switch to list → 5 rows, Codex row amount "43%", risk "" ; a 7% row has risk "critical"; new Widget with same settings starts in list), `test_quota_list_row_click_opens_card`, `test_list_fits_default_size` (each row bottom within the visible list area at 430×780), and in `test_desktop.py` `test_desktop_hides_quota_view_switch` (switch hidden, cards shown even if setting says list).

### Task 5: 데스크톱 대시보드·구독 화면 개편

Files: `desktop.py`, `test_desktop.py`. Consumes Tasks 3–4.

- Dashboard metrics: replace `("accounts", "AI 계정")` with `("lowest", "가장 적게 남은 한도")`: value = minimum primary remaining among cards with data (unlimited excluded), e.g. `"7%"`, with `risk`; a caption under every metric value (`self.metric_notes[key]`): lowest → `"<service name> · <primary label>"` (no data: value "—", caption "조회 전"); agents → `"승인 대기 N건"` in warning color when any task is waiting, else `"실행 중 N건"` / `"실행 중인 작업 없음"`; billing → `"N일 후"` / `"오늘"`; events → keep current value, caption `"오늘 기준"`.
- AI 한도 요약 rows: value label carries `risk`; add a 4 px bar per row with `risk` (service color when not low).
- 구독·결제 page: summary panel at top — with records: `"설정된 구독 N / 5 · 다음 결제 MM/DD (N일 후) · <service>"`; without any record: guidance `"각 서비스의 결제일 설정에서 결제 기준일과 주기를 입력하면 다음 결제 예정일과 남은 날짜를 표시합니다. 결제일 자동 조회·자동 결제는 하지 않습니다."`. Service billing cards in a grid: 2 columns when `self.width() >= 1060` (same breakpoint as `reflow_cards`), else 1, reflowed on resize; each card shows logo + name, plan caption, large next-payment `"MM/DD"` or `"미설정"`, caption `"<N일 후|오늘|확인 필요> · <주기>"`, button `"결제일 설정"` / `"수정"`.
- Tests: `test_dashboard_lowest_quota_metric_and_risk`, `test_dashboard_agents_metric_shows_waiting`, `test_billing_page_summary_and_empty_state` (fixed `today`), `test_billing_cards_reflow_two_columns` (width 1160 → 2 columns, 900 → 1).

## 5. Verification

- Per task: focused module tests RED then GREEN, then the full command in §2 (expect `OK`, 112 + new tests).
- Main session per task: scope check, diff review, offscreen screenshots of affected pages in light and dark (`--smoke` with isolated `XDG_CONFIG_HOME`, or a render script) and visual review.
- Final: full suite twice, live `widget.py --check`, smoke renders of compact and desktop, both themes.

## 6. Review focus and recovery

- Stylesheet specificity: risk colors must actually override service chunk colors (pixel test).
- Countdown must not cause per-second relayout; refresh only on minute change.
- Waiting notifications: Claude's ambiguous "승인 대기 또는 실행 중" also notifies once per episode (known ceiling: a long silent tool call notifies once; the setting turns it off).
- DesktopWindow re-parents compact widgets; new compact widgets (switch, list) must not leak into the desktop layout.
- On failure: same worker fixes; after the fix-round limit the main session rules and fixes inline. Preserve `/home/wego/ai-quota-widget` checkout untouched.
