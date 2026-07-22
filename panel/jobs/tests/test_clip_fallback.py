from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from panel.jobs.clip_pipeline import propose_cuts


class ClipFallbackTests(unittest.TestCase):
    def test_propose_cuts_fallback(self):
        segments = [
            {"start": 0.0, "end": 5.0, "text": "ola"},
            {"start": 5.0, "end": 40.0, "text": "mundo"},
        ]

        class FakeLlm:
            @staticmethod
            def _generate_response(prompt: str) -> str:
                raise RuntimeError("no llm")

        fake_services = types.ModuleType("app.services")
        fake_services.llm = FakeLlm
        sys.modules["app"] = types.ModuleType("app")
        sys.modules["app.services"] = fake_services
        sys.modules["app.services.llm"] = FakeLlm

        with mock.patch(
            "panel.jobs.clip_pipeline.ensure_repo_on_path",
            return_value=None,
        ):
            cuts = propose_cuts(segments, topic="teste", target_duration=30)

        self.assertTrue(cuts)
        self.assertLessEqual(cuts[0]["end"], 40.0)
