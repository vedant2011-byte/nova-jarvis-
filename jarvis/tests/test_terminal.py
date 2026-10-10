"""Terminal tool behaviour tests (no destructive commands)."""
from jarvis.tools.terminal import find_local_urls, run_terminal_command


class TestFindUrls:
    def test_detect_localhost(self):
        text = "VITE ready in 300 ms\nLocal: http://localhost:5173/"
        urls = find_local_urls(text)
        assert urls and "localhost:5173" in urls[0]

    def test_detect_127(self):
        urls = find_local_urls("Running on http://127.0.0.1:8000")
        assert urls and ":8000" in urls[0]

    def test_ignores_remote(self):
        assert find_local_urls("see https://example.com/docs") == []


class TestRunCommand:
    def test_echo(self):
        r = run_terminal_command("echo hello", wait_seconds=15)
        assert r["success"], r.get("error")
        assert "hello" in r["result"]["stdout"]

    def test_failing_command_reports_failure(self):
        r = run_terminal_command("cmd /c exit 3", wait_seconds=15)
        assert not r["success"]
        assert "code 3" in r.get("error", "")

    def test_python_version(self):
        r = run_terminal_command("python --version", wait_seconds=20)
        assert r["success"]
        assert "python" in r["result"]["stdout"].lower()

    def test_timeout(self):
        r = run_terminal_command("ping -n 30 127.0.0.1", wait_seconds=2)
        assert not r["success"]
        assert "timed out" in r.get("error", "").lower()

    def test_bad_cwd(self):
        r = run_terminal_command("echo hi", cwd=r"C:\definitely\not\a\real\dir\xyz")
        assert not r["success"]
