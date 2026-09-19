"""No-network integration checks against the pinned OpenHands SDK.

These supplement the stdlib policy suite when the SDK is installed.
"""

import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

try:
    from litellm.types.utils import ModelResponse
    from pydantic import PrivateAttr
    from openhands.sdk import LLM
    from openhands.sdk.llm import LLMResponse, Message, MessageToolCall
    from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage
    HAS_SDK = True
except ImportError:
    HAS_SDK = False

SOURCE = Path(__file__).parents[1] / "sources/notebook-field-notes"
spec = importlib.util.spec_from_file_location("notebook_writer_sdk", SOURCE / "writer.py")
w = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = w
spec.loader.exec_module(w)
PINS = [{"repository": "OpenHands/software-agent-sdk", "sha": "a" * 40}]
GENERATED = {"title": "A design investigation", "summary": "A summary of the relevant public code", "body_markdown": "Implementation evidence.", "tags": ["architecture"]}


if HAS_SDK:
    class FixtureLLM(LLM):
        _calls: int = PrivateAttr(default=0)
        _available_tools: set = PrivateAttr(default_factory=set)
        _saw_public_source: bool = PrivateAttr(default=False)

        def vision_is_active(self):
            return True

        def completion(self, *, messages, tools=None, **kwargs):
            self._calls += 1
            self._available_tools = {tool.name for tool in tools}
            if self._calls == 1:
                source_tool = next(name for name in self._available_tools if name != "finish")
                name = source_tool
                arguments = {"operation": "read_file", "repository": PINS[0]["repository"],
                             "sha": PINS[0]["sha"], "path": "src/api.py"}
            else:
                self._saw_public_source = any(
                    message.role == "tool" and any("1: def dispatch():" in getattr(part, "text", "") for part in message.content)
                    for message in messages
                )
                name, arguments = "finish", {"message": json.dumps(GENERATED)}
            return LLMResponse(
                message=Message(role="assistant", tool_calls=[MessageToolCall(
                    id=f"call-{self._calls}", name=name, arguments=json.dumps(arguments), origin="completion",
                )]),
                metrics=MetricsSnapshot(model_name="test", accumulated_cost=0.0,
                                        max_budget_per_task=0.0, accumulated_token_usage=TokenUsage(model="test")),
                raw_response=MagicMock(spec=ModelResponse, id=f"response-{self._calls}"),
            )


@unittest.skipUnless(HAS_SDK, "OpenHands SDK is not installed in this interpreter")
class OpenHandsWriterIntegrationTests(unittest.TestCase):
    def test_initialization_check_does_not_call_model_or_read_source(self):
        llm = FixtureLLM(model="test-model")
        with patch("openhands.sdk.plugin.load_available_plugins", return_value={}), \
                patch("openhands.sdk.conversation.impl.local_conversation.load_available_plugins", return_value={}), \
                patch.object(w, "fetch_public") as source:
            result = w.check_writer(llm)
        self.assertEqual(result, {"writer_model": "test-model", "tools": ["finish", "public_evidence"]})
        self.assertEqual(llm._calls, 0)
        source.assert_not_called()

    def test_unexpected_sdk_tool_is_reported_without_execution(self):
        from openhands.sdk.conversation import LocalConversation

        def conversation_with_extra_tool(**kwargs):
            kwargs["agent"] = kwargs["agent"].model_copy(update={"include_default_tools": ["FinishTool", "ThinkTool"]})
            return LocalConversation(**kwargs)

        llm = FixtureLLM(model="test-model")
        with patch("openhands.sdk.plugin.load_available_plugins", return_value={}), \
                patch("openhands.sdk.conversation.impl.local_conversation.load_available_plugins", return_value={}), \
                patch("openhands.sdk.conversation.LocalConversation", side_effect=conversation_with_extra_tool):
            with self.assertRaisesRegex(w.WriterError, r"^writer_tool_boundary:tools=finish,public_evidence,think:mcp=0$"):
                w.check_writer(llm)
        self.assertEqual(llm._calls, 0)

    def test_agent_reads_public_source_and_returns_a_note_with_no_other_tools(self):
        llm = FixtureLLM(model="test-model")
        with patch("openhands.sdk.plugin.load_available_plugins", return_value={}), \
                patch("openhands.sdk.conversation.impl.local_conversation.load_available_plugins", return_value={}), \
                patch.object(w, "fetch_public", return_value=b"def dispatch():\n    pass\n"):
            result = w.write_note({"title": "Public change"}, llm=llm, pinned_commits=PINS)
        self.assertEqual(result["generated"], GENERATED)
        self.assertEqual(result["examined_commits"], PINS)
        self.assertEqual(llm._calls, 2)
        self.assertTrue(llm._saw_public_source)
        self.assertEqual(len(llm._available_tools), 2)
        self.assertIn("finish", llm._available_tools)

    def test_ambient_plugin_refused_before_any_conversation_or_model_call(self):
        llm = FixtureLLM(model="test-model")
        with patch("openhands.sdk.plugin.load_available_plugins", return_value={"unsafe": object()}), \
                patch("openhands.sdk.conversation.LocalConversation") as conversation:
            with self.assertRaisesRegex(w.WriterError, "ambient plugins"):
                w.write_note({}, llm=llm, pinned_commits=PINS)
        conversation.assert_not_called()
        self.assertEqual(llm._calls, 0)


if __name__ == "__main__":
    unittest.main()
