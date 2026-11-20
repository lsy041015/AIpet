<p align="center">
  <img src="assets/aipet-banner.png" alt="AIpet — AI 한도, 에이전트 작업, 캘린더를 알려 주는 데스크톱 펫" width="560">
</p>

<p align="center"><b>한국어</b> · <a href="README.en.md">English</a></p>

# AIpet (check AI)

바탕화면에 사는 작은 도트 컴퓨터가 AI 한도, 돌아가는 에이전트, 다음 일정을 알려 줘요.

Codex, Antigravity, Copilot을 번갈아 쓰다 보니 한도가 얼마 남았는지, 에이전트가 아직 일하는 중인지, 다음 회의가 언제인지
확인하려고 창을 계속 열게 되더라고요. 그래서 셋을 위젯 하나에 모았어요. Ubuntu GNOME(X11)에서 PySide6로 만들었고,
제 데스크톱에서 매일 켜 두고 씁니다.

할 수 있는 것:

- **AI 한도** — 서비스별 남은 비율과 초기화 시각을 3분마다 갱신해요. 토큰은 저장하지 않고, 이미 로그인된
  `codex`, `agy`, `gh` CLI를 불러서 조회만 해요.
- **에이전트 말풍선** — Codex·Copilot·Antigravity의 로컬 작업 기록을 5초마다 읽어서 "파일 수정 중", "명령 실행 중" 같은
  상태를 띄워요. 누르면 그 에이전트 창으로 넘어가요. 프로세스가 켜져 있다는 이유만으로 "작업 중"이라고 하지는 않아요.
- **Google Calendar** — 오늘·내일·7일 일정 보기, 추가·수정·삭제, `내일 오후 3시 회의` 같은 한국어 빠른 입력.
  빠른 입력은 LLM이 아니라 작은 정규식 문법이고, 저장 전에 꼭 미리보기를 보여 줘요.
- **알림** — 일정 10분 전, 한도 20%·10% 남았을 때. 같은 알림은 두 번 안 보내요.
- **펫** — 접으면 항상 위에 떠 있는 작은 원이 되고, 상태에 따라 9가지 동작을 해요. 라이트·다크 테마와 투명도도 바꿀 수 있어요.

접힌 펫에 마우스를 올리면 다음 일정과 마지막 한도 조회 결과가 말풍선으로 나와요. 10분 안에 일정이 있으면 파란 시계,
20% 이하로 남은 서비스가 있으면 주황 배지가 붙어요. 우클릭하면 애니메이션 미리보기와 빠른 일정 입력을 고를 수 있고요.

만들면서 신경 쓴 것도 몇 가지 있어요.

- 일정 추가 응답을 못 받아서 다시 눌러도 같은 이벤트 ID로 보내요. 서버가 409를 주면 새로 만들지 않고 그 일정을 다시 읽어 와요.
- 펫 스프라이트 파일은 한 바이트도 안 바꿨어요. 그릴 때만 베이지를 흰색으로, 초록을 하늘색으로 바꿔요.
- 파일 잠금과 로컬 소켓으로 한 번만 실행돼요. 바로가기를 또 누르면 새 창 대신 떠 있는 창을 앞으로 가져와요.

## 실행

필요한 것: Ubuntu GNOME(X11), Python 3, PySide6, 로그인된 `codex`·`agy`·`gh`. Google OAuth 라이브러리는 `vendor/`에 넣어 뒀어요.

```bash
git clone https://github.com/lsy041015/AIpet.git ~/ai-quota-widget
cd ~/ai-quota-widget

python3 widget.py                      # 위젯 실행
python3 widget.py --check              # 창 없이 실제 한도만 조회
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v                        # 테스트 54개
QT_QPA_PLATFORM=offscreen python3 widget.py --smoke /tmp/check-ai.png --theme light   # 화면을 이미지로 렌더링
```

캘린더를 쓰려면 Google Cloud 프로젝트에서 Calendar API를 켜고 **데스크톱 앱** OAuth 클라이언트 JSON을 받은 다음,
위젯의 **Google 연결** 창에서 그 파일을 고르면 돼요([설정 안내](https://developers.google.com/workspace/calendar/api/quickstart/python?hl=ko)).
권한은 `calendar.events.owned`만 요청하고, 토큰은 `0600` 권한으로 로컬에 저장해요.

## 아쉬운 점

X11 전용이라 Wayland, Windows, macOS에서는 안 돌아가요. Copilot 한도는 공식 확장이 쓰는 내부 API에 기대고 있어서
응답 형식이 바뀌면 고쳐야 할 수 있어요. 로그에 안 남는 승인 화면이나 공유 캘린더도 아직 못 다뤄요.

서비스 로고는 식별용으로만 쓰고([assets/SOURCES.md](assets/SOURCES.md)), 펫 그림은 제가 그린 게 아니라 Codex CLI의
`tiny-crt` 펫(OpenAI) 원본이에요([assets/TINY-CRT-SOURCE.md](assets/TINY-CRT-SOURCE.md)). 개인 프로젝트라 라이선스 파일은 없어요.

<sub>[다른 프로젝트 보기](https://github.com/lsy041015)</sub>
