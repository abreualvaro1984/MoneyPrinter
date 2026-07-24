from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from panel.niches.models import Niche
from panel.ui.models import LlmCredential, ScriptDraft, TrendRun, YoutubeDataApiKey
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
        with patch("panel.ui.services.ai_detect._resolve_gemini_api_key", return_value=("", "")):
            result = ai_detect.score_text(text)
        self.assertIsNotNone(result.score)
        self.assertGreaterEqual(result.score or 0, 45)
        self.assertIn(result.status, {"review", "regen", "skipped"})

    def test_gemini_score_preferred(self):
        fake_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "score": 82,
                                        "label": "ai",
                                        "reasons": ["frases genéricas"],
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }
        with (
            patch(
                "panel.ui.services.ai_detect._resolve_gemini_api_key",
                return_value=("fake-key", "env:GEMINI_API_KEY"),
            ),
            patch("panel.ui.services.ai_detect.requests.post") as post,
        ):
            post.return_value.status_code = 200
            post.return_value.json.return_value = fake_response
            post.return_value.text = "ok"
            result = ai_detect.score_text("texto qualquer para score")
        self.assertEqual(result.score, 82)
        self.assertEqual(result.status, "regen")
        self.assertEqual(result.raw.get("provider"), "gemini")


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
        with patch(
            "panel.ui.services.scripts.gather_script_evidence",
            return_value={
                "published_after": "2026-04-01T00:00:00Z",
                "cutoff_days": 90,
                "videos": [
                    {
                        "title": "Juros em alta",
                        "url": "https://youtu.be/x",
                        "view_count": 900000,
                        "description": "Selic e poupança",
                        "published_at": "2026-06-01T00:00:00Z",
                    }
                ],
                "articles": [
                    {
                        "title": "Matéria recente",
                        "url": "https://example.com/a",
                        "source": "Revista X",
                        "lang": "pt-BR",
                    }
                ],
                "errors": [],
            },
        ):
            draft = script_service.generate_script(self.niche, "Juros compostos em 60s")
        self.assertTrue(draft.body)
        self.assertEqual(draft.niche_id, self.niche.pk)
        self.assertIn(draft.ai_status, dict(ScriptDraft.AiStatus.choices))
        self.assertIn("research", draft.ai_raw)
        self.assertTrue(draft.ai_raw["research"]["videos"])

    def test_script_generate_with_credential(self):
        cred = LlmCredential.objects.create(
            name="OpenAI roteiro",
            provider="openai",
            api_key="sk-test",
            model_name="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
        )
        with patch(
            "panel.ui.services.scripts.gather_script_evidence",
            return_value={
                "published_after": "2026-04-01T00:00:00Z",
                "cutoff_days": 90,
                "videos": [],
                "articles": [],
                "errors": [],
            },
        ):
            draft = script_service.generate_script(
                self.niche, "Tema com IA", llm_credential=cred
            )
        self.assertEqual(draft.llm_credential_id, cred.pk)

    def test_scripts_index_has_llm_field(self):
        response = self.client.get(reverse("ui:scripts_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IA para o roteiro")
        self.assertContains(response, "Sugerir temas (IA)")

    def test_scripts_suggest_topics_requires_niche(self):
        response = self.client.post(reverse("ui:scripts_suggest_topics"), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selecione um nicho")

    def test_scripts_suggest_topics_ok(self):
        with patch(
            "panel.ui.services.scripts.suggest_topics",
            return_value=[
                "Tema A",
                "Tema B",
                "Tema C",
                "Tema D",
                "Tema E",
            ],
        ):
            response = self.client.post(
                reverse("ui:scripts_suggest_topics"),
                {"niche": self.niche.pk},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tema A")
        self.assertContains(response, "Tema E")
        self.assertContains(response, "data-topic")

    def test_use_topic_creates_script(self):
        run = TrendRun.objects.create(
            niche=self.niche,
            platforms=["youtube"],
            summary_pt="ok",
            topics_json=[{"title": "Tema X", "why": "y", "platform": "youtube"}],
        )
        cred = LlmCredential.objects.create(
            name="Grok roteiro",
            provider="grok",
            api_key="xai-test",
            model_name="grok-3-mini",
            base_url="https://api.x.ai/v1",
        )
        with patch(
            "panel.ui.services.scripts.gather_script_evidence",
            return_value={
                "published_after": "2026-04-01T00:00:00Z",
                "cutoff_days": 90,
                "videos": [],
                "articles": [],
                "errors": [],
            },
        ):
            response = self.client.post(
                reverse("ui:trends_use_topic", args=[run.pk]),
                {"topic": "Tema X", "llm_credential": cred.pk},
            )
        self.assertEqual(response.status_code, 302)
        draft = ScriptDraft.objects.get(topic="Tema X")
        self.assertEqual(draft.llm_credential_id, cred.pk)

    def test_humanize_rewrites_and_rescores(self):
        draft = ScriptDraft.objects.create(
            niche=self.niche,
            topic="Tema",
            title="T",
            body="Neste vídeo vamos explorar o tema com clareza absoluta.",
            target_duration_sec=60,
            ai_raw={"research": {"videos": []}},
        )
        with (
            patch(
                "panel.ui.services.scripts._llm_humanize",
                return_value="Olha, sobre o tema… te falo direto, sem enrolação.",
            ),
            patch(
                "panel.ui.services.ai_detect.score_text",
                return_value=ai_detect.ScoreResult(20.0, "pass", {"provider": "test"}),
            ),
        ):
            out = script_service.humanize_for_anti_ai(draft)
        self.assertIn("te falo direto", out.body)
        self.assertEqual(out.ai_score, 20.0)
        self.assertEqual(out.ai_status, "pass")
        self.assertIn("research", out.ai_raw)

    def test_create_duration_variant(self):
        draft = ScriptDraft.objects.create(
            niche=self.niche,
            topic="Tema",
            title="Curto",
            body="Fato um. Fato dois. Aposta minha.",
            target_duration_sec=30,
            ai_raw={"research": {"videos": [{"title": "v1"}]}},
        )
        with (
            patch(
                "panel.ui.services.scripts._llm_resize_variant",
                return_value={
                    "title": "Longo",
                    "body": "Fato um com mais contexto. Fato dois. Aposta minha.",
                    "hooks": "Hook",
                    "cta": "CTA",
                    "hashtags": "#a",
                },
            ),
            patch(
                "panel.ui.services.ai_detect.score_text",
                return_value=ai_detect.ScoreResult(30.0, "pass", {"provider": "test"}),
            ),
        ):
            variant = script_service.create_duration_variant(
                draft, target_duration_sec=480
            )
        self.assertNotEqual(variant.pk, draft.pk)
        self.assertEqual(variant.target_duration_sec, 480)
        self.assertIn("mais contexto", variant.body)
        self.assertEqual(variant.ai_raw["research"]["variant_of"], draft.pk)

    def test_gather_script_evidence_filters_old_trend_candidates(self):
        run = TrendRun.objects.create(
            niche=self.niche,
            platforms=["youtube"],
            candidates_json=[
                {
                    "video_id": "old1",
                    "url": "https://youtu.be/old1",
                    "title": "Velho",
                    "published_at": "2020-01-01T00:00:00Z",
                    "view_count": 99,
                },
                {
                    "video_id": "new1",
                    "url": "https://youtu.be/new1",
                    "title": "Novo",
                    "published_at": "2026-06-15T00:00:00Z",
                    "view_count": 50,
                    "description": "ok",
                },
            ],
        )
        with (
            patch("panel.channels.youtube.search_videos", return_value=[]),
            patch("panel.ui.services.scripts._google_news_rss", return_value=[]),
        ):
            evidence = script_service.gather_script_evidence(
                self.niche, "Tema", trend_run=run
            )
        ids = {v.get("url") for v in evidence["videos"]}
        self.assertIn("https://youtu.be/new1", ids)
        self.assertNotIn("https://youtu.be/old1", ids)


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
        self.assertContains(response, "YouTube Data API")
        self.assertContains(response, "APIs de IA")
        self.assertContains(response, reverse("ui:apis_youtube_test"))

    def test_save_youtube_api_key_to_db(self):
        response = self.client.post(
            reverse("ui:apis_youtube_save"),
            {"api_key": "AIzaSyTestKey1234567890"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(YoutubeDataApiKey.get_api_key(), "AIzaSyTestKey1234567890")

    def test_reject_gocspx_youtube_key(self):
        response = self.client.post(
            reverse("ui:apis_youtube_save"),
            {"api_key": "GOCSPX-fake-secret"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(YoutubeDataApiKey.get_api_key(), "")
        self.assertContains(response, "GOCSPX")

    def test_youtube_test_button_rejects_gocspx(self):
        response = self.client.post(
            reverse("ui:apis_youtube_test"),
            {"api_key": "GOCSPX-fake"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GOCSPX")

    def test_youtube_test_button_ok(self):
        from panel.ui.services.youtube_test import YoutubeTestResult

        with patch(
            "panel.ui.services.youtube_test.test_youtube_api_key",
            return_value=YoutubeTestResult(
                True, "YouTube OK — key válida (form), respondeu em 0.1s", 0.1
            ),
        ):
            response = self.client.post(
                reverse("ui:apis_youtube_test"),
                {"api_key": "AIzaSyFake"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "YouTube OK")

    def test_create_multiple_credentials(self):
        response = self.client.post(
            reverse("ui:apis_create"),
            {
                "provider": "moonshot",
                "model_name": "kimi-k2.5",
                "api_key": "sk-test-aaaa",
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.client.post(
            reverse("ui:apis_create"),
            {
                "provider": "openai",
                "model_name": "gpt-4o-mini",
                "api_key": "sk-test-bbbb",
                "is_default": False,
            },
        )
        self.assertEqual(LlmCredential.objects.count(), 2)
        self.assertEqual(LlmCredential.objects.filter(is_default=True).count(), 1)
        moon = LlmCredential.objects.get(provider="moonshot")
        self.assertTrue(moon.base_url)
        self.assertEqual(moon.model_name, "kimi-k2.5")
        openai = LlmCredential.objects.get(provider="openai")
        self.assertEqual(openai.model_name, "gpt-4o-mini")

    def test_create_grok_credential(self):
        response = self.client.post(
            reverse("ui:apis_create"),
            {
                "provider": "grok",
                "model_name": "grok-3-mini",
                "api_key": "xai-test-key",
                "is_default": False,
            },
        )
        self.assertEqual(response.status_code, 302)
        grok = LlmCredential.objects.get(provider="grok")
        self.assertEqual(grok.base_url, "https://api.x.ai/v1")
        self.assertEqual(grok.model_name, "grok-3-mini")
        create_page = self.client.get(reverse("ui:apis_create"))
        self.assertContains(create_page, "Grok (xAI)")
        self.assertContains(create_page, "id_model_name")
        self.assertContains(create_page, "llm-models-catalog")

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
        self.assertContains(response, "Parar / cancelar")
        self.assertContains(response, "ai-wait-cancel")

    def test_accounts_form_has_tutorial(self):
        response = self.client.get(reverse("ui:accounts_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tutorial")
