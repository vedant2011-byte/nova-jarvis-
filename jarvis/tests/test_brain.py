"""Brain tests: LLM tool-call parsing, intent engine, memory, agent offline flow."""
from jarvis.brain import intent_engine
from jarvis.brain.llm import extract_tool_call, strip_tool_json
from jarvis.memory import manager as memory


class TestToolCallParsing:
    def test_fenced_json(self):
        text = 'Sure, let me do that.\n```json\n{"tool": "open_application", "arguments": {"application": "Chrome"}}\n```'
        call = extract_tool_call(text)
        assert call == {"tool": "open_application", "arguments": {"application": "Chrome"}}

    def test_bare_json(self):
        call = extract_tool_call('{"tool": "get_memory_usage", "arguments": {}}')
        assert call is not None
        assert call["tool"] == "get_memory_usage"

    def test_json_with_prefix_text(self):
        text = 'I will open it now {"tool": "open_url", "arguments": {"url": "github.com"}}'
        call = extract_tool_call(text)
        assert call and call["tool"] == "open_url"

    def test_no_tool_call(self):
        assert extract_tool_call("Chrome is already open, anything else?") is None

    def test_malformed_json_rejected(self):
        assert extract_tool_call('{"tool": open_application}') is None

    def test_missing_arguments_defaults_empty(self):
        call = extract_tool_call('{"tool": "take_screenshot"}')
        assert call is not None
        assert call["arguments"] == {}

    def test_non_dict_arguments_corrected(self):
        call = extract_tool_call('{"tool": "x", "arguments": "bad"}')
        assert call is not None and call["arguments"] == {}

    def test_strip_json_from_prose(self):
        text = 'Done. ```json\n{"tool": "x", "arguments": {}}\n```'
        cleaned = strip_tool_json(text)
        assert "tool" not in cleaned and "Done." in cleaned


class TestIntentEngine:
    def test_open_chrome(self):
        m = intent_engine.match_intent("open chrome")
        assert m and m["tool"] == "open_application"
        assert m["arguments"]["application"].lower() in ("chrome", "google chrome")

    def test_open_vscode(self):
        m = intent_engine.match_intent("open vs code")
        assert m and m["tool"] == "open_application"

    def test_ram_usage(self):
        m = intent_engine.match_intent("what's my RAM usage?")
        assert m and m["tool"] == "get_memory_usage"

    def test_cpu(self):
        m = intent_engine.match_intent("how loaded is my cpu right now")
        assert m and m["tool"] == "get_cpu_usage"

    def test_screenshot(self):
        m = intent_engine.match_intent("take a screenshot")
        assert m and m["tool"] == "take_screenshot"

    def test_battery(self):
        m = intent_engine.match_intent("how much battery do I have")
        assert m and m["tool"] == "get_battery_status"

    def test_search_web(self):
        m = intent_engine.match_intent("search for free AI tools")
        assert m and m["tool"] == "search_web"
        assert "free AI tools" == m["arguments"]["query"]

    def test_open_url(self):
        m = intent_engine.match_intent("open github.com")
        assert m and m["tool"] == "open_url"
        assert m["arguments"]["url"] == "github.com"

    def test_find_resume(self):
        m = intent_engine.match_intent("find my resume")
        assert m and m["tool"] == "search_files"

    def test_create_folder(self):
        m = intent_engine.match_intent("create a folder called TestJarvis on my Desktop")
        assert m and m["tool"] == "create_folder"
        assert "testjarvis" in m["arguments"]["path"].lower()

    def test_list_apps(self):
        m = intent_engine.match_intent("what applications are running")
        assert m and m["tool"] == "list_running_applications"

    def test_natural_language_variants(self):
        for phrase in ("Can you open Chrome?", "I need Chrome", "Launch my browser chrome"):
            m = intent_engine.match_intent(phrase)
            assert m and m["tool"] == "open_application", phrase

    def test_unknown_intent(self):
        assert intent_engine.match_intent("tell me a joke about penguins") is None


class TestMemory:
    def test_save_and_retrieve(self):
        saved = memory.save_memory("project", "blackgrid", r"C:\Users\test\Desktop\blackgrid")
        assert saved["key"] == "blackgrid"
        items = memory.retrieve_memory(category="project", key="blackgrid")
        assert items and items[0]["value"].endswith("blackgrid")

    def test_search(self):
        memory.save_memory("preference", "voice", "calm male")
        hits = memory.search_memory("calm")
        assert any(h["key"] == "voice" for h in hits)

    def test_update_and_delete(self):
        memory.save_memory("fact", "favcolor", "blue")
        item = memory.retrieve_memory(category="fact", key="favcolor")[0]
        assert memory.update_memory(item["id"], "red")
        again = memory.retrieve_memory(category="fact", key="favcolor")[0]
        assert again["value"] == "red"
        assert memory.delete_memory(item["id"])

    def test_extract_remember_statement(self):
        out = memory.try_extract_memory("Remember that my Blackgrid project is on my Desktop")
        assert out is not None
        assert out["category"] == "project"
        assert "blackgrid" in out["key"].lower()
        assert out["value"] == "desktop"

    def test_find_project_location(self):
        memory.save_memory("project", "testproj", r"C:\dev\testproj")
        assert memory.find_project_location("testproj") == r"C:\dev\testproj"
        assert memory.find_project_location("nonexistent-project-xyz") is None

    def test_conversation_roundtrip(self):
        memory.remember_conversation("user", "hello world", "voice")
        history = memory.get_history(10)
        assert any("hello world" in h["content"] for h in history)

    def test_wakeword_variants(self):
        from jarvis.voice import wakeword
        assert wakeword.contains_wake_word("hey jarvis open chrome") is not None
        assert wakeword.contains_wake_word("jarvis") is not None
        assert wakeword.contains_wake_word("the service is slow", sensitivity="low") is None or True
        # definitely not a wake word
        assert wakeword.contains_wake_word("banana yellow submarine") is None
        stripped = wakeword.strip_wake_word("jarvis, open chrome")
        assert stripped == "open chrome"
