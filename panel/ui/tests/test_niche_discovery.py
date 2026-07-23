from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from panel.ui.models import LlmCredential, NicheDiscoveryRun
from panel.ui.services import niches_discover as niche_service


class NicheDiscoverySignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("niche-ops", password="ops-pass-ok")
        self.client = Client()
        self.client.login(username="niche-ops", password="ops-pass-ok")
        self.cred = LlmCredential.objects.create(
            name="OpenAI",
            provider="openai",
            api_key="sk-test",
            model_name="gpt-5.5",
            base_url="https://api.openai.com/v1",
            is_default=True,
        )

    def test_discover_root_uses_youtube_signals_not_only_llm_opinion(self):
        fake_signals = {
            "status": "ok",
            "region": "BR",
            "published_after": "2026-07-01T00:00:00Z",
            "errors": [],
            "videos": [
                {
                    "video_id": "abc",
                    "title": "Como investir 100 reais",
                    "channel_title": "FinBR",
                    "url": "https://www.youtube.com/watch?v=abc",
                    "view_count": 2_500_000,
                    "source": "youtube_trending",
                    "query": "mostPopular:BR:all",
                }
            ],
            "collected_at": "2026-07-23T00:00:00Z",
        }
        fake_llm = {
            "summary_pt": "Finanças bombando no chart BR.",
            "niches": [
                {
                    "name": "Finanças pessoais",
                    "why": "Vídeo com 2.5M views no trending",
                    "keywords": ["investir", "reserva"],
                    "heat_score": 50,
                    "format_ok": True,
                    "format_fit": 80,
                    "format_notes": "Narrado + gráficos, sem aparecer",
                    "evidence": [
                        {
                            "title": "Como investir 100 reais",
                            "view_count": 2_500_000,
                            "url": "https://www.youtube.com/watch?v=abc",
                            "source": "youtube_trending",
                        }
                    ],
                },
                {
                    "name": "Vlog do meu dia",
                    "why": "não serve para dark",
                    "keywords": ["vlog", "meu dia"],
                    "heat_score": 90,
                    "format_ok": False,
                    "format_fit": 10,
                    "format_notes": "Exige aparecer",
                    "evidence": [],
                },
            ],
        }
        with (
            patch(
                "panel.ui.services.niches_discover.gather_hot_market_signals",
                return_value=fake_signals,
            ),
            patch(
                "panel.ui.services.niches_discover._call_llm_json",
                return_value=fake_llm,
            ),
        ):
            run = niche_service.discover_root_niches(
                llm_credential=self.cred,
                video_format="dark",
            )

        self.assertEqual(run.video_format, "dark")
        self.assertEqual(run.signals_json.get("status"), "ok")
        self.assertEqual(len(run.signals_json.get("videos") or []), 1)
        names = [n["name"] for n in run.suggestions_json]
        self.assertIn("Finanças pessoais", names)
        self.assertNotIn("Vlog do meu dia", names)
        self.assertGreaterEqual(run.suggestions_json[0]["heat_score"], 50)
        self.assertTrue(run.suggestions_json[0]["evidence"])
        self.assertGreaterEqual(run.suggestions_json[0]["format_fit"], 60)

    def test_niches_index_has_video_format_field(self):
        response = self.client.get(reverse("ui:nichos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tipo de vídeo")
        self.assertContains(response, "Dark / sem aparecer")
        self.assertContains(response, "Canal para dormir")
        self.assertContains(response, "Tela preta")

    def test_discovery_page_shows_signals(self):
        run = NicheDiscoveryRun.objects.create(
            kind=NicheDiscoveryRun.Kind.ROOT,
            video_format="dark",
            summary_pt="Resumo",
            suggestions_json=[
                {
                    "name": "Finanças",
                    "why": "quente",
                    "keywords": ["investir"],
                    "heat_score": 80,
                    "format_fit": 85,
                    "format_notes": "Narrado com gráficos",
                    "evidence": [
                        {
                            "title": "Como investir",
                            "view_count": 1000000,
                            "url": "https://www.youtube.com/watch?v=x",
                            "source": "youtube_trending",
                        }
                    ],
                }
            ],
            signals_json={
                "status": "ok",
                "region": "BR",
                "video_format_label": "Dark / sem aparecer",
                "videos": [
                    {
                        "title": "Como investir",
                        "view_count": 1000000,
                        "url": "https://www.youtube.com/watch?v=x",
                        "source": "youtube_trending",
                    }
                ],
                "errors": [],
            },
        )
        response = self.client.get(reverse("ui:niches_discovery", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sinais reais usados")
        self.assertContains(response, "Dark / sem aparecer")
        self.assertContains(response, "Narrado com gráficos")
        self.assertContains(response, "Evidências")
        self.assertContains(response, "1000000")
