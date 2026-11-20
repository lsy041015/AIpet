"""Account registry, login isolation and billing helpers. No CLI login is ever launched."""
import base64
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

import accounts
import providers
from billing import BillingDialog, list_price, parse_price, price_summary
from widget import Widget

FETCH_ACCOUNT = Widget.fetch_account


def record(kind, account_id="a1", profile="", label=""):
    return {"id": account_id, "kind": kind, "label": label, "profile_dir": profile, "use_default": not profile}


def fake_jwt(claims):
    encode = lambda data: base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{encode({'alg': 'none'})}.{encode(claims)}.signature"


class Isolated(unittest.TestCase):
    """Temporary HOME, XDG folders and QSettings file for every test."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / "home"
        self.home.mkdir()
        environment = patch.dict(os.environ, {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.root / "config"),
                                              "XDG_DATA_HOME": str(self.root / "data")})
        environment.start()
        self.addCleanup(environment.stop)
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.IniFormat)


class RegistryTests(Isolated):
    def test_records_persist_and_invalid_entries_are_dropped(self):
        saved = [record("claude"), record("codex", "b2", str(self.root / "p"), "업무")]
        accounts.save(self.settings, saved)
        self.assertEqual(accounts.load(self.settings), saved)
        self.settings.setValue("accounts", json.dumps(saved + [
            record("claude"),  # duplicate id
            dict(record("claude", "../escape")), dict(record("unknown", "c3")),
            {"id": "d4", "kind": "codex", "label": "", "profile_dir": "", "use_default": False}, "junk"]))
        self.assertEqual(accounts.load(self.settings), saved)
        self.settings.setValue("accounts", "not json")
        self.assertEqual(accounts.load(self.settings), [])

    def test_each_kind_isolates_its_profile_and_scrubs_inherited_credentials(self):
        inherited = {"CLAUDE_CONFIG_DIR": "/other", "CODEX_HOME": "/other", "GH_CONFIG_DIR": "/other",
                     "GH_TOKEN": "x", "OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "x", "CLAUDE_CODE_OAUTH_TOKEN": "x"}
        with patch.dict(os.environ, inherited):
            for kind, variable in (("claude", "CLAUDE_CONFIG_DIR"), ("codex", "CODEX_HOME"),
                                   ("copilot", "GH_CONFIG_DIR"), ("opencode", "XDG_DATA_HOME")):
                with self.subTest(kind=kind):
                    first = providers.environment(record(kind, "a", "/profiles/a"))
                    second = providers.environment(record(kind, "b", "/profiles/b"))
                    self.assertEqual((first[variable], second[variable]), ("/profiles/a", "/profiles/b"))
                    default = providers.environment(record(kind))
                    if variable == "XDG_DATA_HOME":
                        self.assertEqual(default[variable], str(self.root / "data"))
                    else:
                        self.assertNotIn(variable, default)
                    for secret in ("GH_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
                        self.assertNotIn(secret, first)
            self.assertEqual(os.environ["CLAUDE_CONFIG_DIR"], "/other")
        # Without a profile variable only the default login exists.
        self.assertIsNone(providers.KINDS["antigravity"]["env"])
        self.assertEqual(providers.KINDS["antigravity"]["login"], ["agy"])

    def test_fetchers_run_with_the_account_environment(self):
        account = record("copilot", "a", str(self.root / "gh-a"))
        with patch("providers.output", return_value=json.dumps({"copilot_plan": "individual", "login": "octo",
                                                               "quota_snapshots": {"chat": {"unlimited": True}}})) as output:
            snapshot = providers.copilot(account)
        self.assertEqual(output.call_args.kwargs["env"]["GH_CONFIG_DIR"], str(self.root / "gh-a"))
        self.assertEqual((snapshot["plan"], snapshot["account"]), ("individual", "octo"))
        self.assertNotIn("cursor", providers.PROVIDERS)
        self.assertNotIn("opencode", providers.PROVIDERS)

    def test_login_status_checks_the_account_profile_only(self):
        profile = self.root / "c"
        profile.mkdir()
        with patch("providers.output", return_value=json.dumps(
                {"loggedIn": True, "authMethod": "claude.ai", "configDirectory": str(profile)})) as output:
            self.assertTrue(providers.logged_in(record("claude", "a", str(profile))))
            self.assertEqual(output.call_args.args[0], ["claude", "auth", "status", "--json"])
            self.assertFalse(providers.logged_in(record("claude", "a", str(self.root / "other"))))
        with patch("providers.output", side_effect=providers.QuotaError("x")):
            self.assertFalse(providers.logged_in(record("codex", "a", str(profile))))
        with patch("providers.output", return_value="Logged in") as output:
            self.assertTrue(providers.logged_in(record("copilot", "a", str(profile))))
            self.assertEqual(output.call_args.args[0][:3], ["gh", "auth", "status"])
        auth = profile / "opencode/auth.json"
        self.assertFalse(providers.logged_in(record("opencode", "a", str(profile))))
        auth.parent.mkdir()
        auth.write_text("{}")
        self.assertFalse(providers.logged_in(record("opencode", "a", str(profile))))
        auth.write_text('{"provider": {}}')
        self.assertTrue(providers.logged_in(record("opencode", "a", str(profile))))


class LinkTests(Isolated):
    def test_existing_logins_are_offered_once_and_billing_carries_over(self):
        self.assertEqual(accounts.candidates([], "claude"), [])
        (self.home / ".claude").mkdir()
        old = providers.claude_max_dir()
        old.mkdir(parents=True)
        (self.root / "config/gh").mkdir(parents=True)
        (self.root / "config/gh/hosts.yml").write_text("github.com: {}\n")
        self.settings.setValue("billing/claude_max", json.dumps({"date": "2026-12-01", "period": "monthly"}))
        self.settings.setValue("notify/claude_max/20", False)
        self.settings.setValue("billing/claude", json.dumps({"date": "2026-12-05", "period": "monthly"}))
        registered = []
        found = accounts.candidates(registered, "claude")
        self.assertEqual([(c["use_default"], c["legacy"]) for c in found], [(True, "claude"), (False, "claude_max")])
        self.assertEqual(len(accounts.candidates(registered, "copilot")), 1)
        self.assertEqual(accounts.candidates(registered, "antigravity"), [])
        max_account = accounts.link(self.settings, registered, found[1])
        self.assertEqual((max_account["label"], max_account["profile_dir"]), ("Claude · Max 5x", str(old)))
        self.assertEqual(json.loads(self.settings.value("billing/" + max_account["id"]))["date"], "2026-12-01")
        self.assertFalse(self.settings.value(f"notify/{max_account['id']}/20", True, type=bool))
        # A setting the new account already has is never overwritten.
        found = accounts.candidates(registered, "claude")
        self.assertEqual([c["use_default"] for c in found], [True])
        self.settings.setValue("billing/fixed", "keep")
        with patch("accounts.new_id", return_value="fixed"):
            accounts.link(self.settings, registered, found[0])
        self.assertEqual(self.settings.value("billing/fixed"), "keep")
        self.assertEqual(accounts.candidates(registered, "claude"), [])
        self.assertEqual([a["id"] for a in accounts.load(self.settings)], [max_account["id"], "fixed"])
        # Linked folders outside the app's own profile root are never deletable.
        self.assertFalse(accounts.deletable(max_account))
        self.assertFalse(accounts.deletable(registered[1]))


class LoginTests(Isolated):
    def start(self, kind="claude", started=True):
        login = accounts.Login(kind)
        process = MagicMock()
        process.return_value.startDetached.return_value = started
        with patch("accounts.QProcess", process), \
             patch("accounts.terminal_command", return_value=("/usr/bin/terminator", ["-x", "login"])) as command:
            login.start()
        return login, process, command

    def test_login_uses_an_isolated_pending_profile_and_registers_on_success(self):
        login, process, command = self.start()
        profile = Path(login.account["profile_dir"])
        self.assertEqual(profile.parent, accounts.accounts_root())
        self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o700)
        self.assertTrue((profile / accounts.PENDING).is_file())
        self.assertEqual(command.call_args.args[1], ["claude", "auth", "login"])
        env = process.return_value.setProcessEnvironment.call_args.args[0]
        self.assertEqual(env.value("CLAUDE_CONFIG_DIR"), str(profile))
        results = []
        login.finished.connect(results.append)
        with patch("accounts.logged_in", return_value=False):
            login.tick()
            login.check.join()
            login.tick()
            login.check.join()
        self.assertEqual(results, [])
        with patch("accounts.logged_in", return_value=True):
            login.tick()
            login.check.join()
            login.tick()
        self.assertEqual(results, [login.account])
        self.assertFalse((profile / accounts.PENDING).exists())
        self.assertFalse(login.timer.isActive())

    def test_unfinished_login_registers_nothing_and_removes_its_profile(self):
        login, _, _ = self.start("codex")
        profile = Path(login.account["profile_dir"])
        results = []
        login.finished.connect(results.append)
        login.deadline = 0
        login.tick()
        self.assertEqual(results, [None])
        self.assertFalse(profile.exists())
        cancelled, _, _ = self.start("copilot")
        cancelled.cancel()
        self.assertFalse(Path(cancelled.account["profile_dir"]).exists())
        with self.assertRaises(OSError):
            self.start("opencode", started=False)
        self.assertEqual(list(accounts.accounts_root().iterdir()), [])

    def test_startup_cleanup_keeps_registered_and_kept_profiles(self):
        pending = accounts.create_profile("pending1")
        kept = accounts.create_profile("kept1")
        (kept / accounts.PENDING).unlink()
        accounts.cleanup_pending()
        self.assertFalse(pending.exists())
        self.assertTrue(kept.is_dir())

    def test_agents_without_profiles_log_in_to_the_default(self):
        login, process, _ = self.start("cursor")
        self.assertTrue(login.account["use_default"])
        self.assertEqual(login.account["profile_dir"], "")
        self.assertFalse(accounts.accounts_root().exists())

    def test_terminal_command_prefers_terminator_and_falls_back(self):
        def found(*names):
            def executable(name):
                if name in names or name in ("rtk", "claude"):
                    return "/bin/" + name
                raise providers.QuotaError("missing")
            return executable
        with patch("accounts.executable", found("terminator")), patch("providers.executable", found()):
            self.assertEqual(accounts.terminal_command("T", ["claude", "auth", "login"]),
                             ("/bin/terminator", ["--no-dbus", "--title", "T", "-x", "/bin/rtk", "proxy", "/bin/claude", "auth", "login"]))
        with patch("accounts.executable", found("gnome-terminal")):
            self.assertEqual(accounts.terminal_command("T", ["claude"])[1][:3], ["--title", "T", "--"])
        with patch("accounts.executable", found()), self.assertRaisesRegex(providers.QuotaError, "터미널"):
            accounts.terminal_command("T", ["claude"])

    def test_rtk_is_optional_and_missing_cli_is_named(self):
        def found(*names):
            def executable(name):
                if name in names:
                    return "/bin/" + name
                raise providers.QuotaError(name + " 설치·로그인 필요")
            return executable
        with patch("accounts.executable", found("terminator")), patch("providers.executable", found("claude")):
            self.assertEqual(accounts.terminal_command("T", ["claude", "auth", "login"])[1][-3:], ["/bin/claude", "auth", "login"])
            self.assertEqual(providers.cli(["claude", "x"]), ["/bin/claude", "x"])
        with patch("providers.executable", found("claude", "rtk")):
            self.assertEqual(providers.cli(["claude"]), ["/bin/rtk", "proxy", "/bin/claude"])
        with patch("accounts.executable", found("terminator")), patch("providers.executable", found("rtk")), \
             self.assertRaisesRegex(providers.QuotaError, "claude 명령 없음"):
            accounts.terminal_command("T", ["claude"])


class AgySwitchTests(Isolated):
    def accounts_file(self, active):
        path = self.home / ".gemini/google_accounts.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"active": active, "old": []}))

    def test_agy_login_waits_for_the_active_account_to_change(self):
        self.accounts_file("first@example.test")
        login = accounts.Login("antigravity")
        self.assertTrue(login.account["use_default"])
        self.assertEqual(providers.KINDS["antigravity"]["login"], ["agy"])
        login.poll()
        self.assertFalse(login.ok, "an existing login is not a new sign-in")
        self.accounts_file("second@example.test")
        login.poll()
        self.assertTrue(login.ok)
        self.accounts_file("")
        self.assertFalse(providers.logged_in(record("antigravity")))

    def test_dialog_offers_switch_when_agy_is_already_linked(self):
        with patch("accounts.installed", return_value=True):
            dialog = accounts.AddAccountDialog(None, [record("antigravity")])
        self.addCleanup(dialog.deleteLater)
        items = {dialog.kinds.item(i).data(Qt.UserRole): i for i in range(dialog.kinds.count())}
        dialog.kinds.setCurrentRow(items["antigravity"])
        self.assertTrue(dialog.login_button.isEnabled())
        self.assertEqual(dialog.login_button.text(), "Antigravity 계정 바꾸기")
        self.assertIn("추가가 아님", dialog.login_hint.text())
        dialog.kinds.setCurrentRow(items["claude"])
        self.assertEqual(dialog.login_button.text(), "새 계정 로그인")


class BillingInfoTests(Isolated):
    def test_codex_plan_and_renewal_come_from_unverified_jwt_claims(self):
        home = self.root / "codex"
        home.mkdir()
        claims = {"https://api.openai.com/auth": {"chatgpt_plan_type": "plus",
                                                  "chatgpt_subscription_active_until": "2026-11-30T15:12:37+00:00"}}
        (home / "auth.json").write_text(json.dumps({"tokens": {"id_token": fake_jwt(claims), "access_token": "secret"}}))
        expected = datetime(2026, 11, 30, 15, 12, 37, tzinfo=timezone.utc).astimezone().date().isoformat()
        self.assertEqual(providers.codex_subscription(home), {"plan": "plus", "renews": expected})
        self.assertEqual(providers.jwt_claims(fake_jwt({"a": 1})), {"a": 1})
        for broken in ("", "abc", "a.!!!.c", None, fake_jwt(["list"])):
            self.assertEqual(providers.jwt_claims(broken), {})
        self.assertEqual(providers.codex_subscription(self.root / "missing"), {})
        (home / "auth.json").write_text(json.dumps({"tokens": {"id_token": fake_jwt({"https://api.openai.com/auth": {}})}}))
        self.assertEqual(providers.codex_subscription(home), {"plan": None, "renews": None})
        self.assertEqual(providers.codex_home(record("codex")), self.home / ".codex")

    def test_list_prices_and_manual_override(self):
        cases = {("claude", "pro", ""): 20, ("claude", "max", "max_5x"): 100, ("claude", "max", "default_claude_max_20x"): 200,
                 ("claude", "max", ""): None, ("claude", "team", "other"): None, ("claude", "team", "team_premium"): 150,
                 ("codex", "plus", ""): 20, ("codex", "pro", ""): 200, ("codex", "team", ""): 30, ("codex", "prolite", ""): None,
                 ("copilot", "individual", ""): 10, ("copilot", "individual_pro", ""): 39, ("copilot", "business", ""): 19,
                 ("copilot", None, ""): None, ("antigravity", "pro", ""): None}
        for (kind, plan, tier), price in cases.items():
            self.assertEqual(list_price(kind, plan, tier), price, (kind, plan, tier))
        self.assertEqual([parse_price(v) for v in ("$25", "19.5", " 0 ", "-1", "nan", "abc", "")], [25, 19.5, 0, None, None, None, None])
        self.assertEqual(price_summary(None, 20), "$20/월 · 정가")
        self.assertEqual(price_summary(25, 20), "$25/월 · 직접 입력")
        self.assertEqual(price_summary(None, None), "요금 미확인")
        dialog = BillingDialog(None, "Claude", None, 25, 20)
        self.assertEqual(dialog.price(), 25)
        dialog.price_input.clear()
        self.assertIsNone(dialog.price())
        self.assertIn("$20", dialog.price_input.placeholderText())
        dialog.deleteLater()


class WidgetAccountTests(Isolated):
    def setUp(self):
        super().setUp()
        for item in (patch("widget.QSettings", return_value=self.settings), patch.object(Widget, "refresh"),
                     patch.object(Widget, "refresh_activity"), patch("calendar_panel.CalendarPanel.refresh"),
                     patch.object(Widget, "fetch_account")):
            item.start()
            self.addCleanup(item.stop)

    def window(self, kind=Widget):
        window = kind()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        return window

    def test_first_run_shows_only_the_empty_state(self):
        (self.home / ".claude").mkdir()
        window = self.window()
        self.assertEqual((window.accounts, window.cards, window.quota_rows), ([], {}, {}))
        self.assertFalse(window.empty_state.isHidden())
        self.assertIsNone(self.settings.value("accounts"))
        self.assertIn("등록된 에이전트 없음", window.pet_summary())

    def test_switched_agy_login_refreshes_the_existing_card(self):
        self.settings.setValue("accounts", json.dumps([record("antigravity", "agy1")]))
        window = self.window()
        login = MagicMock()
        window.logins.append(login)
        with patch.object(Widget, "account_added") as added:
            window.login_finished(login, {"id": "new1", "kind": "antigravity", "label": "",
                                          "profile_dir": "", "use_default": True})
        added.assert_called_once_with(window.accounts[0])
        self.assertEqual([a["id"] for a in accounts.load(self.settings)], ["agy1"])

    def test_link_login_and_remove_update_cards(self):
        (self.home / ".claude").mkdir()
        self.settings.setValue("billing/claude", json.dumps({"date": "2099-01-15", "period": "monthly"}))
        window = self.window()
        dialog = MagicMock()
        dialog.Accepted = 1
        dialog.return_value.exec.return_value = 1
        dialog.return_value.choice = ("link", accounts.candidates([], "claude")[0])
        with patch("widget.AddAccountDialog", dialog):
            window.add_account()
        (linked,) = window.accounts
        window.fetch_account.assert_called_with(linked)
        self.assertTrue(window.empty_state.isHidden())
        self.assertIn("01.15", window.cards[linked["id"]].billing.text())
        self.assertEqual(len(window.quota_alert_menus), 1)

        login = MagicMock()
        dialog.return_value.choice = ("login", "claude")
        with patch("widget.AddAccountDialog", dialog), patch("widget.Login", login):
            window.add_account()
        login.assert_called_once_with("claude", window)
        login.return_value.start.assert_called_once()
        self.assertEqual(len(window.cards), 1)
        profile = accounts.create_profile("second1")
        (profile / accounts.PENDING).unlink()
        window.login_finished(login.return_value, {"id": "second1", "kind": "claude", "label": "",
                                                   "profile_dir": str(profile), "use_default": False})
        self.assertEqual(list(window.cards), [linked["id"], "second1"])
        self.assertEqual(window.quota_rows["second1"].title, "Claude Code 2")
        self.assertEqual([a["id"] for a in accounts.load(self.settings)], [linked["id"], "second1"])

        with patch("widget.confirm_remove", return_value=(False, True)):
            window.remove_account("second1")
        self.assertIn("second1", window.cards)
        window.save_billing("second1", {"date": "2099-01-01", "period": "monthly"})
        with patch("widget.confirm_remove", return_value=(True, False)) as confirm:
            window.remove_account("second1")
        self.assertEqual(confirm.call_args.args[1], "Claude Code 2")
        self.assertNotIn("second1", window.cards)
        self.assertTrue(profile.is_dir())
        self.assertFalse(self.settings.contains("billing/second1"))
        self.assertEqual(len(window.quota_alert_menus), 1)
        # A late result for a removed account is ignored.
        window.received("second1", {"rows": [{"label": "주간", "remaining": 5, "reset": ""}]}, "")

        window.accounts.append({"id": "third1", "kind": "claude", "label": "", "profile_dir": str(profile), "use_default": False})
        window.sync_accounts()
        with patch("widget.confirm_remove", return_value=(True, True)):
            window.remove_account("third1")
        self.assertFalse(profile.exists())
        with patch("widget.confirm_remove", return_value=(True, True)):
            window.remove_account(linked["id"])
        self.assertTrue((self.home / ".claude").is_dir())
        self.assertFalse(window.empty_state.isHidden())

    def test_unsupported_agents_show_no_quota_and_are_not_fetched(self):
        self.settings.setValue("accounts", json.dumps([record("opencode")]))
        window = self.window()
        FETCH_ACCOUNT(window, window.accounts[0])
        card = window.cards["a1"]
        self.assertEqual(window.jobs, [])
        self.assertIn("한도를 제공하지 않습니다", card.reset.text())
        self.assertTrue(card.bar.isHidden())
        self.assertEqual(window.pet_quotas["a1"], "한도 정보 없음")
        self.assertEqual(window.quota_alert_menus, [])

    def test_card_shows_plan_price_payment_and_nearest_reset(self):
        self.settings.setValue("accounts", json.dumps([record("codex", "cx"), record("claude", "cl")]))
        window = self.window()
        soon = datetime.now().timestamp() + 2 * 3600
        window.received("cx", {"rows": [{"label": "Codex · 주간", "remaining": 50, "reset": "주간", "reset_at": soon + 86400},
                                        {"label": "Codex · 5시간", "remaining": 90, "reset": "곧", "reset_at": soon}],
                               "plan": "plus", "renews": "2099-02-03"}, "")
        card = window.cards["cx"]
        self.assertIn("2099.02.03 · D-", card.billing.text())
        self.assertEqual(card.price.text(), "$20/월 · 정가")
        self.assertIn("Codex · 5시간 · 2시간 후", card.next_reset.text())
        self.assertFalse(card.next_reset.isHidden())
        window.received("cl", {"rows": [{"label": "현재 세션", "remaining": 50, "reset": ""}], "plan": "max",
                               "tier": "max_20x", "org": "Home", "account": "me@example.test"}, "")
        self.assertEqual(window.cards["cl"].plan_badge.text(), "Max 20x")
        self.assertEqual(window.cards["cl"].plan.text(), "me@example.test · Home")
        self.assertEqual(window.plan_text("cl"), "플랜 · Max 20x · Home")
        self.assertIn("$200/월", window.cards["cl"].price.text())

        def save(dialog):
            dialog.price_input.setText("180")
            return BillingDialog.Accepted
        with patch.object(BillingDialog, "exec", save):
            window.edit_billing("cl")
        self.assertEqual(window.cards["cl"].price.text(), "$180/월 · 직접 입력")
        self.assertEqual(self.settings.value("price/cl"), "180")
        with patch.object(BillingDialog, "exec", lambda dialog: (dialog.remove(), BillingDialog.Accepted)[1]):
            window.edit_billing("cl")
        self.assertEqual(window.cards["cl"].price.text(), "$200/월 · 정가")

    def test_desktop_pages_follow_account_changes(self):
        from desktop import DesktopWindow
        window = self.window(DesktopWindow)
        self.assertFalse(window.overview_empty.isHidden())
        self.assertEqual(window.billing_cards, {})
        window.accounts.append(record("codex", "cx"))
        window.sync_accounts()
        self.assertEqual((list(window.overview_rows), list(window.billing_cards)), (["cx"], ["cx"]))
        self.assertIs(window.cards["cx"].parent(), window.quota_content)
        self.assertTrue(window.overview_empty.isHidden())
        window.received("cx", {"rows": [{"label": "Codex · 주간", "remaining": 30, "reset": ""}], "plan": "pro"}, "")
        self.assertEqual(window.billing_prices["cx"].text(), "$200/월 · 정가")
        self.assertEqual(window.metric_notes["lowest"].text(), "GPT / Codex · Codex · 주간")
        with patch("widget.confirm_remove", return_value=(True, False)):
            window.remove_account("cx")
        self.assertEqual((window.overview_rows, window.billing_cards, window.cards), ({}, {}, {}))
        self.assertEqual(window.metrics["lowest"].text(), "—")


class DialogTests(Isolated):
    def test_add_dialog_offers_login_and_existing_logins_per_agent(self):
        (self.home / ".claude").mkdir()
        with patch("accounts.installed", side_effect=lambda kind: kind != "cursor"):
            dialog = accounts.AddAccountDialog(None, [])
        items = {dialog.kinds.item(i).data(Qt.UserRole): dialog.kinds.item(i) for i in range(dialog.kinds.count())}
        self.assertEqual(list(items), ["claude", "codex", "copilot", "antigravity", "cursor", "opencode"])
        self.assertIn("설치 필요", items["cursor"].text())
        self.assertFalse(items["cursor"].flags() & Qt.ItemIsEnabled)
        self.assertEqual(dialog.kind(), "claude")
        self.assertTrue(dialog.login_button.isEnabled())
        self.assertEqual([b.text() for b in dialog.link_buttons], ["이 PC 기존 로그인 사용"])
        dialog.kinds.setCurrentItem(items["antigravity"])
        self.assertTrue(dialog.login_button.isEnabled())
        self.assertEqual(dialog.login_button.text(), "새 계정 로그인")
        self.assertIn("/login", dialog.login_hint.text())
        self.assertEqual(dialog.link_buttons, [])
        dialog.kinds.setCurrentItem(items["claude"])
        dialog.link_buttons[0].click()
        self.assertEqual(dialog.choice[0], "link")
        self.assertTrue(dialog.choice[1]["use_default"])
        dialog.deleteLater()
        with patch("accounts.installed", return_value=True):
            dialog = accounts.AddAccountDialog(None, [record("claude")])
        self.assertEqual(dialog.link_buttons, [])
        dialog.login_button.click()
        self.assertEqual(dialog.choice, ("login", "claude"))
        dialog.deleteLater()

    def test_remove_confirmation_offers_folder_deletion_only_for_own_profiles(self):
        profile = accounts.create_profile("own1")
        seen = []

        def answer(box, check=False):
            seen.append(box.checkBox())
            if box.checkBox() is not None:
                self.assertFalse(box.checkBox().isChecked())
                box.checkBox().setChecked(check)
            next(b for b in box.buttons() if b.text() == "제거").click()
            return 0
        with patch.object(QMessageBox, "exec", lambda box: answer(box, True)):
            self.assertEqual(accounts.confirm_remove(None, "Claude", record("claude", "own1", str(profile))), (True, True))
            self.assertEqual(accounts.confirm_remove(None, "Claude", record("claude")), (True, False))
        self.assertIsNotNone(seen[0])
        self.assertIsNone(seen[1])
        with patch.object(QMessageBox, "exec", lambda box: 0):
            self.assertEqual(accounts.confirm_remove(None, "Claude", record("claude")), (False, False))


if __name__ == "__main__":
    unittest.main()
