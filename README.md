<p align="center">
  <img src="assets/aipet-banner.png" alt="AIpet — AI 한도, 에이전트 작업, 캘린더를 알려 주는 데스크톱 펫" width="100%">
</p>

<p align="center"><b>한국어</b> · <a href="README.en.md">English</a></p>

# AIpet (check AI)

> **남은 AI 한도, 지금 돌아가는 에이전트, 다음 일정을 바탕화면의 작은 컴퓨터 펫이 알려 줍니다.**
> Ubuntu GNOME(X11)용 PySide6 데스크톱 위젯입니다. Codex·Antigravity·GitHub Copilot의 남은 한도를
> 이미 로그인된 CLI로 조회하고, 작업 중인 에이전트를 말풍선으로 띄우고, Google Calendar 일정을 관리합니다.

> **상태: 개인용 비공개 프로젝트.** 작성자의 Ubuntu GNOME X11 데스크톱에서 매일 쓰고 있습니다.
> 테스트는 5개 파일, 54개입니다. 바탕화면의 **check AI** 아이콘으로 실행합니다.

---

## 목차

1. [왜 만들었나](#1-왜-만들었나)
2. [한눈에 보기](#2-한눈에-보기)
3. [동작 방식](#3-동작-방식)
4. [연동하는 서비스](#4-연동하는-서비스)
5. [설치와 실행](#5-설치와-실행)
6. [사용법](#6-사용법)
7. [펫과 에이전트 말풍선](#7-펫과-에이전트-말풍선)
8. [Google Calendar](#8-google-calendar)
9. [설정과 알림](#9-설정과-알림)
10. [저장소 구조](#10-저장소-구조)
11. [만들면서 고민한 것](#11-만들면서-고민한-것)
12. [한계](#12-한계)
13. [출처와 권리](#13-출처와-권리)

---

## 1. 왜 만들었나

AI 코딩 도구 세 개를 번갈아 쓰면 확인할 게 많아집니다.

- **어느 서비스 한도가 얼마나 남았지?** Codex, Antigravity, Copilot의 확인 방법이 제각각입니다.
- **에이전트가 지금 뭘 하고 있지?** 파일을 고치는 중인지, 승인을 기다리는지, 이미 끝났는지 창을 열어 봐야 압니다.
- **다음 회의는 언제지?** 캘린더는 또 다른 창에 있습니다.

check AI는 이 세 가지를 바탕화면의 위젯 하나에 모았습니다. 한도는 3분마다, 에이전트 작업은 5초마다,
일정은 5분마다 갱신합니다. 원칙은 세 가지입니다.

- **토큰을 따로 저장하지 않습니다.** 한도는 각 CLI가 가진 기존 로그인으로 조회합니다(`providers.py`).
- **작업 기록을 밖으로 보내지 않습니다.** 에이전트 말풍선은 로컬 세션 기록만 읽습니다.
- **조회만 합니다.** 한도 초기화 크레딧을 소비하지 않습니다. Google Calendar만 별도 로그인이 필요합니다.

## 2. 한눈에 보기

| 기능 | 하는 일 |
|---|---|
| **AI 한도** | 서비스별 잔여 비율과 초기화 시각(PC 현지 시간). 가장 적게 남은 항목은 큰 숫자로 보여 주고, **상세**에서 모델·기간별 한도를 봅니다. 조회에 실패하면 마지막 값과 오류를 함께 표시합니다. |
| **에이전트 말풍선** | Codex·Copilot·Antigravity의 로컬 작업 기록을 읽어 "파일 수정·자료 확인·명령 실행·응답 준비" 상태와 경과 시간을 말풍선으로 띄웁니다. 클릭하면 그 에이전트 창으로 이동합니다. |
| **캘린더** | 기본 캘린더의 오늘·내일·7일 일정 보기, 추가·수정·삭제, `내일 오후 3시 회의` 같은 한국어 빠른 입력. |
| **알림** | 일정 10분 전(5분 전·끄기 선택), 서비스별 잔여 20%·10%. 같은 알림은 두 번 보내지 않습니다. |
| **펫** | 도트 미니 컴퓨터 캐릭터가 9가지 동작으로 상태를 표현합니다. 접으면 작은 원이 되어 화면 위에 떠 있습니다. |
| **창** | 라이트·다크 테마, 투명도 0–70%, 크기 조절과 프리셋, 로그인 시 자동 시작, 단일 실행. |

## 3. 동작 방식

모든 기능이 PySide6 프로세스 하나(`widget.py`) 안에서 돌아갑니다. 서버나 백엔드는 없습니다.
네트워크와 파일 읽기는 전부 백그라운드 `QThread`에서 실행해 화면이 멈추지 않습니다.

```text
 codex app-server ─┐
 agy /usage ───────┼─▶ providers.py ──────(3분)──▶ AI 한도 카드
 gh api ───────────┘

 ~/.codex/sessions ──────────┐
 ~/.copilot/session-state ───┼─▶ agent_activity.py ─(5초)─▶ 작업 말풍선 ─(클릭)─▶ agent_windows.py
 ~/.gemini/…/*.db ───────────┘                                                  (X11, PID 트리로 창 찾기)

 Google Calendar API v3 ─▶ calendar_client.py (OAuth) ─(5분)─▶ calendar_panel.py
                            quick_event.py (한국어 빠른 입력 해석)

 notifications.py : notify-send 알림, 보낸 기록은 재시작 후에도 유지
 pixel_pet.py     : 스프라이트 시트에서 동작·프레임을 고르고 그릴 때 색을 바꿈
```

## 4. 연동하는 서비스

| 서비스 | 조회 경로 | 로그인 |
|---|---|---|
| Codex | `codex app-server` → `account/rateLimits/read` | `codex login` |
| Antigravity | `agy --print /usage --print-timeout 20s` | `agy`에서 로그인 |
| GitHub Copilot | `gh api /copilot_internal/user` | `gh auth login` |

- GPT 카드는 **ChatGPT 구독에 포함된 Codex 한도**입니다. ChatGPT 웹 채팅 한도나 OpenAI API 비용이 아닙니다.
- Codex 기간은 서버가 준 값을 그대로 씁니다. 첫 번째 기간이 늘 5시간인 것은 아닙니다.
- 플랜 이름은 Codex의 `planType`, Copilot의 `copilot_plan`을 씁니다. Antigravity `/usage`에는 플랜 정보가 없어서
  **설정 → Antigravity 플랜 직접 설정…**으로 입력하고 `(직접 설정)`으로 구분합니다. 남은 한도로 플랜을 추측하지 않습니다.
- Copilot 경로는 공식 Copilot 확장이 쓰는 **내부 API**입니다. 응답 형식이 바뀌면 수정이 필요할 수 있습니다.
  크레딧 방식과 요청 방식을 서버 응답으로 구분하고, 무제한을 0으로 표시하지 않습니다.

참고: [Codex App Server](https://learn.chatgpt.com/docs/app-server) ·
[Antigravity /usage](https://antigravity.google/docs/cli/commands/usage) ·
[Copilot 사용량 확인](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/monitor-premium-requests)

## 5. 설치와 실행

**필요한 것**: Ubuntu GNOME(X11), Python 3, PySide6, 로그인된 `codex`·`agy`·`gh` CLI.
Google 라이브러리 `google-auth-oauthlib==1.4.1`, `requests-oauthlib==2.0.0`은 `vendor/`에 들어 있고,
시스템에 설치된 `google-auth`, `requests`, `oauthlib`을 함께 씁니다.

```bash
git clone https://github.com/lsy041015/AIpet.git ~/ai-quota-widget
cd ~/ai-quota-widget

python3 widget.py                      # 위젯 실행
python3 widget.py --check              # 창 없이 실제 한도만 조회
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v                        # 테스트 54개
QT_QPA_PLATFORM=offscreen python3 widget.py --smoke /tmp/check-ai.png --theme light   # 화면을 이미지로 렌더링
```

- 설정 파일: `~/.config/wego/ai-quota-widget.conf`
- 자동 시작: `~/.config/autostart/ai-quota-widget.desktop` (템플릿은 저장소의 `ai-quota-widget.desktop`).
  설치 위치를 옮기면 `.desktop`의 `Exec`와 `Path`도 고치세요.
- 네트워크 호출은 각각 30초 제한입니다. 종료할 때 진행 중인 조회가 있으면 최대 약 32초 기다립니다.

## 6. 사용법

1. **check AI**를 실행하면 둥근 앱 아이콘만 있는 메뉴가 나옵니다. **AI 한도**(파란 그래프)와 **캘린더**(오늘 날짜) 중 하나를 누릅니다.
2. 선택한 기능만 화면에 남습니다. 오른쪽 아래 **메뉴**로 돌아가도 조회 결과·일정·스크롤 위치는 그대로 유지됩니다.
3. 위쪽 흰색 원을 누르면 위젯이 항상 위에 떠 있는 작은 원(미니 컴퓨터)으로 접힙니다. 다시 누르면 원래 위치·크기로 돌아옵니다.
4. 접힌 펫에 마우스를 올리면 다음 일정과 마지막 한도 조회 결과가 말풍선으로 나옵니다.
   10분 안에 시작하는 일정이 있으면 파란 시계, 잔여 20% 이하 서비스가 있으면 주황 배지가 붙습니다.
5. 펫을 우클릭하면 **애니메이션 미리보기**와 **빠른 일정 입력**을 고를 수 있습니다.
6. 톱니바퀴 **설정**에서 테마, 투명도, 동작 줄이기, 자동 시작, 알림 기준을 바꿉니다.

바로가기를 다시 누르면 새 창을 띄우지 않고 실행 중인 창을 앞으로 가져옵니다. 숨겨진 창이나 화면 밖으로 나간 창도 되찾습니다.

## 7. 펫과 에이전트 말풍선

**펫**은 Codex에서 선택된 `custom:tiny-crt` 펫의 원본 스프라이트(8열×9행, 칸당 192×208)를 씁니다.
동작은 9가지입니다: 기본 대기, 오른쪽 이동, 왼쪽 이동, 인사, 점프, 실패, 확인 대기, 작업 중, 결과 확인.

- 상태가 바뀌면 해당 동작을 3번 재생하고, 그다음에는 6배 느린 기본 대기를 반복합니다(Codex의 v1 펫 재생 규칙).
- 마우스를 올리면 점프하고, 드래그 중 수평으로 4px 넘게 움직이면 바라보는 방향이 바뀝니다.
- 자동으로 연결되는 상태: 첫 인사, 조회 중 작업, 실패, 완료 후 결과 확인, 한도 부족 시 확인 대기, 임박 일정 확인.
  Codex 작업 자체를 구독하는 것이 아니라 위젯의 작업에 동작을 대응시킨 것입니다.
- **동작 줄이기**를 켜면 각 상태의 첫 프레임만 보여 줍니다. 수동 미리보기는 그대로 재생됩니다.

**에이전트 말풍선**은 펫 모드에서 로컬 작업 기록을 5초마다 읽습니다.

| 에이전트 | 읽는 기록 |
|---|---|
| Codex | `.codex/sessions`의 작업 시작·도구 호출·완료 이벤트 |
| GitHub Copilot | `.copilot/session-state`의 로컬 세션 이벤트 |
| Antigravity | CLI·데스크톱의 `conversation_summaries.db` 실행 상태와 작업 제목 |

- 동시에 4개까지 띄우고, 나머지는 **전체 작업 보기**에서 고릅니다. 작업 시작 기록부터 경과 시간을 셉니다.
- 권한 요청·사용자 입력 대기를 강조합니다. 로그에 남지 않는 승인 화면은 감지할 수 없습니다.
- 완료·최종 실패가 확인되면 데스크톱 알림을 한 번 보냅니다. 처음 실행할 때 과거 결과는 알리지 않습니다.
- 말풍선을 누르면 Linux X11에서 PID와 부모 프로세스 관계로 해당 창을 찾아 앞으로 가져옵니다. 후보가 여럿이면 선택 메뉴가 뜹니다.
- 기록이 잠깐 끊기면 15초 동안 표시를 유지하고, 2분간 갱신이 없으면 숨깁니다.
  **프로세스가 켜져 있다는 이유만으로 작업 중이라고 표시하지 않습니다.**

## 8. Google Calendar

- 기본 캘린더의 오늘·내일·7일 일정을 시간순 카드로 보여 줍니다. 진행 중·다음 일정을 강조하고, 종일·반복 일정도 표시합니다.
- **+ 일정**으로 추가, 일정 열기로 제목·시간·종일 여부 수정과 삭제. 반복 일정은 선택한 한 건만 바꾸고,
  서버에서 이미 바뀐 일정은 덮어쓰지 않습니다.
- **빠른 입력** 예: `내일 오후 3시 회의`, `오늘 18:30 30분 동안 운동`, `모레 종일 출장`, `2026-10-01 09:00 회의`.
  길이를 생략하면 1시간입니다. 미리보기로 날짜·시간을 확인하고 **일정 추가**를 눌러야 저장됩니다.
- 권한은 `calendar.events.owned` 범위만 요청합니다. 공유 캘린더는 아직 지원하지 않습니다.

**처음 연결하기**: Google Cloud 프로젝트에서 Calendar API 활성화 → Google Auth Platform 앱 정보·대상 설정 →
본인 이메일을 테스트 사용자로 추가 → **데스크톱 앱** OAuth 클라이언트 생성 → JSON 다운로드.
위젯의 **Google 연결** 안내 창에서 그 JSON을 고르면 브라우저 로그인이 시작됩니다
([Python Calendar 설정 안내](https://developers.google.com/workspace/calendar/api/quickstart/python?hl=ko)).

로그인 정보는 `~/.config/wego/check-ai-calendar/token.json`에 권한 `0600`으로 저장하며, 오류 화면에 토큰이나 클라이언트 비밀을
보여 주지 않습니다. **연결 해제**는 로컬 로그인 정보를 지웁니다(Google 계정의 앱 권한 철회는 Google 계정 설정에서).

## 9. 설정과 알림

| 설정 | 내용 |
|---|---|
| 테마 | 라이트·다크. 다음 실행에도 유지 |
| 투명도 | 0–70% 슬라이더, 즉시 저장. 설정 메뉴는 선명하게 유지 |
| 크기·위치 | 가장자리 드래그나 프리셋으로 조절, 종료할 때 저장 |
| 동작 줄이기 | 애니메이션을 끄고 즉시 전환 (기본 전환: 접기 200ms, 화면 160ms) |
| 자동 시작 | 로그인할 때 시작, 기본 꺼짐 |
| 알림 | 일정 10분 전(5분 전·끄기), 한도 잔여 20%·10%를 서비스별로 켜고 끄기 |

알림은 Ubuntu `notify-send`로 보내며 위젯이 실행 중일 때만 동작합니다. 일정은 같은 시작 시각에 한 번,
한도는 임계점마다 한 번 알리고, 보낸 기록은 재시작 후에도 남습니다.

## 10. 저장소 구조

```text
AIpet/
├── widget.py              # 메인 앱: 창, 펫 렌더 루프, 설정, 단일 실행 (1626줄)
├── providers.py           # Codex·Antigravity·Copilot 한도 조회 (CLI 서브프로세스)
├── agent_activity.py      # 로컬 에이전트 작업 기록 읽기 → 말풍선 상태
├── agent_windows.py       # X11 창 찾기·활성화 (PID 관계)
├── calendar_client.py     # Google Calendar OAuth와 REST 호출
├── calendar_panel.py      # 캘린더 화면: 목록, 추가·수정 대화상자, 빠른 입력
├── quick_event.py         # 한국어 빠른 일정 입력 해석기
├── notifications.py       # 알림과 중복 방지 기록
├── pixel_pet.py           # 스프라이트 동작 선택과 표시용 색 변환
├── AGENT-ACTIVITY.md      # 에이전트 말풍선 설계 메모
├── ai-quota-widget.desktop  # 실행·자동 시작 템플릿
├── assets/                # 서비스 로고, 펫 스프라이트, 출처 문서
├── vendor/                # google-auth-oauthlib 1.4.1, requests-oauthlib 2.0.0
└── test_*.py              # unittest 5개 파일, 테스트 54개
```

## 11. 만들면서 고민한 것

- **한도를 보되 비밀은 갖지 않기.** 위젯은 Codex·Antigravity·GitHub 토큰을 저장하지 않습니다. 각 CLI를 서브프로세스로
  불러 그 CLI가 이미 가진 로그인을 쓰게 합니다. 저장하는 자격 증명은 사용자가 직접 연결한 Google Calendar 토큰뿐입니다.
- **중복 없는 일정 등록.** 응답을 못 받아 다시 눌러도 같은 이벤트 ID로 요청합니다. 서버가 409(이미 있음)를 돌려주면 새로 만들지 않고
  그 일정을 다시 읽어 옵니다(`calendar_client.py`).
- **LLM 없는 한국어 빠른 입력.** `quick_event.py`는 오늘·내일·모레·날짜·시각·기간을 정규식 문법으로 해석합니다.
  모르는 표현은 추측하지 않고 상세 입력을 안내하며, 저장 전에 반드시 미리보기를 거칩니다.
- **원본은 그대로, 색은 그릴 때만.** 펫 스프라이트 파일은 한 바이트도 바꾸지 않았습니다. 그릴 때 베이지를 흰색으로,
  초록을 하늘색으로 바꾸고 크로마키 배경 잔여물을 숨깁니다(`pixel_pet.py`).
- **실행은 한 번만.** 파일 잠금(`flock`)과 같은 사용자만 쓸 수 있는 로컬 소켓을 함께 써서, 두 번째 실행은 새 창 대신
  기존 창을 앞으로 부르고 바로 끝납니다.
- **확실할 때만 "작업 중".** 에이전트 프로세스가 떠 있다는 사실만으로는 작업 중으로 표시하지 않습니다. 명령 실행이 실제 테스트인지도
  임의로 판단하지 않습니다.

## 12. 한계

- Linux X11 전용입니다(`fcntl`, `/proc`, `libX11`). Wayland 전용 세션, Windows, macOS에서는 동작하지 않습니다.
- Copilot 한도는 공개 보장되지 않은 내부 API에 의존합니다.
- 로컬 기록 형식이 바뀌면 에이전트 감지가 제한될 수 있고, 기록을 남기지 않는 IDE 작업은 보이지 않습니다.
- 터미널 창 안의 탭·분할 패널까지 골라 주지는 못합니다.
- 공유 캘린더, 참석자 초대, Google Tasks는 지원하지 않습니다.

## 13. 출처와 권리

- **서비스 로고**: OpenAI(Codex), Google Antigravity 공식 배포 자료와 GitHub Primer Octicons(MIT)의 아이콘을 서비스 식별용으로만 씁니다.
  제휴나 후원을 뜻하지 않으며 상표는 각 권리자의 것입니다. 자세한 출처: [assets/SOURCES.md](assets/SOURCES.md).
- **펫 이미지**: Codex CLI의 `tiny-crt` 펫 스프라이트(OpenAI) 원본 복사본입니다. 이 프로젝트의 창작물이 아니며,
  표시할 때만 색을 바꿉니다. 자세한 내용: [assets/TINY-CRT-SOURCE.md](assets/TINY-CRT-SOURCE.md).
- **라이선스**: 비공개 개인 프로젝트로, 라이선스 파일이 없습니다.

---

<p align="center"><sub>LSY.KOR · <a href="https://github.com/lsy041015">다른 프로젝트 보기</a></sub></p>
