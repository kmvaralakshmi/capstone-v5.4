"""Agent runtime, guardrails and the structured-judgement layer (no network, no API key)."""
import json
import os
import types
import unittest
from unittest.mock import patch

import _env  # noqa: F401
from esg_agents.framework import BaseAgent, Blackboard, Decision, LLMBrain, Tool


class FakeClient:
    """Scripted stand-in for the OpenAI client: yields queued tool calls."""
    def __init__(self, script):
        self.script = list(script)
        self.responses = types.SimpleNamespace(create=self._create)

    def _create(self, **kw):
        name, args = self.script.pop(0) if self.script else ("finish", {"summary": "x"})
        call = types.SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args))
        return types.SimpleNamespace(output=[call])


class Counter(BaseAgent):
    agent_id, name = "counter", "Counter"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.memory.update(n=0, fin=False)
        self.repeatable_tools = {"tick"}

    def build_tools(self):
        return [Tool("tick", "increment", lambda: self.memory.update(n=self.memory["n"] + 1) or {"n": self.memory["n"]}),
                Tool("finalize", "finish", lambda: self.memory.update(fin=True) or {}),
                Tool("boom", "always fails", lambda: 1 / 0)]

    def observe(self):
        return {"n": self.memory["n"], "fin": self.memory["fin"]}

    def is_done(self, s):
        return (s["fin"], "done") if s["fin"] else (False, "")

    def policy(self, s):
        return Decision("tick") if s["n"] < 3 else Decision("finalize")


class FakeJudgeBrain(LLMBrain):
    """An LLM that answers every judgement with a prepared dict (or None = no answer)."""
    def __init__(self, answers):
        super().__init__(client=FakeClient([]))
        self.answers = list(answers)

    def judge(self, system, prompt, max_chars=14000):
        return self.answers.pop(0) if self.answers else None


class FrameworkTests(unittest.TestCase):
    def test_judgement_uses_compact_agent_instructions_and_judge_limits(self):
        captured = {}

        class CaptureClient:
            def with_options(self, **options):
                captured["options"] = options
                return self

            def create(self, **request):
                captured["request"] = request
                message = types.SimpleNamespace(content='{"ok": true}')
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

        client = CaptureClient()
        client.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=client.create))
        brain = LLMBrain(client=client)
        brain.api_style = "chat"
        with patch.dict(os.environ, {"LLM_JUDGE_TIMEOUT": "17", "LLM_JUDGE_MAX_TOKENS": "93"}):
            self.assertEqual(brain.judge("system", "prompt"), {"ok": True})
        self.assertEqual(captured["options"]["timeout"], 17)
        self.assertEqual(captured["request"]["max_tokens"], 93)

        class CapturingBrain(FakeJudgeBrain):
            def judge(self, system, prompt, max_chars=14000):
                captured["system"] = system
                return {"ok": True}

        agent = Counter(brain=CapturingBrain([]), verbose=False)
        agent.judge("inspect evidence", "Answer JSON.", {}, lambda *_: (True, {"ok": True}, ""),
                    lambda _: {"ok": False})
        self.assertIn("evidence-grounded analyst", captured["system"])
        self.assertNotIn("only act by calling", captured["system"])

    def test_policy_mode_completes(self):
        r = Counter(verbose=False).run()
        self.assertEqual(r.status, "completed")
        self.assertEqual(r.decisions_by_source["policy"], 4)

    def test_llm_mode_uses_model_choices(self):
        brain = LLMBrain(client=FakeClient([("tick", {})] * 3 + [("finalize", {})]))
        with patch.dict(os.environ, {"LLM_PLANNER": "on"}):
            r = Counter(brain=brain, verbose=False).run()
        self.assertEqual(r.status, "completed")
        self.assertEqual(r.decisions_by_source["llm"], 4)

    def test_guardrails_override_bad_llm_choices(self):
        script = [("finish", {"summary": "done"}), ("nonexistent", {}), ("tick", {"bogus": 1}),
                  ("tick", {}), ("tick", {}), ("tick", {}), ("finalize", {})]
        with patch.dict(os.environ, {"LLM_PLANNER": "on"}):
            r = Counter(brain=LLMBrain(client=FakeClient(script)), verbose=False).run()
        self.assertEqual(r.status, "completed")
        self.assertGreaterEqual(r.decisions_by_source["guardrail"], 3)

    def test_failing_tool_is_blocked_not_looped(self):
        class Bad(Counter):
            def policy(self, s):
                return Decision("boom")
        self.assertEqual(Bad(verbose=False).run(max_steps=20).status, "stalled")

    def test_blackboard_roundtrip(self):
        b = Blackboard()
        b.post("a", "topic", {"x": 1}, severity="warning")
        self.assertEqual(b.latest("topic"), {"x": 1})

    def test_chat_style_llm_path(self):
        call = types.SimpleNamespace(function=types.SimpleNamespace(name="tick", arguments="{}"))
        msg = types.SimpleNamespace(tool_calls=[call], content="Narrative")
        request = {}

        class ChatClient:
            def __init__(self):
                self.options = []
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create))

            def with_options(self, **kwargs):
                self.options.append(kwargs)
                return self

            def _create(self, **kwargs):
                request.update(kwargs)
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

        client = ChatClient()
        brain = LLMBrain(client=client)
        brain.api_style = "chat"
        d = brain.decide("sys", {}, [], [Tool("tick", "x", lambda: 1)])
        self.assertEqual((d.tool, d.source), ("tick", "llm"))
        self.assertEqual(request["max_tokens"], 128)
        self.assertEqual(brain.write("sys", "prompt"), "Narrative")
        self.assertEqual([options["max_retries"] for options in client.options], [0, 0])

    def test_llm_budget_then_policy(self):
        with patch.dict(os.environ, {"LLM_PLANNER": "on"}):
            a = Counter(brain=LLMBrain(client=FakeClient([("tick", {})] * 50)), verbose=False)
            a.llm_budget = 2
            r = a.run()
        self.assertEqual(r.status, "completed")
        self.assertLessEqual(r.decisions_by_source["llm"], 2)
        self.assertEqual(r.budget_reason, "decision budget")

    def test_recover_uses_one_small_run_checkpoint_per_shared_brain(self):
        class CountingBrain(LLMBrain):
            def __init__(self):
                super().__init__(client=FakeClient([("tick", {})]))
                self.decision_calls = 0

            def decide(self, *args, **kwargs):
                self.decision_calls += 1
                return super().decide(*args, **kwargs)

        brain = CountingBrain()
        with patch.dict(os.environ, {"LLM_PLANNER": "recover"}):
            first = Counter(brain=brain, verbose=False).run()
            second = Counter(brain=brain, verbose=False).run()
        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertEqual(brain.decision_calls, 1)


class JudgementTests(unittest.TestCase):
    """Every judgement is verified by code; the LLM can never push an unverified answer through."""
    @staticmethod
    def _verify(ans, ev):
        return (ans.get("v") in (1, 2), {"v": ans.get("v")}, "v must be 1 or 2")

    def test_rule_fallback_without_llm(self):
        a = Counter(verbose=False)
        out = a.judge("t", "i", {"key": "k"}, self._verify, lambda ev: {"v": 2})
        self.assertEqual((out["v"], out["_source"]), (2, "rule"))

    def test_judgement_time_budget_is_reported(self):
        class TimeBudgetCounter(Counter):
            def on_start(self):
                self.judge_time_budget = 0
                self.judge("t", "i", {"key": "k"}, JudgementTests._verify, lambda ev: {"v": 2})

        with patch.dict(os.environ, {"LLM_PLANNER": "off"}):
            report = TimeBudgetCounter(brain=FakeJudgeBrain([]), verbose=False).run()
        self.assertTrue(report.judge_time_budget_reached)
        self.assertEqual(report.budget_reason, "time budget")

    def test_llm_answer_accepted_when_verified(self):
        a = Counter(brain=FakeJudgeBrain([{"v": 1}]), verbose=False)
        out = a.judge("t", "i", {"key": "k"}, self._verify, lambda ev: {"v": 2})
        self.assertEqual((out["v"], out["_source"]), (1, "llm"))

    def test_bad_llm_answer_rejected_then_corrected(self):
        a = Counter(brain=FakeJudgeBrain([{"v": 99}, {"v": 2}]), verbose=False)
        out = a.judge("t", "i", {"key": "k"}, self._verify, lambda ev: {"v": 1})
        self.assertEqual((out["v"], out["_source"]), (2, "llm"))

    def test_two_bad_answers_fall_back_to_rule(self):
        a = Counter(brain=FakeJudgeBrain([{"v": 99}, {"v": 98}]), verbose=False)
        out = a.judge("t", "i", {"key": "k"}, self._verify, lambda ev: {"v": 1})
        self.assertEqual((out["v"], out["_source"]), (1, "rule_fallback"))
        self.assertIn("rejected", out["_reason"])

    def test_faulty_verifier_never_accepts(self):
        a = Counter(brain=FakeJudgeBrain([{"v": 1}, {"v": 1}]), verbose=False)
        out = a.judge("t", "i", {"key": "k"}, lambda ans, ev: 1 / 0, lambda ev: {"v": 2})
        self.assertEqual(out["_source"], "rule_fallback")

    def test_judgement_counts_are_reported(self):
        a = Counter(verbose=False)
        a.judge("t", "i", {"key": "k"}, self._verify, lambda ev: {"v": 1})
        self.assertEqual(a._judgement_counts(), {"rule": 1})

    def test_llm_json_parsing(self):
        brain = LLMBrain(client=FakeClient([]))
        brain.write = lambda system, prompt, max_chars=0, **kwargs: '```json\n{"a": 1}\n```'
        self.assertEqual(brain.judge("s", "p"), {"a": 1})
        brain.write = lambda system, prompt, max_chars=0, **kwargs: "no json here"
        self.assertIsNone(brain.judge("s", "p"))


class TelemetryTests(unittest.TestCase):
    def test_stage_vs_sum(self):
        from esg_agents.telemetry import RunTracker
        t = RunTracker("t")
        with t.stage("acq"):
            with t.span("a", "agents", kind="agent"):
                pass
        self.assertIn("acq", t.summary()["stages"])

    def test_fallback_is_registered(self):
        from esg_agents.telemetry import RunTracker
        t = RunTracker("t2")
        with t.stage("acq"):
            t.mark_fallback("news:fallback", "bundled csv", "GDELT timed out")
        self.assertTrue(t.summary()["fallbacks_used"])


if __name__ == "__main__":
    unittest.main()
