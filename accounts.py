"""User-managed agent accounts. Only records are stored; each login stays in its CLI profile."""
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal, Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QDialogButtonBox, QMessageBox, QCheckBox)

from providers import (KINDS, QuotaError, claude_max_dir, cli, codex_home, default_login, environment, executable,
                       logged_in, opencode_data)


PENDING = ".check-ai-pending"
LOGIN_TIMEOUT = 600
# Unregistered profile folders older than this are leftovers of an abandoned or removed login.
ORPHAN_AGE = 15 * 60
# Former fixed cards whose billing/notification settings carry over to the matching account.
LEGACY_KEYS = ("claude", "codex", "copilot", "antigravity")


def accounts_root():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wego/check-ai/accounts"


def logo_asset(kind, dark=False):
    """Bundled logo for an agent kind; dark surfaces use the white line variant when one exists."""
    folder = Path(__file__).parent / "assets"
    if kind == "antigravity":
        return folder / "antigravity.png"
    white = folder / f"{kind}-white.svg"
    return white if dark and white.exists() else folder / f"{kind}.svg"


def valid(account):
    return (isinstance(account, dict) and isinstance(account.get("id"), str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", account["id"]) is not None
            and account.get("kind") in KINDS and isinstance(account.get("label"), str)
            and isinstance(account.get("use_default"), bool) and isinstance(account.get("profile_dir"), str)
            and (account["use_default"] or bool(account["profile_dir"])))


def load(settings):
    try:
        records = json.loads(settings.value("accounts", "[]"))
    except (TypeError, ValueError):
        return []
    accounts, seen = [], set()
    for record in records if isinstance(records, list) else []:
        if valid(record) and record["id"] not in seen:
            seen.add(record["id"])
            accounts.append({key: record[key] for key in ("id", "kind", "label", "profile_dir", "use_default")})
    return accounts


def save(settings, accounts):
    settings.setValue("accounts", json.dumps(accounts, ensure_ascii=False))


def new_id():
    return uuid.uuid4().hex[:12]


def installed(kind):
    try:
        executable(KINDS[kind]["binary"])
        return True
    except QuotaError:
        return False


def create_profile(account_id):
    root = accounts_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / account_id
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    # Marks a login that has not finished; cleanup_pending removes such folders.
    (path / PENDING).touch()
    return path


def deletable(account):
    """Only isolated profiles created by this app may be deleted, never a default or linked folder."""
    if account.get("use_default") or not account.get("profile_dir"):
        return False
    path = Path(account["profile_dir"])
    return not path.is_symlink() and path.resolve().parent == accounts_root().resolve()


def remove_profile(path):
    if not deletable({"profile_dir": str(path)}):
        return False
    shutil.rmtree(path, ignore_errors=False)
    return True


def cleanup_pending(accounts=()):
    """Removes unfinished logins, and folders no account uses that a terminal login may have recreated."""
    try:
        folders = list(accounts_root().iterdir())
    except OSError:
        return
    used = {Path(a["profile_dir"]).resolve() for a in accounts if not a["use_default"]}
    for folder in folders:
        try:
            if (folder / PENDING).is_file() or (
                    folder.resolve() not in used and time.time() - folder.lstat().st_mtime > ORPHAN_AGE):
                remove_profile(folder)
        except OSError:
            pass


def candidates(accounts, kind):
    """Existing logins on this PC that can be linked without logging in again."""
    found = []
    if default_login(kind) and not any(a["kind"] == kind and a["use_default"] for a in accounts):
        found.append({"kind": kind, "label": "", "profile_dir": "", "use_default": True,
                      "legacy": kind if kind in LEGACY_KEYS else None, "text": "이 PC 기존 로그인 사용"})
    old = claude_max_dir()
    if kind == "claude" and old.is_dir() and not any(
            not a["use_default"] and Path(a["profile_dir"]).resolve() == old.resolve() for a in accounts):
        found.append({"kind": "claude", "label": "Claude · Max 5x", "profile_dir": str(old), "use_default": False,
                      "legacy": "claude_max", "text": "기존 Max 5x 프로필 사용"})
    return found


def migrate(settings, legacy, account_id):
    if not legacy:
        return
    for old, new in ((f"billing/{legacy}", f"billing/{account_id}"),
                     *((f"notify/{legacy}/{t}", f"notify/{account_id}/{t}") for t in (20, 10))):
        if settings.contains(old) and not settings.contains(new):
            settings.setValue(new, settings.value(old))


def link(settings, accounts, candidate, label=""):
    account = {"id": new_id(), "kind": candidate["kind"], "label": candidate["label"] or label,
               "profile_dir": candidate["profile_dir"], "use_default": candidate["use_default"]}
    migrate(settings, candidate.get("legacy"), account["id"])
    accounts.append(account)
    save(settings, accounts)
    return account


def unregister(settings, accounts, account_id, delete_profile=False):
    account = next(a for a in accounts if a["id"] == account_id)
    accounts.remove(account)
    save(settings, accounts)
    for key in ("billing/", "price/", "notify/"):
        settings.remove(key + account_id)
    if delete_profile:
        remove_profile(account["profile_dir"])


def terminal_command(title, args):
    try:
        command = cli(args)
    except QuotaError:
        raise QuotaError(f"{args[0]} 명령 없음 · CLI 설치 확인") from None
    for name, options in (("terminator", ["--no-dbus", "--title", title, "-x"]),
                          ("x-terminal-emulator", ["-e"]),
                          ("gnome-terminal", ["--title", title, "--"])):
        try:
            return executable(name), options + command
        except QuotaError:
            continue
    raise QuotaError("터미널 프로그램 없음 · terminator 설치 확인")


def login_file(account):
    """The file the agent writes on a login, or None when unknown."""
    kind, profile = account["kind"], None if account["use_default"] else Path(account["profile_dir"])
    if kind == "claude":
        return (profile or Path.home() / ".claude") / ".credentials.json"
    if kind == "codex":
        return codex_home(account) / "auth.json"
    if kind == "copilot":
        return (profile or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gh") / "hosts.yml"
    if kind == "opencode":
        return opencode_data(account) / "opencode/auth.json"
    switch = KINDS[kind].get("switch")
    return Path.home() / switch if switch else None


def file_stamp(path):
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_ino, info.st_mtime_ns, info.st_size


class Login(QObject):
    """Runs the agent's own login in a terminal and waits for its status to report a login.

    With `account`, logs in again to that registered account's own profile (or default login).
    """
    finished = Signal(object)

    def __init__(self, kind, parent=None, account=None):
        super().__init__(parent)
        self.existing = account is not None
        self.account = dict(account) if self.existing else {
            "id": new_id(), "kind": kind, "label": "", "profile_dir": "", "use_default": KINDS[kind]["env"] is None}
        self.ok = False
        # A PC-wide login (agy) or a re-login is already logged in: wait until the login file is rewritten.
        self.switch = bool(KINDS[kind].get("switch"))
        self.watch = login_file(self.account) if self.switch or self.existing else None
        self.before = file_stamp(self.watch) if self.watch else None
        self.check = None
        self.deadline = 0
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.tick)

    def start(self):
        kind = self.account["kind"]
        if not self.account["use_default"] and not self.existing:
            self.account["profile_dir"] = str(create_profile(self.account["id"]))
        try:
            program, arguments = terminal_command(KINDS[kind]["name"] + " 로그인", KINDS[kind]["login"])
            env = QProcessEnvironment()
            for name, value in environment(self.account).items():
                env.insert(name, value)
            process = QProcess(self)
            process.setProcessEnvironment(env)
            process.setWorkingDirectory(self.account["profile_dir"] or str(Path.home()))
            process.setProgram(program)
            process.setArguments(arguments)
            result = process.startDetached()
            process.deleteLater()
            if not (result[0] if isinstance(result, tuple) else result):
                raise OSError("terminal launch failed")
        except (OSError, QuotaError):
            self.discard()
            raise
        self.deadline = time.monotonic() + LOGIN_TIMEOUT
        self.timer.start()

    def tick(self):
        if self.check is not None and self.check.is_alive():
            return
        if self.ok:
            return self.done(True)
        if time.monotonic() > self.deadline:
            return self.done(False)
        # Status commands can take seconds; keep them off the UI thread.
        self.check = threading.Thread(target=self.poll, daemon=True)
        self.check.start()

    def poll(self):
        self.ok = logged_in(self.account) and (self.watch is None or file_stamp(self.watch) != self.before)

    def done(self, ok):
        self.timer.stop()
        if ok and self.account["profile_dir"]:
            (Path(self.account["profile_dir"]) / PENDING).unlink(missing_ok=True)
        elif not ok:
            self.discard()
        self.finished.emit(self.account if ok else None)

    def cancel(self):
        if self.timer.isActive():
            self.timer.stop()
            self.discard()

    def discard(self):
        # A re-login never deletes the registered account's profile.
        if self.account["profile_dir"] and not self.existing:
            try:
                remove_profile(self.account["profile_dir"])
            except OSError:
                pass


class AddAccountDialog(QDialog):
    """Pick an agent, then log in to a new isolated profile or link this PC's existing login."""

    def __init__(self, parent, accounts):
        super().__init__(parent)
        self.accounts = accounts
        self.choice = None
        self.setWindowTitle("에이전트 추가")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(12)
        heading = QLabel("에이전트 추가")
        heading.setObjectName("eventHeading")
        layout.addWidget(heading)
        hint = QLabel("에이전트를 고른 뒤 로그인 방법을 선택하세요. 토큰은 이 앱에 저장하지 않습니다.")
        hint.setObjectName("caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.kinds = QListWidget()
        self.kinds.setObjectName("agentKinds")
        self.kinds.setAccessibleName("에이전트 목록")
        self.kinds.setIconSize(QSize(20, 20))
        dark = getattr(parent, "theme", "dark") == "dark"
        for kind, info in KINDS.items():
            ready = installed(kind)
            item = QListWidgetItem(QIcon(str(logo_asset(kind, dark))), info["name"] + ("" if ready else " · 설치 필요"))
            item.setData(Qt.UserRole, kind)
            item.setSizeHint(QSize(0, 34))
            if not ready:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
            self.kinds.addItem(item)
        self.kinds.setFixedHeight(34 * len(KINDS) + 14)
        layout.addWidget(self.kinds)
        self.login_button = QPushButton("새 계정 로그인")
        self.login_button.setObjectName("primaryButton")
        self.login_button.setMinimumHeight(36)
        self.login_button.setAccessibleName("새 계정 로그인")
        self.login_button.clicked.connect(lambda: self.choose(("login", self.kind())))
        layout.addWidget(self.login_button)
        self.login_hint = QLabel("")
        self.login_hint.setObjectName("caption")
        self.login_hint.setWordWrap(True)
        layout.addWidget(self.login_hint)
        self.link_box = QVBoxLayout()
        self.link_box.setSpacing(6)
        layout.addLayout(self.link_box)
        self.link_hint = QLabel("")
        self.link_hint.setObjectName("caption")
        self.link_hint.setWordWrap(True)
        layout.addWidget(self.link_hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("닫기")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.link_buttons = []
        self.kinds.currentRowChanged.connect(self.select)
        first = next((i for i in range(self.kinds.count()) if self.kinds.item(i).flags() & Qt.ItemIsEnabled), -1)
        self.kinds.setCurrentRow(first)
        self.select(first)

    def kind(self):
        item = self.kinds.currentItem()
        return item.data(Qt.UserRole) if item and item.flags() & Qt.ItemIsEnabled else None

    def select(self, _row=None):
        kind = self.kind()
        for button in self.link_buttons:
            button.setParent(None)
            button.deleteLater()
        self.link_buttons = []
        if kind is None:
            self.login_button.setEnabled(False)
            self.login_hint.setText("설치된 에이전트를 선택하세요.")
            self.link_hint.setText("")
            return
        info = KINDS[kind]
        default_taken = any(a["kind"] == kind and a["use_default"] for a in self.accounts)
        self.login_button.setText("새 계정 로그인")
        if not info["login"]:
            can_login, text = False, "이 에이전트는 별도 로그인을 지원하지 않습니다. 에이전트에서 로그인한 뒤 기존 로그인을 사용하세요."
        elif info.get("switch"):
            can_login = True
            if default_taken:
                self.login_button.setText(info["name"] + " 계정 바꾸기")
            text = (f"터미널에서 {info['binary']}가 열리면 /login을 입력하세요. 계정을 분리할 수 없어 "
                    "이 PC의 " + info["name"] + " 계정이 바뀝니다(추가가 아님).")
        elif info["env"] is None:
            can_login = not default_taken
            text = ("이 PC 기본 로그인이 이미 추가되어 있습니다." if default_taken else
                    "계정 분리를 지원하지 않아 이 PC 기본 로그인으로 로그인합니다.")
        else:
            can_login, text = True, "별도 프로필 폴더에 로그인합니다. 터미널에서 로그인을 마치면 카드가 추가됩니다."
        self.login_button.setEnabled(can_login)
        self.login_hint.setText(text)
        for candidate in candidates(self.accounts, kind):
            button = QPushButton(candidate["text"])
            button.setAccessibleName(info["name"] + " " + candidate["text"])
            button.setToolTip(candidate["profile_dir"] or "다시 로그인하지 않고 이 PC의 기본 로그인을 연결합니다")
            button.clicked.connect(lambda checked=False, choice=candidate: self.choose(("link", choice)))
            self.link_box.addWidget(button)
            self.link_buttons.append(button)
        self.link_hint.setText("" if self.link_buttons else "이 PC 기존 로그인이 이미 추가되어 있습니다."
                               if default_taken else "이 PC에서 기존 로그인을 찾지 못했습니다.")

    def choose(self, choice):
        self.choice = choice
        self.accept()


def confirm_remove(parent, title, account):
    """Returns (remove, delete_profile). Deleting is offered only for this app's own profiles."""
    box = QMessageBox(QMessageBox.Question, "계정 제거", f"{title} 계정을 목록에서 제거할까요?", parent=parent)
    box.setInformativeText("이 PC의 로그인 정보와 프로필 폴더는 그대로 남습니다.")
    remove = box.addButton("제거", QMessageBox.AcceptRole)
    cancel = box.addButton("취소", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    check = None
    if deletable(account):
        check = QCheckBox("프로필 폴더도 삭제")
        check.setToolTip(account["profile_dir"])
        box.setCheckBox(check)
    box.exec()
    return box.clickedButton() is remove, bool(check and check.isChecked())
