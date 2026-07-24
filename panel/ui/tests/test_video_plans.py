from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from panel.niches.models import Niche
from panel.ui.models import LlmCredential, ScriptDraft, VideoPlan
from panel.ui.services import video_plans as plan_service


class VideoPlanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("plan-ops", password="ops-pass-ok")
        self.client = Client()
        self.client.login(username="plan-ops", password="ops-pass-ok")
        self.niche = Niche.objects.create(
            name="Curiosidades Dark",
            slug="curiosidades-dark",
            briefing="Narrado, sem aparecer",
            keywords="curiosidades\nfatos",
            default_voice="pt-BR-FranciscaNeural-Female",
        )
        self.cred = LlmCredential.objects.create(
            name="OpenAI",
            provider="openai",
            api_key="sk-test",
            model_name="gpt-5.5",
            base_url="https://api.openai.com/v1",
            is_default=True,
        )

    def test_create_plan_with_mocks(self):
        fake_llm = {
            "title": "5 fatos absurdos",
            "summary_pt": "Plano dark narrado.",
            "script_body": "Olha só esse fato…",
            "voice_name": "pt-BR-AntonioNeural-Male",
            "voice_notes": "Voz grave",
            "assets": [
                {
                    "kind": "stock_video",
                    "query_or_brief": "night city rain",
                    "why": "clima",
                    "timing_hint": "início",
                },
                {
                    "kind": "recorded",
                    "query_or_brief": "não deve passar em dark",
                    "why": "x",
                    "timing_hint": "meio",
                },
            ],
            "dub_suggestions": [
                {
                    "title": "Weird facts EN",
                    "url": "https://www.youtube.com/watch?v=abc",
                    "why": "bom para dub",
                    "language": "en",
                }
            ],
        }
        with (
            patch(
                "panel.ui.services.video_plans._gather_plan_signals",
                return_value={"videos": [], "dub_candidates": [], "errors": []},
            ),
            patch(
                "panel.ui.services.video_plans._call_plan_llm",
                return_value=fake_llm,
            ),
        ):
            plan = plan_service.create_plan(
                niche=self.niche,
                topic="fatos",
                video_format="dark",
                llm_credential=self.cred,
            )

        self.assertEqual(plan.title, "5 fatos absurdos")
        self.assertIn("fato", plan.script_body.lower())
        self.assertEqual(plan.voice_name, "pt-BR-AntonioNeural-Male")
        self.assertEqual(plan.llm_credential_id, self.cred.pk)
        kinds = [a["kind"] for a in plan.assets_json]
        self.assertIn("stock_video", kinds)
        self.assertNotIn("recorded", kinds)  # faceless filtra recorded
        self.assertEqual(len(plan.dub_suggestions_json), 1)

    def test_plans_index_and_save(self):
        plan = VideoPlan.objects.create(
            niche=self.niche,
            llm_credential=self.cred,
            video_format="dark",
            topic="tema",
            title="Título",
            script_body="corpo",
            voice_name="pt-BR-FranciscaNeural-Female",
            assets_json=[{"kind": "broll", "query_or_brief": "x", "why": "y"}],
            status=VideoPlan.Status.READY,
        )
        response = self.client.get(reverse("ui:plans_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Plano de vídeo")
        self.assertContains(response, "Histórico")
        self.assertContains(response, "OpenAI")

        response = self.client.post(
            reverse("ui:plans_save", args=[plan.pk]),
            {
                "title": "Novo título",
                "topic": "tema 2",
                "script_body": "roteiro editado",
                "voice_name": "pt-BR-FranciscaNeural-Female",
                "voice_notes": "ok",
                "assets_text": "stock_image | gato | fofo",
                "dub_text": "Doc EN | https://youtu.be/x | dubar",
                "status": "ready",
            },
        )
        self.assertEqual(response.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.title, "Novo título")
        self.assertEqual(plan.script_body, "roteiro editado")
        self.assertEqual(plan.assets_json[0]["kind"], "stock_image")
        self.assertEqual(plan.dub_suggestions_json[0]["title"], "Doc EN")

    def test_export_to_script_does_not_create_job(self):
        plan = VideoPlan.objects.create(
            niche=self.niche,
            topic="tema",
            title="T",
            script_body="corpo do plano",
            video_format="face",
        )
        with patch("panel.jobs.worker") as worker:
            draft = plan_service.export_to_script_draft(plan)
            worker.assert_not_called()
        self.assertIsInstance(draft, ScriptDraft)
        self.assertEqual(draft.body, "corpo do plano")
        plan.refresh_from_db()
        self.assertEqual(plan.script_draft_id, draft.pk)

        response = self.client.post(reverse("ui:plans_to_script", args=[plan.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/roteiros/", response["Location"])

    def test_create_via_post_mocked(self):
        with (
            patch(
                "panel.ui.services.video_plans._gather_plan_signals",
                return_value={"videos": [], "dub_candidates": [], "errors": []},
            ),
            patch(
                "panel.ui.services.video_plans._call_plan_llm",
                return_value={
                    "title": "Via POST",
                    "script_body": "texto",
                    "voice_name": "pt-BR-FranciscaNeural-Female",
                    "assets": [],
                    "dub_suggestions": [],
                },
            ),
        ):
            response = self.client.post(
                reverse("ui:plans_index"),
                {
                    "niche": self.niche.pk,
                    "video_format": "dark",
                    "topic": "post tema",
                    "llm_credential": self.cred.pk,
                },
            )
        self.assertEqual(response.status_code, 302)
        plan = VideoPlan.objects.latest("id")
        self.assertEqual(plan.title, "Via POST")
        self.assertIn(f"/planos/{plan.pk}/", response["Location"])
