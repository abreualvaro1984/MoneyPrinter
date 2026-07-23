from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from panel.niches.models import Niche
from panel.ui.models import LlmCredential, ScriptDraft, TrendRun
from panel.ui.services import ai_detect
from panel.ui.services import scripts as script_service
from panel.ui.services.llm_test import LlmTestResult


class AiDetectTests(TestCase):
    def test_heuristic_flags_llmish_text(self):
        text = (
            "Neste vídeo vamos explorar o tema. "
            "É importante ressaltar que, além disso, em conclusão tudo importa. "
            "Vale ressaltar o ponto final com clareza absoluta e estrutura uniforme demais."
        )
        result = ai_detect.score_text(text)
        self.assertIsNotNone(result.score)
        self.assertGreaterEqual(result.score or 0, 45)
        self.assertIn(result.status, {"review", "regen", "skipped"})


class UiFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ops", password="ops-pass")
        self.niche = Niche.objects.create(
            name="Finanças",
            slug="financas",
            keywords="juros\ninvestimentos",
            briefing="Tom direto, público BR.",
        )
        self.client = Client()
        self.client.login(username="ops", password="ops-pass")

    def test_home_requires_login_redirect(self):
        anon = Client()
        response = anon.get(reverse("ui:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_home_ok(self):
        response = self.client.get(reverse("ui:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Trends")

    def test_trends_page(self):
        response = self.client.get(reverse("ui:trends_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pesquisar trends")

    def test_script_generate_fallback(self):
        draft = script_service.generate_script(self.niche, "Juros compostos em 60s")
        self.assertTrue(draft.body)
        self.assertEqual(draft.niche_id, self.niche.pk)
        self.assertIn(draft.ai_status, dict(ScriptDraft.AiStatus.choices))

    def test_use_topic_creates_script(self):
        run = TrendRun.objects.create(
            niche=self.niche,
            platforms=["youtube"],
            summary_pt="ok",
            topics_json=[{"title": "Tema X", "why": "y", "platform": "youtube"}],
        )
        response = self.client.post(
            reverse("ui:trends_use_topic", args=[run.pk]),
            {"topic": "Tema X"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ScriptDraft.objects.filter(topic="Tema X").exists())


class RegisterTests(TestCase):
    def test_register_page(self):
        response = Client().get(reverse("register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Criar conta")

    def test_register_uses_bcrypt(self):
        response = Client().post(
            reverse("register"),
            {
                "username": "novo_user",
                "email": "novo@example.com",
                "password1": "SenhaForte123!",
                "password2": "SenhaForte123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="novo_user")
        self.assertTrue(user.password.startswith("bcrypt"))
        self.assertTrue(user.check_password("SenhaForte123!"))


class LlmApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ops2", password="ops-pass-ok")
        self.client = Client()
        self.client.login(username="ops2", password="ops-pass-ok")

    def test_apis_page(self):
        response = self.client.get(reverse("ui:apis_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "APIs de IA")

    def test_create_multiple_credentials(self):
        response = self.client.post(
            reverse("ui:apis_create"),
            {
                "provider": "moonshot",
                "api_key": "sk-test-aaaa",
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.client.post(
            reverse("ui:apis_create"),
            {
                "provider": "openai",
                "api_key": "sk-test-bbbb",
                "is_default": False,
            },
        )
        self.assertEqual(LlmCredential.objects.count(), 2)
        self.assertEqual(LlmCredential.objects.filter(is_default=True).count(), 1)
        moon = LlmCredential.objects.get(provider="moonshot")
        self.assertTrue(moon.base_url)
        self.assertTrue(moon.model_name)

    def test_apis_test_button_saved_credential(self):
        cred = LlmCredential.objects.create(
            name="Kimi",
            provider="moonshot",
            api_key="sk-test",
            model_name="kimi-k2.7-code",
            base_url="https://api.moonshot.ai/v1",
        )
        with patch(
            "panel.ui.services.llm_test.test_llm_credential",
            return_value=LlmTestResult(True, "API OK — respondeu em 0.1s", 0.1),
        ):
            response = self.client.post(reverse("ui:apis_test", args=[cred.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "API OK")

    def test_apis_test_live_requires_key(self):
        response = self.client.post(
            reverse("ui:apis_test_live"),
            {"provider": "openai", "api_key": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cole a API key")

    def test_apis_index_has_test_button(self):
        LlmCredential.objects.create(
            name="OpenAI",
            provider="openai",
            api_key="sk-x",
            model_name="gpt-5.5",
        )
        response = self.client.get(reverse("ui:apis_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Testar")

    def test_trends_form_has_llm_field(self):
        response = self.client.get(reverse("ui:trends_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IA para a pesquisa")

    def test_niches_page_and_add(self):
        response = self.client.get(reverse("ui:nichos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pesquisar nichos")
        self.assertContains(response, "js-ai-wait")
        self.assertContains(response, "ai-wait")

    def test_accounts_form_has_tutorial(self):
        response = self.client.get(reverse("ui:accounts_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tutorial")
