from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from panel.niches.models import Niche
from panel.ui.models import LlmCredential, ScriptDraft, TrendRun
from panel.ui.services import ai_detect
from panel.ui.services import scripts as script_service


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
                "name": "Moonshot A",
                "provider": "moonshot",
                "api_key": "sk-test-aaaa",
                "model_name": "",
                "base_url": "",
                "is_default": True,
                "is_active": True,
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.client.post(
            reverse("ui:apis_create"),
            {
                "name": "OpenAI B",
                "provider": "openai",
                "api_key": "sk-test-bbbb",
                "model_name": "gpt-4o-mini",
                "base_url": "",
                "is_default": False,
                "is_active": True,
                "notes": "",
            },
        )
        self.assertEqual(LlmCredential.objects.count(), 2)
        self.assertEqual(LlmCredential.objects.filter(is_default=True).count(), 1)

    def test_trends_form_has_llm_field(self):
        response = self.client.get(reverse("ui:trends_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IA para a pesquisa")
