"""Read quota snapshots using the user's existing CLI logins. No stored tokens."""
import json
import math
import os
import re
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class QuotaError(Exception):
    pass


def executable(name):
    path = shutil.which(name)
    if not path:
        for folder in (".local/bin", ".npm-global/bin"):
            candidate = Path.home() / folder / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                path = str(candidate)
                break
    if not path:
        raise QuotaError(f"{name} 설치·로그인 필요")
    return path


def start(args, env=None):
    return subprocess.Popen(
        [executable("rtk"), "proxy", executable(args[0]), *args[1:]],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True, env=env,
    )


def stop(process):
    # Terminate the whole CLI group, including any helper process.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    for stream in (process.stdin, process.stdout):
        if stream:
            stream.close()


def output(args, env=None, timeout=30):
    process = start(args, env=env)
    try:
        raw, _ = process.communicate(timeout=timeout)
        if process.returncode:
            raise QuotaError(f"{args[0]} 조회 실패 · 로그인·네트워크 확인")
        return raw.decode("utf-8")
    except subprocess.TimeoutExpired:
        raise QuotaError("조회 시간 초과 · 잠시 후 재시도") from None
    finally:
        stop(process)


def percent(value):
    if isinstance(value, bool):
        raise ValueError("invalid percentage")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("invalid percentage")
    return value


def reset_text(value):
    if not value:
        return "초기화 시각 미제공"
    try:
        if isinstance(value, str) and len(value) == 10:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%m/%d") + " 초기화 · 시각 미제공"
        moment = (datetime.fromtimestamp(value).astimezone()
                  if isinstance(value, (int, float))
                  else datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone())
        return moment.strftime("%m/%d %H:%M") + " 초기화"
    except (ValueError, OverflowError, OSError, TypeError):
        return "초기화 시각 미제공"


def window_name(minutes):
    if minutes == 10080:
        return "주간"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60}시간"
    return f"{minutes}분" if minutes else "기간 미제공"


def parse_codex(data):
    buckets = data.get("rateLimitsByLimitId") or {"codex": data.get("rateLimits")}
    rows = []
    for key, bucket in buckets.items():
        if not bucket:
            continue
        name = bucket.get("limitName") or ("Codex" if key == "codex" else key)
        for kind in ("primary", "secondary"):
            window = bucket.get(kind)
            if window:
                rows.append({"label": f"{name} · {window_name(window.get('windowDurationMins'))}",
                             "remaining": 100 - percent(window["usedPercent"]),
                             "reset": reset_text(window.get("resetsAt"))})
    if not rows:
        raise QuotaError("구독 한도 없음 · Codex에서 ChatGPT 로그인 확인")
    return rows


def codex():
    process = start(["codex", "app-server"])
    poller = selectors.DefaultSelector()
    poller.register(process.stdout, selectors.EVENT_READ)
    buffer = b""

    def send(message):
        process.stdin.write(json.dumps(message).encode() + b"\n")
        process.stdin.flush()

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "ai_quota_widget", "version": "1.0.0"}}})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not poller.select(min(1, max(0, deadline - time.monotonic()))):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                message = json.loads(line)
                if message.get("id") in (1, 2) and "error" in message:
                    raise QuotaError("Codex 조회 실패 · Codex 로그인·네트워크 확인")
                if message.get("id") == 1:
                    send({"method": "initialized"})
                    send({"id": 2, "method": "account/rateLimits/read", "params": {}})
                elif message.get("id") == 2:
                    data = message["result"]
                    buckets = data.get("rateLimitsByLimitId") or {}
                    plan = (data.get("rateLimits") or {}).get("planType")
                    if not plan:
                        plan = next((bucket.get("planType") for bucket in buckets.values()
                                     if bucket and bucket.get("planType")), None)
                    return {"rows": parse_codex(data), "plan": plan}
        raise QuotaError("Codex 응답 없음 · 잠시 후 재시도")
    finally:
        poller.close()
        stop(process)


def parse_antigravity(raw):
    rows = []
    periods = {"Weekly Limit Remaining": "주간", "Five Hour Limit Remaining": "5시간"}
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) == 4 and fields[2].endswith("%"):
            group, period, remaining, reset = fields
            rows.append({"label": f"{group.replace(' Models', '').replace(' models', '')} · {periods.get(period, period)}",
                         "remaining": percent(remaining[:-1]), "reset": reset_text(reset)})
    if not rows:
        raise QuotaError("Antigravity 한도 응답 없음 · agy /usage 확인")
    return rows


def antigravity():
    # The usage command supplies quotas, but does not identify the subscription.
    return {"rows": parse_antigravity(output(["agy", "--print", "/usage", "--print-timeout", "20s"])),
            "plan": None}


def parse_copilot(data):
    snapshots = data.get("quota_snapshots") or {}
    rows = []
    names = {"premium_interactions": "프리미엄", "premium_models": "프리미엄",
             "chat": "채팅", "completions": "자동완성"}
    for key, quota in snapshots.items():
        if key == "premium_interactions" and "premium_models" in snapshots:
            continue
        if not isinstance(quota, dict):
            continue
        unlimited = quota.get("unlimited") is True
        remaining = None if unlimited else percent(quota["percent_remaining"])
        detail = "무제한" if unlimited else ""
        total = quota.get("entitlement")
        count = quota.get("quota_remaining", quota.get("remaining"))
        if not unlimited and total is not None and count is not None:
            unit = "크레딧" if quota.get("token_based_billing") else "요청"
            detail = f"{count:g}/{total:g} {unit}" if isinstance(count, (int, float)) and isinstance(total, (int, float)) else ""
        rows.append({"label": names.get(key, key), "remaining": remaining,
                     "detail": detail, "reset": reset_text(quota.get("reset_date") or data.get("quota_reset_date"))})
    if not rows:
        raise QuotaError("Copilot 한도 없음 · gh 로그인·구독 확인")
    return rows


def copilot():
    # ponytail: the IDE's internal read API; replace if GitHub changes its schema.
    data = json.loads(output(["gh", "api", "/copilot_internal/user"]))
    return {"rows": parse_copilot(data), "plan": data.get("copilot_plan")}


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def claude_reset(text, now=None):
    # The CLI prints e.g. "Oct 6, 2pm (Asia/Seoul)", or only the time for today.
    # Month names are matched directly: Qt applies the system locale to strptime's %b.
    match = re.fullmatch(r"(?:([A-Z][a-z]{2}) (\d{1,2}), )?(\d{1,2})(?::(\d{2}))?([ap]m) \(([^()\s]+)\)", text)
    try:
        month, day, hour, minute, half, zone = match.groups()
        zone = ZoneInfo(zone)
        now = (now or datetime.now()).astimezone(zone)
        if not 1 <= int(hour) <= 12:
            raise ValueError("invalid hour")
        moment = datetime(now.year, MONTHS.index(month) + 1 if month else now.month, int(day or now.day),
                          int(hour) % 12 + (12 if half == "pm" else 0), int(minute or 0), tzinfo=zone)
        if moment < now - timedelta(days=1):
            moment = moment.replace(year=now.year + 1)
        return moment.astimezone().strftime("%m/%d %H:%M") + " 초기화"
    except (AttributeError, KeyError, ValueError, OSError):
        # Unknown formats stay readable as printed by the CLI.
        return text + " 초기화"


def parse_claude(data):
    if not isinstance(data, dict) or data.get("is_error") or not isinstance(data.get("result"), str):
        raise QuotaError("Claude Code 사용량 응답 없음 · claude /usage 확인")
    rows = []
    for line in data["result"].splitlines():
        name, separator, detail = line.partition(":")
        if not separator or not name.startswith("Current "):
            continue
        match = re.search(r"(\d+(?:\.\d+)?)% used", detail)
        if not match:
            continue
        try:
            remaining = 100 - percent(match.group(1))
        except ValueError:
            raise QuotaError("Claude Code 사용량 비율 오류") from None
        if name == "Current session":
            label = "현재 세션"
        elif name.startswith("Current week (") and name.endswith(")"):
            model = name[len("Current week ("):-1]
            label = "이번 주 · " + ("전체 모델" if model == "all models" else model)
        else:
            label = name
        reset = detail.partition("resets ")[2].strip()
        rows.append({"label": label, "remaining": remaining,
                     "reset": claude_reset(reset) if reset else "초기화 시각 미제공"})
    if not rows:
        raise QuotaError("Claude Code 사용량 응답 없음 · claude /usage 확인")
    return rows


def claude_max_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wego/check-ai-claude/max"


def claude_environment(config_dir=None):
    env = os.environ.copy()
    # Subscription profiles must use their own CLI login, not inherited API credentials.
    for key in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_USE_FOUNDRY"):
        env.pop(key, None)
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    else:
        # The default card is the default CLI login, even if the shell selected another profile.
        env.pop("CLAUDE_CONFIG_DIR", None)
    return env


def claude(config_dir=None):
    env = claude_environment(config_dir)
    login_message = "Max 5x 로그인 필요 · 계정 로그인 버튼을 눌러 주세요" if config_dir else "Claude 로그인 필요 · claude auth login 확인"
    try:
        auth = json.loads(output(["claude", "auth", "status", "--json"], env=env, timeout=5))
    except QuotaError:
        raise QuotaError(login_message) from None
    if not isinstance(auth, dict) or not auth.get("loggedIn") or auth.get("authMethod") != "claude.ai":
        raise QuotaError(login_message)
    if config_dir is not None:
        actual = auth.get("configDirectory")
        if not isinstance(actual, str) or Path(actual).resolve() != Path(config_dir).resolve():
            raise QuotaError("Max 계정 분리 확인 실패 · CLI 설정 확인")
        if auth.get("subscriptionType") != "max":
            raise QuotaError("Max 구독 계정으로 로그인해 주세요 · 현재 다른 구독 연결됨")
    data = json.loads(output(["claude", "--print", "/usage", "--output-format", "json",
                              "--no-session-persistence"], env=env, timeout=25))
    account = auth.get("email")
    return {"rows": parse_claude(data), "plan": auth.get("subscriptionType"),
            "account": account[:160] if isinstance(account, str) else ""}


def claude_max():
    profile = claude_max_dir()
    if not profile.is_dir():
        raise QuotaError("Max 5x 로그인 필요 · 계정 로그인 버튼을 눌러 주세요")
    return claude(profile)


PROVIDERS = {"codex": codex, "claude": claude, "claude_max": claude_max,
             "antigravity": antigravity, "copilot": copilot}
