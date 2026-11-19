import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from adapter import Adapter, AdapterError, FileTools, State, add_reminder, signature, LOCAL_NAMES


def response(content="", calls=None, native=None):
    delta = {"content": content, "reasoning_content": "test reasoning"}
    if calls:
        delta["tool_calls"] = [{"index": i, **c} for i, c in enumerate(calls)]
    if native:
        delta["server_tool_calls"] = native
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls" if calls else "stop"}]}


def call(name, args, id="call-doc"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeAPI:
    def __init__(self, scripted):
        self.scripted = iter(scripted)
        self.requests, self.uploads = [], []

    def stream(self, payload):
        self.requests.append(copy.deepcopy(payload))
        step = next(self.scripted)
        yield from step if isinstance(step, list) else [step]

    def upload(self, data, filename, purpose, ocr=False):
        self.uploads.append((filename, purpose, ocr))
        if ocr:
            return {"page_count": 2, "pages": [{"index": 0, "text": "hello", "finish_reason": "stop"}, {"index": 1, "text": "partial", "finish_reason": "length"}]}
        return {"id": "file-api-test123"}


def collect(parts):
    result = {"role": "assistant", "content": ""}
    for p in parts:
        d = p.get("delta", {})
        result["content"] += d.get("content", "")
        if d.get("tool_calls"):
            result["tool_calls"] = d["tool_calls"]
    return result


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "diagram.pdf"
        self.path.write_bytes(b"%PDF-test")
        self.settings = {"timezone": "Asia/Hong_Kong", "location": "", "allowed_roots": [self.tmp.name]}
        self.state = State(Path(self.tmp.name) / "state")

    def tearDown(self):
        self.state.db.close()
        self.tmp.cleanup()

    def body(self, content="read the diagram"):
        return {"model": "deepseek-flash", "messages": [{"role": "user", "content": content}], "tool_choice": "none",
                "tools": [{"type": "function", "function": {"name": "finder-read", "parameters": {"type": "object", "properties": {}}}}]}

    def test_merge_preserves_raycast_and_native_tools_and_images(self):
        api = FakeAPI([response("ok")])
        adapter = Adapter(self.settings, self.state, lambda _: api)
        body = self.body()
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}
        body["messages"][0]["content"] = [{"type": "text", "text": "describe"}, image]
        list(adapter.run(body, "key"))
        req = api.requests[0]
        self.assertEqual(req["tool_choice"], "auto")
        self.assertEqual(req["thinking"], {"type": "enabled"})
        self.assertEqual([t["type"] for t in req["tools"]].count("deepseek_web_search"), 1)
        self.assertEqual([t["type"] for t in req["tools"]].count("deepseek_read_url"), 1)
        names = {t.get("function", {}).get("name") for t in req["tools"]}
        self.assertTrue(LOCAL_NAMES <= names)
        self.assertEqual(LOCAL_NAMES, {"deepseek_open_document", "deepseek_view_image", "deepseek_ocr"})
        self.assertFalse({"deepseek_research_web", "deepseek_read_webpage"} & names)
        self.assertIn("finder-read", names)
        self.assertEqual(req["messages"][-1]["content"][-1], image)

    def test_native_document_context_survives_next_turn(self):
        api = FakeAPI([response(calls=[call("deepseek_open_document", {"path": str(self.path)})]), response("the blue bar is 17"), response("the other bar is 43")])
        adapter = Adapter(self.settings, self.state, lambda _: api)
        body = self.body()
        answer = collect(adapter.run(body, "key"))
        self.assertNotIn("tool_calls", answer)  # Internal tools must not be sent to Raycast to execute.
        self.assertEqual(api.uploads, [("diagram.pdf", "rich_layout_recognition", False)])
        follow = self.body()
        follow["messages"] += [answer, {"role": "user", "content": "What about the other bar?"}]
        collect(adapter.run(follow, "key"))
        for req in api.requests[1:]:
            self.assertTrue(any(b.get("file_id") == "file-api-test123" for m in req["messages"] if isinstance(m.get("content"), list) for b in m["content"]))
        self.assertIn("read the diagram", api.requests[1]["messages"][-1]["content"][0]["text"])
        self.assertEqual(len(api.uploads), 1)

    def test_mixed_local_and_raycast_calls_restore_all_results(self):
        api = FakeAPI([response(calls=[call("deepseek_open_document", {"path": str(self.path)}), call("finder-read", {}, "call-finder")]), response("done")])
        adapter = Adapter(self.settings, self.state, lambda _: api)
        body = self.body()
        answer = collect(adapter.run(body, "key"))
        self.assertEqual(len(answer["tool_calls"]), 1)
        self.assertEqual(answer["tool_calls"][0]["id"], "call-finder")
        follow = self.body()
        follow["messages"] += [answer, {"role": "tool", "tool_call_id": "call-finder", "content": "test result"}]
        collect(adapter.run(follow, "key"))
        ms = api.requests[-1]["messages"]
        self.assertEqual({m.get("tool_call_id") for m in ms if m["role"] == "tool"}, {"call-doc", "call-finder"})
        self.assertTrue(any(isinstance(m.get("content"), list) and any(b.get("type") == "file" for b in m["content"]) for m in ms))

    def test_state_isolated_by_transcript_and_key(self):
        self.state.remember([{"role": "user", "content": "one"}], [{"role": "user", "content": "secret"}], [], "account1")
        self.assertFalse(self.state.restore([{"role": "user", "content": "two"}], "account1")[2])
        self.assertFalse(self.state.restore([{"role": "user", "content": "one"}], "account2")[2])

    def test_raycast_markdown_reserialization_keeps_document_context(self):
        visible = [{"role": "user", "content": "read this"}, {"role": "assistant", "content": "- Blue: 17\n- Amber: 43"}]
        native = visible + [{"role": "user", "content": [{"type": "file", "file_id": "file-api-kept"}]}]
        self.state.remember(visible, native, [], "account")
        replay = [{"role": "user", "content": [{"type": "text", "text": "read this"}]}, {"role": "assistant", "content": "- Blue: 17\n-  Amber: 43"}, {"role": "user", "content": "next question"}]
        restored, _, matched = self.state.restore(replay, "account")
        self.assertTrue(matched)
        self.assertEqual(restored[-2]["content"][0]["file_id"], "file-api-kept")

    def test_normalization_does_not_change_user_input_or_code(self):
        self.assertNotEqual(signature([{"role": "user", "content": "a b"}]), signature([{"role": "user", "content": "a  b"}]))
        self.assertNotEqual(signature([{"role": "assistant", "content": "```\n x\n```"}]), signature([{"role": "assistant", "content": "```\n  x\n```"}]))

    def test_ocr_cache_page_range_and_truncation(self):
        api = FakeAPI([])
        files = FileTools(self.settings, self.state, api, "account")
        result, attached = files.execute("deepseek_ocr", {"path": str(self.path)})
        self.assertTrue(result["incomplete"])
        self.assertEqual(attached, [])
        second, _ = files.execute("deepseek_ocr", {"path": str(self.path), "start_page": 1, "end_page": 1})
        self.assertFalse(second["incomplete"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(api.uploads), 1)

    def test_native_web_stream_and_history_without_wrapper(self):
        native = [{"index": 0, "calls": [{"type": "deepseek_web_search", "status": "completed", "action": {"type": "search", "queries": ["test"]}, "sources": [{"url": "https://example.com", "title": "Example"}]}]}]
        event = {"choices": [{"delta": None, "server_tool_event": {"type": "test"}, "finish_reason": None}]}
        api = FakeAPI([[{"choices": [{"delta": {"role": "assistant", "content": None}, "finish_reason": None}]}, event, response("verified answer", native=native)], response("followup")])
        adapter = Adapter(self.settings, self.state, lambda _: api)
        body = self.body("search the web")
        parts = list(adapter.run(body, "key"))
        answer = collect(parts)
        self.assertEqual(answer["content"], "verified answer")
        self.assertNotIn("tool_calls", answer)
        self.assertTrue(any(p.get("server_tool_event") for p in parts))
        self.assertEqual(len(api.requests), 1)  # No separate search conversation.
        body["messages"] += [answer, {"role": "user", "content": "follow up"}]
        collect(adapter.run(body, "key"))
        prior = next(m for m in api.requests[-1]["messages"] if m.get("server_tool_calls"))
        self.assertEqual(prior["server_tool_calls"], native)

    def test_obsolete_function_is_not_forwarded_to_raycast(self):
        api = FakeAPI([response(calls=[call("deepseek_research_web", {"query": "test"})]), response("recovered")])
        adapter = Adapter(self.settings, self.state, lambda _: api)
        answer = collect(adapter.run(self.body("search"), "key"))
        self.assertNotIn("tool_calls", answer)
        self.assertEqual(answer["content"], "recovered")
        result = next(m for m in api.requests[-1]["messages"] if m["role"] == "tool")
        self.assertEqual(json.loads(result["content"])["status"], "error")
        self.assertEqual(api.uploads, [])

    def test_symlink_cannot_escape_allowed_root(self):
        restricted = Path(self.tmp.name) / "allowed"
        restricted.mkdir()
        link = restricted / "escape.pdf"
        link.symlink_to(self.path)
        settings = {**self.settings, "allowed_roots": [str(restricted)]}
        with self.assertRaises(AdapterError):
            FileTools(settings, self.state, FakeAPI([]), "account").read_path(str(link))

    def test_marker_position_and_preserve_old_dates(self):
        messages = [{"role": "latest_reminder", "content": "2026-11-18,星期三,,App,中文"}, {"role": "user", "content": "yesterday"}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "today"}]
        add_reminder(messages, self.settings, dt.datetime(2026, 11, 19, tzinfo=ZoneInfo("Asia/Hong_Kong")))
        self.assertEqual(messages[0]["content"], "2026-11-18,星期三,,App,中文")
        self.assertEqual(messages[-2], {"role": "latest_reminder", "content": "2026-11-19,星期四,,App,中文"})
        english = [{"role": "user", "content": "today"}]
        add_reminder(english, {**self.settings, "language": "English"}, dt.datetime(2026, 11, 19, tzinfo=ZoneInfo("Asia/Hong_Kong")))
        self.assertTrue(english[0]["content"].endswith(",App,English"))


if __name__ == "__main__":
    unittest.main()
