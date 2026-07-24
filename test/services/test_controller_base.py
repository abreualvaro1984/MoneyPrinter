import unittest
from types import SimpleNamespace

from app.config import config
from app.controllers import base
from app.controllers.v1.base import new_router
from app.models.exception import HttpException


class TestControllerAuthentication(unittest.TestCase):
    def setUp(self):
        self.original_app_config = dict(config.app)

    def tearDown(self):
        config.app.clear()
        config.app.update(self.original_app_config)

    @staticmethod
    def _request(headers=None):
        return SimpleNamespace(
            headers=headers or {},
            url="http://localhost/api/v1/tasks",
        )

    def test_get_task_id_reuses_header_or_generates_uuid(self):
        """
        Quando o cliente fornece o ID da solicitação, ele precisa ser mantido intacto. Caso falte, será gerado e poderá ser registrado no log e
        O UUID na resposta de erro garante que ambas as entradas tenham identificadores rastreáveis.
        """
        self.assertEqual(
            base.get_task_id(self._request({"x-task-id": "request-123"})),
            "request-123",
        )

        generated = base.get_task_id(self._request())
        self.assertEqual(len(generated), 36)
        self.assertEqual(generated.count("-"), 4)

    def test_verify_token_accepts_matching_key(self):
        """Quando a API Key é configurada, o mesmo cabeçalho da solicitação deve passar pela autenticação normalmente."""
        config.app["api_key"] = "secret"

        result = base.verify_token(self._request({"x-api-key": "secret"}))

        self.assertIsNone(result)

    def test_verify_token_rejects_missing_or_wrong_key(self):
        """
        As chaves de API ausentes e incorretas devem retornar 401 e reter o ID da solicitação do cliente.
        Isso evita que falhas de autenticação não correspondam à solicitação do chamador no log.
        """
        config.app["api_key"] = "secret"

        for provided_key in (None, "wrong"):
            with self.subTest(provided_key=provided_key):
                headers = {"x-task-id": "auth-request"}
                if provided_key is not None:
                    headers["x-api-key"] = provided_key

                with self.assertRaises(HttpException) as raised:
                    base.verify_token(self._request(headers))

                self.assertEqual(raised.exception.status_code, 401)
                self.assertIn("invalid token", raised.exception.message)

    def test_new_router_preserves_common_prefix_and_dependencies(self):
        """Todas as rotas V1 devem reutilizar o mesmo prefixo e definir dependências de autenticação apenas no tráfego de entrada."""
        dependency = object()

        plain_router = new_router()
        protected_router = new_router(dependencies=[dependency])

        self.assertEqual(plain_router.prefix, "/api/v1")
        self.assertEqual(plain_router.tags, ["V1"])
        self.assertEqual(protected_router.dependencies, [dependency])


if __name__ == "__main__":
    unittest.main()
