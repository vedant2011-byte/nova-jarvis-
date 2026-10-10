"""Security tests: path validation, command classification, permissions."""
import pytest

from jarvis.security.command_policy import classify
from jarvis.security.permissions import RiskLevel, decide
from jarvis.security.validator import PathValidationError, normalize, validate_delete, validate_write


class TestPathValidation:
    def test_normalize_simple(self, temp_allowed_root):
        p = normalize(str(temp_allowed_root / "foo.txt"))
        assert str(p).startswith(str(temp_allowed_root))

    def test_traversal_blocked(self, temp_allowed_root):
        sneaky = str(temp_allowed_root / ".." / ".." / "Windows" / "system32" / "evil.exe")
        with pytest.raises(PathValidationError):
            validate_write(sneaky)

    def test_outside_allowed_roots(self, temp_allowed_root):
        with pytest.raises(PathValidationError):
            validate_write(r"C:\SomeRandomPlace\file.txt")

    def test_protected_windows(self, temp_allowed_root):
        with pytest.raises(PathValidationError):
            validate_write(r"C:\Windows\System32\drivers\etc\hosts")

    def test_protected_appdata_read(self):
        # only meaningful when the default protected list is active
        import os
        if os.getenv("JARVIS_DISABLE_DEFAULT_PROTECTED") == "1":
            pytest.skip("protected list relaxed for tests")
        from jarvis.security.validator import validate_read
        with pytest.raises(PathValidationError):
            validate_read(r"C:\Users\Victim\AppData\Roaming\creds.txt")

    def test_drive_relative_rejected(self):
        with pytest.raises(PathValidationError):
            normalize("C:evil.txt")

    def test_empty_rejected(self):
        with pytest.raises(PathValidationError):
            normalize("   ")

    def test_delete_requires_exist(self, temp_allowed_root):
        with pytest.raises(PathValidationError):
            validate_delete(str(temp_allowed_root / "nope.txt"))

    def test_valid_delete(self, temp_allowed_root):
        f = temp_allowed_root / "x.txt"
        f.write_text("hi")
        assert validate_delete(str(f)) == f.resolve()


class TestCommandPolicy:
    def test_safe_dev_commands(self):
        for cmd in ("npm run dev", "python script.py", "git status", "pytest -q"):
            d = classify(cmd)
            assert d.risk == "low", f"{cmd} -> {d.risk}"
            assert not d.requires_confirmation

    def test_medium_risk(self):
        d = classify("npm install left-pad")
        assert d.risk == "medium"
        assert d.requires_confirmation

    def test_high_risk_shutdown(self):
        d = classify("shutdown /s /t 0")
        # shutdown is in BLOCKED_ALWAYS for auto-run, surfaced as blocked
        assert d.risk in ("high", "blocked")
        assert d.blocked or d.requires_confirmation

    def test_rm_rf_blocked(self):
        d = classify("rm -rf /")
        assert d.blocked

    def test_format_blocked(self):
        d = classify("format C:")
        assert d.blocked

    def test_registry_high(self):
        d = classify("reg delete HKLM\\Software\\Key")
        assert d.risk == "high"

    def test_system32_high(self):
        d = classify("del C:\\Windows\\system32\\config")
        assert d.risk == "high"

    def test_chained_commands_flagged(self):
        d = classify("echo hi && rm -rf something")
        assert d.requires_confirmation

    def test_empty_blocked(self):
        assert classify("").blocked

    def test_unknown_is_conservative(self):
        d = classify("weirdtool --do-stuff")
        assert d.requires_confirmation


class TestPermissions:
    def test_safe_tool_auto(self):
        d = decide("open_application", {"application": "Chrome"})
        assert d.allowed and not d.requires_confirmation

    def test_delete_requires_confirmation(self):
        d = decide("delete_file", {"path": r"C:\Users\x\Desktop\a.txt"})
        assert d.requires_confirmation
        assert d.risk in (RiskLevel.HIGH_RISK, RiskLevel.CONFIRM)

    def test_shutdown_high_risk(self):
        d = decide("system_shutdown", {})
        assert d.requires_confirmation and d.risk == RiskLevel.HIGH_RISK

    def test_dangerous_terminal_blocked(self):
        d = decide("run_terminal_command", {"command": "rm -rf /"})
        assert not d.allowed

    def test_low_risk_terminal_auto(self):
        d = decide("run_terminal_command", {"command": "git status"})
        assert d.allowed and not d.requires_confirmation

    def test_medium_terminal_needs_confirm(self):
        d = decide("run_terminal_command", {"command": "npm install lodash"})
        assert d.requires_confirmation

    def test_unknown_tool_conservative(self):
        d = decide("totally_unknown_tool", {})
        assert d.requires_confirmation
