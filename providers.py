"""Read quota snapshots using the user's existing CLI logins. No stored tokens."""
import base64
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
        for folder in (".local/bin", ".npm-global/bin", ".opencode/bin"):
            candidate = Path.home() / folder / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                path = str(candidate)
                break
    if not path:
        raise QuotaError(f"{name} 설치·로그인 필요")
    return path


def cli(args):
    """The CLI command line, wrapped in `rtk proxy` only when rtk is installed (optional)."""
    command = [executable(args[0]), *args[1:]]
    try:
        return [executable("rtk"), "proxy", *command]
    except QuotaError:
        return command


def start(args, env=None):
    return subprocess.Popen(
        cli(args),
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


def reset_moment(value):
    # Date-only strings carry no exact moment.
    if not value or (isinstance(value, str) and len(value) == 10):
        return None
    try:
        return (datetime.fromtimestamp(value).astimezone()
                if isinstance(value, (int, float))
                else datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone())
    except (ValueError, OverflowError, OSError, TypeError, AttributeError):
        return None


def reset_at(moment):
    return moment.timestamp() if moment else None


def reset_text(value):
    if not value:
        return "초기화 시각 미제공"
    if isinstance(value, str) and len(value) == 10:
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%m/%d") + " 초기화 · 시각 미제공"
        except ValueError:
            return "초기화 시각 미제공"
    moment = reset_moment(value)
    return moment.strftime("%m/%d %H:%M") + " 초기화" if moment else "초기화 시각 미제공"


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
                             "reset": reset_text(window.get("resetsAt")),
                             "reset_at": reset_at(reset_moment(window.get("resetsAt")))})
    if not rows:
        raise QuotaError("구독 한도 없음 · Codex에서 ChatGPT 로그인 확인")
    return rows


def jwt_claims(token):
    # Reads the payload only; the signature is not verified and the token is never stored or logged.
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (AttributeError, IndexError, ValueError, TypeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def codex_home(account):
    return Path.home() / ".codex" if account.get("use_default") else Path(account["profile_dir"])


def codex_subscription(home):
    """Plan and paid-until date from the ChatGPT id_token claims in <CODEX_HOME>/auth.json."""
    try:
        token = json.loads((Path(home) / "auth.json").read_text())["tokens"]["id_token"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    claims = jwt_claims(token).get("https://api.openai.com/auth")
    if not isinstance(claims, dict):
        return {}
    plan = claims.get("chatgpt_plan_type")
    try:
        until = claims["chatgpt_subscription_active_until"].replace("Z", "+00:00")
        renews = datetime.fromisoformat(until).astimezone().date().isoformat()
    except (KeyError, AttributeError, ValueError, TypeError):
        renews = None
    return {"plan": plan if isinstance(plan, str) else None, "renews": renews}


def codex(account):
    subscription = codex_subscription(codex_home(account))
    process = start(["codex", "app-server"], env=environment(account))
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
                # Server requests and notifications carry "method"; only replies to our ids count.
                if not isinstance(message, dict) or "method" in message or message.get("id") not in (1, 2):
                    continue
                if "error" in message:
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
                    return {"rows": parse_codex(data), "plan": plan or subscription.get("plan"),
                            "renews": subscription.get("renews")}
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
                         "remaining": percent(remaining[:-1]), "reset": reset_text(reset),
                         "reset_at": reset_at(reset_moment(reset))})
    if not rows:
        raise QuotaError("Antigravity 한도 응답 없음 · agy /usage 확인")
    return rows


def antigravity(account):
    # The usage command supplies quotas, but does not identify the subscription.
    return {"rows": parse_antigravity(output(["agy", "--print", "/usage", "--print-timeout", "20s"],
                                             env=environment(account))),
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
        remaining = None
        if not unlimited:
            # One odd bucket must not fail the account: skip it, clamp the rest.
            value = quota.get("percent_remaining")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                continue
            remaining = min(100.0, max(0.0, float(value)))
        detail = "무제한" if unlimited else ""
        total = quota.get("entitlement")
        count = quota.get("quota_remaining", quota.get("remaining"))
        if not unlimited and total is not None and count is not None:
            unit = "크레딧" if quota.get("token_based_billing") else "요청"
            detail = f"{count:g}/{total:g} {unit}" if isinstance(count, (int, float)) and isinstance(total, (int, float)) else ""
        if not unlimited and quota["percent_remaining"] < 0:
            detail = "초과 사용"
        reset = quota.get("reset_date") or data.get("quota_reset_date")
        rows.append({"label": names.get(key, key), "remaining": remaining, "detail": detail,
                     "reset": reset_text(reset), "reset_at": reset_at(reset_moment(reset))})
    if not rows:
        raise QuotaError("Copilot 한도 없음 · gh 로그인·구독 확인")
    return rows


def copilot(account):
    # ponytail: the IDE's internal read API; replace if GitHub changes its schema.
    data = json.loads(output(["gh", "api", "/copilot_internal/user"], env=environment(account)))
    login = data.get("login")
    return {"rows": parse_copilot(data), "plan": data.get("copilot_plan"),
            "account": login[:160] if isinstance(login, str) else ""}


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def claude_reset_moment(text, now=None):
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
        if not month and moment <= now:
            moment += timedelta(days=1)  # a time-only reset is the next one, e.g. "1am" printed at 23:30
        if moment < now - timedelta(days=1):
            moment = moment.replace(year=now.year + 1)
        return moment.astimezone()
    except (AttributeError, KeyError, ValueError, OSError):
        return None


def claude_reset(text, now=None):
    moment = claude_reset_moment(text, now)
    # Unknown formats stay readable as printed by the CLI.
    return moment.strftime("%m/%d %H:%M") + " 초기화" if moment else text + " 초기화"


def parse_claude(data):
    if not isinstance(data, dict) or data.get("is_error") or not isinstance(data.get("result"), str):
        raise QuotaError("Claude Code 사용량 응답 없음 · claude /usage 확인")
    rows = []
    for line in data["result"].splitlines():
        name, separator, detail = line.strip().partition(":")
        key = name.lower()
        if not separator or not key.startswith("current "):
            continue
        match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*used", detail, re.I)
        if not match:
            continue
        try:
            remaining = 100 - percent(match.group(1))
        except ValueError:
            raise QuotaError("Claude Code 사용량 비율 오류") from None
        if key == "current session":
            label = "현재 세션"
        elif key.startswith("current week (") and key.endswith(")"):
            model = name[len("Current week ("):-1]
            label = "이번 주 · " + ("전체 모델" if model.lower() == "all models" else model)
        else:
            label = name
        reset = re.search(r"resets\s+(.*)", detail, re.I)
        reset = reset[1].strip() if reset else ""
        rows.append({"label": label, "remaining": remaining,
                     "reset": claude_reset(reset) if reset else "초기화 시각 미제공",
                     "reset_at": reset_at(claude_reset_moment(reset))})
    if not rows:
        raise QuotaError("Claude Code 사용량 응답 없음 · claude /usage 확인")
    return rows


def claude_cached(account, now=None):
    """Last usage the CLI cached in .claude.json (no credentials there), for when /usage prints no rows.

    The CLI drops the rows when its usage request fails, e.g. a remembered 429 for the account.
    """
    home = Path(account["profile_dir"]) if not account.get("use_default") else Path.home()
    try:
        config = json.loads((home / ".claude.json").read_text())
        cache = config["cachedUsageUtilization"]
        if cache["accountUuid"] != config["oauthAccount"]["accountUuid"]:
            return []
        fetched = datetime.fromtimestamp(cache["fetchedAtMs"] / 1000).astimezone()
        limits = cache["utilization"]["limits"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    now = now or datetime.now().astimezone()
    rows = []
    for limit in limits if isinstance(limits, list) else []:
        try:
            moment = datetime.fromisoformat(limit["resets_at"]).astimezone()
            remaining = 100 - percent(limit["percent"])
        except (KeyError, TypeError, ValueError):
            continue
        if moment <= now:
            continue  # window already reset: the cached percent no longer applies
        kind = limit.get("kind")
        if kind == "session":
            label = "현재 세션"
        elif kind == "weekly_all":
            label = "이번 주 · 전체 모델"
        elif kind == "weekly_scoped":
            model = ((limit.get("scope") or {}).get("model") or {}).get("display_name")
            if not isinstance(model, str):
                continue
            label = "이번 주 · " + model[:40]
        else:
            continue
        rows.append({"label": label, "remaining": remaining,
                     "reset": moment.strftime("%m/%d %H:%M") + " 초기화 · " + fetched.strftime("%H:%M") + " 기준",
                     "reset_at": moment.timestamp()})
    return rows


def claude_max_dir():
    # Profile of the former fixed "Claude · Max 5x" card; offered once as an existing login.
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wego/check-ai-claude/max"


# Per agent: profile variable (None: only the default login), login command, default login marker.
KINDS = {
    "claude": {"name": "Claude", "binary": "claude", "env": "CLAUDE_CONFIG_DIR",
               "login": ["claude", "auth", "login"], "default": ".claude"},
    "codex": {"name": "Codex / ChatGPT", "binary": "codex", "env": "CODEX_HOME",
              "login": ["codex", "login"], "default": ".codex/auth.json"},
    "copilot": {"name": "GitHub Copilot", "binary": "gh", "env": "GH_CONFIG_DIR",
                "login": ["gh", "auth", "login", "--web", "--hostname", "github.com"], "default": None},
    # agy has no login subcommand or profile variable: the user types /login inside agy, and the
    # single login lives in the system keyring, so a login replaces this PC's account.
    "antigravity": {"name": "Antigravity", "binary": "agy", "env": None, "login": ["agy"],
                    "default": ".gemini/antigravity-cli", "switch": ".gemini/google_accounts.json"},
    # Cursor's profile folder is not configurable, so its login is the default one.
    "cursor": {"name": "Cursor", "binary": "cursor-agent", "env": None,
               "login": ["cursor-agent", "login"], "default": ".cursor"},
    # opencode keeps auth.json under $XDG_DATA_HOME/opencode.
    "opencode": {"name": "opencode", "binary": "opencode", "env": "XDG_DATA_HOME",
                 "login": ["opencode", "auth", "login"], "default": None},
}

# Inherited credentials would make every profile report the same account.
SCRUBBED = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
            "OPENAI_API_KEY", "CODEX_API_KEY", "GH_TOKEN", "GITHUB_TOKEN")


def environment(account):
    env = os.environ.copy()
    for key in SCRUBBED:
        env.pop(key, None)
    variable = KINDS[account["kind"]]["env"]
    if variable and not account.get("use_default"):
        env[variable] = str(account["profile_dir"])
    elif variable and variable != "XDG_DATA_HOME":
        # A default account is the default CLI login, even if the shell selected another profile.
        env.pop(variable, None)
    return env


def opencode_data(account):
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) if account.get("use_default") \
        else Path(account["profile_dir"])


def default_login(kind):
    """This PC's default login location for the agent, or None when there is none."""
    if kind == "copilot":
        path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gh/hosts.yml"
    elif kind == "opencode":
        path = opencode_data({"use_default": True}) / "opencode/auth.json"
    else:
        path = Path.home() / KINDS[kind]["default"]
    return path if path.exists() else None


def claude_profile_ok(auth, account):
    if not isinstance(auth, dict) or not auth.get("loggedIn") or auth.get("authMethod") != "claude.ai":
        return False
    if account.get("use_default"):
        return True
    actual = auth.get("configDirectory")
    return isinstance(actual, str) and Path(actual).resolve() == Path(account["profile_dir"]).resolve()


def active_login(kind):
    """The signed-in account marker for agents whose login replaces the PC's account, or None."""
    switch = KINDS[kind].get("switch")
    if not switch:
        return None
    try:
        active = json.loads((Path.home() / switch).read_text()).get("active")
    except (OSError, ValueError, AttributeError):
        return None
    return active if isinstance(active, str) and active else None


def logged_in(account):
    """Whether the account's own CLI profile holds a login. Runs status commands only."""
    kind, env = account["kind"], environment(account)
    try:
        if kind == "claude":
            auth = json.loads(output(["claude", "auth", "status", "--json"], env=env, timeout=10))
            return claude_profile_ok(auth, account)
        if kind == "codex":
            output(["codex", "login", "status"], env=env, timeout=10)
        elif kind == "copilot":
            output(["gh", "auth", "status", "--hostname", "github.com"], env=env, timeout=10)
        elif kind == "opencode":
            # Only the size is read: the file holds provider credentials.
            return (opencode_data(account) / "opencode/auth.json").stat().st_size > 2
        elif kind == "antigravity":
            return bool(active_login("antigravity"))
        elif kind == "cursor":
            # shortcut: wording unverified (cursor-agent not installed here); check once it is.
            return "not logged in" not in output(["cursor-agent", "status"], env=env, timeout=10).lower()
        else:
            return False
    except (QuotaError, OSError, ValueError):
        return False
    return True


def claude(account):
    isolated = not account.get("use_default")
    if isolated and not Path(account["profile_dir"]).is_dir():
        raise QuotaError("프로필 폴더 없음 · 계정을 다시 추가해 주세요")
    env = environment(account)
    login_message = "Claude 로그인 필요 · 계정을 다시 추가해 주세요" if isolated else "Claude 로그인 필요 · claude auth login 확인"
    try:
        auth = json.loads(output(["claude", "auth", "status", "--json"], env=env, timeout=5))
    except QuotaError:
        raise QuotaError(login_message) from None
    if not claude_profile_ok(auth, dict(account, use_default=True)):
        raise QuotaError(login_message)
    if not claude_profile_ok(auth, account):
        raise QuotaError("계정 분리 확인 실패 · CLI 설정 확인")
    data = json.loads(output(["claude", "--print", "/usage", "--output-format", "json",
                              "--no-session-persistence"], env=env, timeout=25))
    try:
        rows = parse_claude(data)
    except QuotaError:
        if not isinstance(data, dict) or data.get("is_error"):
            raise
        # A success reply without rows means the CLI's own usage request failed (usually 429).
        rows = claude_cached(account)
        if not rows:
            raise QuotaError("Claude 사용량 조회 제한(요청 과다 추정) · 자동 재시도") from None

    def text(key):
        value = auth.get(key)
        return value[:160] if isinstance(value, str) else ""
    return {"rows": rows, "plan": auth.get("subscriptionType"),
            "org": text("orgName"), "tier": text("planTier"), "account": text("email")}


# Agents without a readable quota (Cursor, opencode) have no fetcher.
PROVIDERS = {"codex": codex, "claude": claude, "antigravity": antigravity, "copilot": copilot}
