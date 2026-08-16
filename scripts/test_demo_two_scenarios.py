from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType


def load_demo_module() -> ModuleType:
    script = Path(__file__).with_name("demo-two-scenarios.py")
    spec = importlib.util.spec_from_file_location("demo_two_scenarios", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEMO = load_demo_module()


class DemoReasonTest(unittest.TestCase):
    def test_scene_one_accepts_documented_and_observed_blocker_titles(self) -> None:
        meeting = {
            "meetingNo": "MTG-DEMO-TRANSIENT",
            "organizerId": 1002,
            "title": "演示并发占位：WeMe1.1",
            "startAt": "2026-08-19T12:30:00+08:00",
            "endAt": "2026-08-19T14:30:00+08:00",
            "status": "CONFIRMED",
        }

        self.assertEqual(DEMO.demo_reason(meeting), "scene-1")
        meeting["title"] = "演示并发占用"
        self.assertEqual(DEMO.demo_reason(meeting), "scene-1")
        self.assertEqual(DEMO.cleanup_reason(meeting), "scene-1")

        for short_title in ("并发占位", "并发占用"):
            meeting["title"] = short_title
            self.assertEqual(DEMO.demo_reason(meeting), "scene-1")
            self.assertEqual(DEMO.cleanup_reason(meeting), "scene-1")

    def test_permanent_li_si_blockers_are_never_demo_cleanup_candidates(self) -> None:
        meeting = {
            "meetingNo": "MTG-DEMO-LISI-20260826-1300",
            "organizerId": 1003,
            "title": "支付链路发布风险评审",
            "startAt": "2026-08-26T13:00:00+08:00",
            "endAt": "2026-08-26T14:00:00+08:00",
            "status": "CONFIRMED",
        }

        self.assertIsNone(DEMO.demo_reason(meeting))
        self.assertIsNone(DEMO.cleanup_reason(meeting))


if __name__ == "__main__":
    unittest.main()
