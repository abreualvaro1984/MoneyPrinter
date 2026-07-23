import json
import unittest
from unittest.mock import MagicMock, patch

from app.controllers.manager.base_manager import TaskQueueFullError
from app.controllers.manager.memory_manager import InMemoryTaskManager
from app.controllers.manager.redis_manager import RedisTaskManager
from app.models.schema import VideoParams
from app.services import task as task_service


class TestInMemoryTaskManager(unittest.TestCase):
    def test_queue_operations_preserve_task_payload(self):
        """A fila de memória deve conter funções, argumentos posicionais e argumentos de palavras-chave e não deve alterar o conteúdo da tarefa."""
        manager = InMemoryTaskManager(max_concurrent_tasks=1, max_queued_tasks=2)
        task = {"func": len, "args": ([1, 2],), "kwargs": {}}

        manager.enqueue(task)

        self.assertFalse(manager.is_queue_empty())
        self.assertEqual(manager.queue_size(), 1)
        self.assertEqual(manager.dequeue(), task)
        self.assertTrue(manager.is_queue_empty())

    def test_add_task_rejects_only_after_queue_limit(self):
        """Após o esgotamento da cota simultânea, o enfileiramento será permitido até o limite superior. Um erro claro será retornado somente quando o limite superior for excedido."""
        manager = InMemoryTaskManager(max_concurrent_tasks=0, max_queued_tasks=1)

        manager.add_task(len, [1])

        with self.assertRaises(TaskQueueFullError):
            manager.add_task(len, [2])

    def test_add_task_reserves_slot_before_background_thread_runs(self):
        """
        A cota simultânea deve ser reservada antes do thread ser iniciado; mesmo que o thread simulado ainda não tenha entrado em run_task,
        A segunda solicitação também deve ser enfileirada e max_concurrent_tasks não pode ser excedido.
        """
        manager = InMemoryTaskManager(max_concurrent_tasks=1, max_queued_tasks=1)

        with patch.object(manager, "execute_task") as execute_task:
            manager.add_task(len, [1])
            manager.add_task(len, [2])

        self.assertEqual(manager.current_tasks, 1)
        execute_task.assert_called_once_with(len, [1])
        self.assertEqual(manager.queue_size(), 1)

    def test_add_task_rolls_back_slot_when_thread_cannot_start(self):
        """A falha na inicialização do thread não pode ocupar permanentemente a cota simultânea e as exceções ainda devem ser tratadas pelo chamador."""
        manager = InMemoryTaskManager(max_concurrent_tasks=1)

        with patch.object(
            manager,
            "execute_task",
            side_effect=RuntimeError("thread unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                manager.add_task(len, [1])

        self.assertEqual(manager.current_tasks, 0)

    def test_task_done_starts_next_queued_task(self):
        """Após o término da tarefa atual, a cota simultânea deverá ser liberada e a próxima tarefa na fila deverá ser agendada imediatamente."""
        manager = InMemoryTaskManager(max_concurrent_tasks=1, max_queued_tasks=2)
        manager.current_tasks = 1
        manager.enqueue({"func": len, "args": ([1, 2],), "kwargs": {}})

        with patch.object(manager, "execute_task") as execute_task:
            manager.task_done()

        self.assertEqual(manager.current_tasks, 1)
        execute_task.assert_called_once_with(len, [1, 2])
        self.assertTrue(manager.is_queue_empty())

    def test_task_done_requeues_task_when_thread_cannot_start(self):
        """Se o encadeamento falhar ao iniciar após a remoção da fila, a cota deverá ser revertida e a tarefa retornada à fila para evitar a perda da tarefa."""
        manager = InMemoryTaskManager(max_concurrent_tasks=1, max_queued_tasks=1)
        manager.current_tasks = 1
        queued_task = {"func": len, "args": ([1, 2],), "kwargs": {}}
        manager.enqueue(queued_task)

        with patch.object(
            manager,
            "execute_task",
            side_effect=RuntimeError("thread unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                manager.task_done()

        self.assertEqual(manager.current_tasks, 0)
        self.assertEqual(manager.dequeue(), queued_task)

    def test_run_task_releases_slot_after_failure(self):
        """Quando a função de tarefa lança uma exceção, finalmente ainda deve liberar a cota para evitar o bloqueio permanente da fila."""
        manager = InMemoryTaskManager(max_concurrent_tasks=1)
        manager.current_tasks = 1

        with patch.object(manager, "task_done") as task_done:
            with self.assertRaisesRegex(RuntimeError, "task failed"):
                manager.run_task(MagicMock(side_effect=RuntimeError("task failed")))

        self.assertEqual(manager.current_tasks, 1)
        task_done.assert_called_once_with()

    def test_execute_task_starts_background_thread(self):
        """A entrada de execução da tarefa deve iniciar o thread e passar os parâmetros completos da função para run_task."""
        manager = InMemoryTaskManager(max_concurrent_tasks=1)
        fake_thread = MagicMock()

        with patch(
            "app.controllers.manager.base_manager.threading.Thread",
            return_value=fake_thread,
        ) as thread:
            manager.execute_task(len, [1, 2])

        thread.assert_called_once_with(
            target=manager.run_task,
            args=(len, [1, 2]),
            kwargs={},
        )
        fake_thread.start.assert_called_once_with()


class TestRedisTaskManager(unittest.TestCase):
    def setUp(self):
        self.redis_client = MagicMock()
        patcher = patch(
            "app.controllers.manager.redis_manager.redis.Redis.from_url",
            return_value=self.redis_client,
        )
        self.addCleanup(patcher.stop)
        from_url = patcher.start()
        self.manager = RedisTaskManager(
            max_concurrent_tasks=1,
            redis_url="redis://localhost:6379/0",
            max_queued_tasks=3,
        )
        from_url.assert_called_once_with("redis://localhost:6379/0")

    def test_enqueue_serializes_video_params_without_mutating_task(self):
        """
        Redis Somente JSON pode ser armazenado; VideoParams deve ser convertido em um dicionário, mas a tarefa original ainda precisa reter o modelo.
        Evite efeitos colaterais de serialização que afetem o registro em log, novas tentativas ou leituras subsequentes pelo chamador.
        """
        params = VideoParams(video_subject="Coffee")
        task = {
            "func": task_service.start,
            "args": (),
            "kwargs": {"task_id": "task-1", "params": params},
        }

        self.manager.enqueue(task)

        self.assertIs(task["kwargs"]["params"], params)
        queue_name, payload = self.redis_client.rpush.call_args.args
        decoded = json.loads(payload)
        self.assertEqual(queue_name, "task_queue")
        self.assertEqual(decoded["func"], "start")
        self.assertEqual(decoded["kwargs"]["task_id"], "task-1")
        self.assertEqual(decoded["kwargs"]["params"]["video_subject"], "Coffee")

    def test_dequeue_restores_function_and_video_params(self):
        """As tarefas obtidas do Redis devem restaurar funções que podem ser chamadas e modelos VideoParams."""
        payload = {
            "func": "start",
            "args": [],
            "kwargs": {
                "task_id": "task-1",
                "params": VideoParams(video_subject="Coffee").model_dump(
                    warnings=False
                ),
            },
        }
        self.redis_client.lpop.return_value = json.dumps(payload)

        task = self.manager.dequeue()

        self.redis_client.lpop.assert_called_once_with("task_queue")
        self.assertIs(task["func"], task_service.start)
        self.assertIsInstance(task["kwargs"]["params"], VideoParams)
        self.assertEqual(task["kwargs"]["params"].video_subject, "Coffee")

    def test_empty_queue_and_size_use_redis_length(self):
        """A verificação e o comprimento da fila vazia devem refletir diretamente o comprimento atual da lista do Redis."""
        self.redis_client.lpop.return_value = None
        self.redis_client.llen.side_effect = [0, 2]

        self.assertIsNone(self.manager.dequeue())
        self.assertTrue(self.manager.is_queue_empty())
        self.assertEqual(self.manager.queue_size(), 2)


if __name__ == "__main__":
    unittest.main()
