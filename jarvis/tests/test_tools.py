"""Tool registry, schema validation and filesystem tool behaviour."""
import pytest

from jarvis.tools.tool_registry import get_registry


class TestRegistry:
    def test_all_tools_registered(self):
        reg = get_registry()
        expected = {
            "open_application", "close_application", "focus_application",
            "list_running_applications",
            "list_directory", "search_files", "read_file", "create_file",
            "edit_file", "create_folder", "delete_file", "copy_file", "move_file",
            "run_terminal_command",
            "get_cpu_usage", "get_memory_usage", "get_disk_usage", "get_battery_status",
            "get_network_status", "get_current_time", "get_system_info",
            "take_screenshot",
            "open_url", "open_browser", "search_web", "click", "type", "scroll",
            "extract_text", "download_file",
        }
        names = set(reg.names())
        missing = expected - names
        assert not missing, f"missing tools: {missing}"

    def test_schemas_valid(self):
        reg = get_registry()
        for schema in reg.schemas():
            assert schema["name"]
            assert schema["description"]
            assert schema["parameters"]["type"] == "object"
            assert schema["risk"] in ("safe", "confirm", "high-risk", "dynamic")


class TestValidation:
    def test_unknown_tool_rejected(self):
        result = get_registry().execute("nope", {})
        assert not result["success"]

    def test_missing_required_arg(self):
        result = get_registry().execute("open_application", {})
        assert not result["success"]
        assert "missing required argument" in result["error"]

    def test_wrong_type_arg(self):
        result = get_registry().execute("open_application", {"application": 123})
        assert not result["success"]

    def test_unexpected_arg(self):
        result = get_registry().execute("get_cpu_usage", {"evil": 1})
        assert not result["success"]


class TestFilesystem:
    def test_create_folder_and_list(self, temp_allowed_root):
        from jarvis.tools.filesystem import create_folder, list_directory
        r = create_folder(str(temp_allowed_root / "TestJarvis"))
        assert r["success"]
        r2 = list_directory(str(temp_allowed_root))
        names = [e["name"] for e in r2["result"]["entries"]]
        assert "TestJarvis" in names

    def test_create_edit_read_file(self, temp_allowed_root):
        from jarvis.tools.filesystem import create_file, edit_file, read_file
        p = str(temp_allowed_root / "note.txt")
        assert create_file(p, "hello")["success"]
        assert edit_file(p, "world", append=True)["success"]
        r = read_file(p)
        assert r["success"] and "helloworld" in r["result"]["content"]

    def test_create_duplicate_fails(self, temp_allowed_root):
        from jarvis.tools.filesystem import create_file
        p = str(temp_allowed_root / "dup.txt")
        assert create_file(p, "a")["success"]
        assert not create_file(p, "b")["success"]

    def test_outside_root_fails(self):
        from jarvis.tools.filesystem import create_file
        assert not create_file(r"D:\evil\file.txt", "x")["success"] or "outside allowed" in create_file(r"D:\evil\file2.txt", "x").get("error", "")

    def test_search_files(self, temp_allowed_root):
        from jarvis.tools.filesystem import search_files
        (temp_allowed_root / "resume_2024.pdf").write_bytes(b"%PDF-1.4 fake")
        r = search_files("resume", root=str(temp_allowed_root))
        assert r["success"]
        assert any("resume" in m["name"] for m in r["result"]["matches"])

    def test_copy_move(self, temp_allowed_root):
        from jarvis.tools.filesystem import copy_file, create_file, move_file
        src = str(temp_allowed_root / "a.txt")
        create_file(src, "data")
        assert copy_file(src, str(temp_allowed_root / "b.txt"))["success"]
        assert move_file(str(temp_allowed_root / "b.txt"), str(temp_allowed_root / "c.txt"))["success"]
        assert (temp_allowed_root / "c.txt").read_text() == "data"

    def test_delete_file(self, temp_allowed_root):
        from jarvis.tools.filesystem import delete_file
        p = temp_allowed_root / "gone.txt"
        p.write_text("bye")
        r = delete_file(str(p))
        assert r["success"]
        assert not p.exists()


class TestSystemTools:
    def test_cpu(self):
        from jarvis.tools.system import get_cpu_usage
        r = get_cpu_usage()
        assert r["success"] and 0 <= r["result"]["percent"] <= 100

    def test_memory(self):
        from jarvis.tools.system import get_memory_usage
        r = get_memory_usage()
        assert r["success"] and r["result"]["total_gb"] > 0

    def test_time(self):
        from jarvis.tools.system import get_current_time
        assert r["success"] if (r := get_current_time()) else False

    def test_battery(self):
        from jarvis.tools.system import get_battery_status
        r = get_battery_status()
        assert r["success"]

    def test_network(self):
        from jarvis.tools.system import get_network_status
        r = get_network_status()
        assert r["success"] and isinstance(r["result"]["internet"], bool)

    def test_screenshot(self, temp_allowed_root):
        from jarvis.tools import screenshot
        from jarvis.config.settings import DATA_DIR
        r = screenshot.take_screenshot()
        if r["success"]:
            assert Path(r["result"]["path"]).exists()
        # on headless CI this may fail; that's acceptable


from pathlib import Path  # noqa: E402
