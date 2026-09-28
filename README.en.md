<p align="center">
  <img src="assets/aipet-banner.png" alt="AIpet — a desktop pet for AI quotas, agent status and your calendar" width="100%">
</p>

<p align="center"><a href="README.md">한국어</a> · <b>English</b></p>

# AIpet (check AI)

> **A tiny pixel computer on your desktop that tells you how much AI quota is left, what your agents are doing, and what is next on your calendar.**

A PySide6 desktop widget for Ubuntu GNOME (X11). It reads Codex, Antigravity and GitHub Copilot quotas through the CLIs you are
already logged in to, shows running agent tasks as speech bubbles, and manages your primary Google Calendar.

> **Status: private personal project**, used daily on the author's Ubuntu GNOME X11 desktop. 54 tests in 5 files.

## Why

When you use three AI coding tools, you keep checking three things: how much quota each service has left, whether an agent is
editing, waiting for approval or already done, and when your next meeting starts. check AI puts all three in one widget:
quotas every 3 minutes, agent tasks every 5 seconds, calendar every 5 minutes.

- **No stored tokens**: quotas are read by calling each CLI, which uses its own login (`providers.py`).
- **Nothing leaves the machine**: agent bubbles read local session logs only.
- **Read-only quota checks**: no reset credits are spent. Only Google Calendar needs a separate sign-in.

## What it does

| Feature | Details |
|---|---|
| AI quotas | Remaining share and reset time per service; the lowest item as a big number; per-model and per-window detail. Keeps the last value and shows the error when a check fails. |
| Agent bubbles | Reads local Codex, Copilot and Antigravity logs and shows "editing files / reading / running a command / writing a reply" with elapsed time. Click a bubble to jump to that agent's window (X11, by process tree). |
| Calendar | Today, tomorrow and the next 7 days of your primary calendar; add, edit, delete; Korean quick-add such as `내일 오후 3시 회의` ("meeting tomorrow 3 pm"). |
| Notifications | Events 10 minutes ahead (5 or off), quota at 20% and 10% left, never twice for the same thing. |
| Pet | The Codex `tiny-crt` pet sprite (8×9 frames of 192×208) with 9 animations tied to the widget's state; folds into a small always-on-top circle. |
| Window | Light/dark, 0–70% transparency, resize and presets, autostart, single instance. |

## Services

| Service | Query | Login |
|---|---|---|
| Codex | `codex app-server` → `account/rateLimits/read` | `codex login` |
| Antigravity | `agy --print /usage --print-timeout 20s` | in `agy` |
| GitHub Copilot | `gh api /copilot_internal/user` (internal API used by the official extension) | `gh auth login` |

## Run

Needs Ubuntu GNOME (X11), Python 3, PySide6 and logged-in `codex`, `agy`, `gh`. The Google OAuth helpers are vendored in `vendor/`.

```bash
git clone https://github.com/lsy041015/AIpet.git ~/ai-quota-widget && cd ~/ai-quota-widget
python3 widget.py                      # run the widget
python3 widget.py --check              # read live quotas without opening a window
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v
QT_QPA_PLATFORM=offscreen python3 widget.py --smoke /tmp/check-ai.png --theme light
```

To connect Google Calendar, create a **Desktop app** OAuth client in a Google Cloud project with the Calendar API enabled,
download its JSON, and pick it in the widget's **Google 연결** dialog. The token is stored with mode `0600`; only the
`calendar.events.owned` scope is requested.

## Design notes

- Retrying an event insert reuses the same event id; a 409 means "already created", so the widget re-reads it instead of duplicating it.
- The Korean quick-add parser is a small regex grammar, not an LLM, and always shows a preview before saving.
- The pet sprite file is never modified; colors are swapped only while drawing.
- A file lock plus a user-only local socket keeps one instance: a second launch brings the running window forward and exits.
- A running agent process alone never counts as "working"; bubbles hide after 2 minutes without new log entries.

## Limits

X11 only (`fcntl`, `/proc`, `libX11`); no Wayland-only sessions, Windows or macOS. The Copilot quota relies on an internal API.
Approval screens that are not logged, terminal tabs and panes, shared calendars and Google Tasks are out of scope.

## Credits

Service logos (OpenAI Codex, Google Antigravity, GitHub Primer Octicons, MIT) are used only to identify each service; see
[assets/SOURCES.md](assets/SOURCES.md). The pet sprite is a copy of the Codex CLI `tiny-crt` pet by OpenAI, not original art; see
[assets/TINY-CRT-SOURCE.md](assets/TINY-CRT-SOURCE.md). This private project has no license file.

---

<p align="center"><sub>LSY.KOR · <a href="https://github.com/lsy041015">More projects</a></sub></p>
