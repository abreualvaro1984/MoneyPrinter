from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from panel.niches.models import Niche
from panel.ui.models import LlmCredential, NicheDiscoveryRun, TrendRun, VideoPlan


class LlmCredentialAdminUsageTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            "admin", "admin@example.com", "pass"
        )
        self.client.login(username="admin", password="pass")
        self.niche = Niche.objects.create(
            name="Sono",
            slug="sono",
            briefing="Dark sleep",
            keywords="sono\ndormir",
        )
        self.cred = LlmCredential.objects.create(
            name="Grok",
            provider="grok",
            api_key="xai-test",
            model_name="grok-4.3",
            base_url="https://api.x.ai/v1",
        )
        TrendRun.objects.create(
            niche=self.niche,
            llm_credential=self.cred,
            summary_pt="Resumo de teste do histórico no admin",
            topics_json=[],
        )
        NicheDiscoveryRun.objects.create(
            kind=NicheDiscoveryRun.Kind.ROOT,
            llm_credential=self.cred,
            video_format="dark",
            summary_pt="Nichos",
            suggestions_json=[],
        )
        VideoPlan.objects.create(
            niche=self.niche,
            llm_credential=self.cred,
            topic="Tema plano",
            title="Título plano",
        )

    def test_change_view_shows_usage_history(self):
        url = reverse("admin:ui_llmcredential_change", args=[self.cred.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Histórico de uso desta IA")
        self.assertContains(response, "1</strong> Trends")
        self.assertContains(response, "1</strong> Nichos")
        self.assertContains(response, "1</strong> Planos")
        self.assertContains(response, "Resumo de teste do histórico no admin")
        self.assertContains(response, "Título plano")
