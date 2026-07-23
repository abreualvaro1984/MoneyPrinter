from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from panel.ui.models import LlmCredential
from panel.ui.services.llm_test import LlmTestResult


class LlmRuntimeImportTests(TestCase):
    def test_use_llm_credential_imports_lock_from_config_module(self):
        from panel.ui.services.llm_runtime import use_llm_credential

        cred = LlmCredential(
            name="t",
            provider="openai",
            api_key="sk-x",
            model_name="gpt-5.5",
            base_url="https://api.openai.com/v1",
        )
        # Não deve levantar ImportError ao entrar no context manager.
        with patch("panel.ui.services.llm_runtime.ensure_repo_on_path"):
            with use_llm_credential(cred):
                pass


class LlmApiTestEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ops-test", password="ops-pass-ok")
        self.client = Client()
        self.client.login(username="ops-test", password="ops-pass-ok")
        self.cred = LlmCredential.objects.create(
            name="OpenAI",
            provider="openai",
            api_key="sk-test",
            model_name="gpt-5.5",
            base_url="https://api.openai.com/v1",
        )

    def test_apis_test_handles_runtime_without_importerror(self):
        with patch(
            "panel.ui.services.llm_test.test_llm_credential",
            return_value=LlmTestResult(True, "API OK — respondeu em 0.1s", 0.1),
        ):
            response = self.client.post(reverse("ui:apis_test", args=[self.cred.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "API OK")
