import json
from typing import Dict

import redis

from app.controllers.manager.base_manager import TaskManager
from app.models.schema import VideoParams
from app.services import task as tm

FUNC_MAP = {
    "start": tm.start,
    # 'start_test': tm.start_test
}


class RedisTaskManager(TaskManager):
    def __init__(
        self,
        max_concurrent_tasks: int,
        redis_url: str,
        max_queued_tasks: int = 100,
    ):
        self.redis_client = redis.Redis.from_url(redis_url)
        super().__init__(max_concurrent_tasks, max_queued_tasks=max_queued_tasks)

    def create_queue(self):
        return "task_queue"

    def enqueue(self, task: Dict):
        task_with_serializable_params = task.copy()
        # task.copy() copia apenas o dicionário mais externo; se você reescrever diretamente os kwargs aninhados, o chamador irá
        # Os VideoParams retidos são substituídos de forma síncrona por dict. Logs ou tentativas subsequentes ainda poderão ler a tarefa original.
        # Portanto, os kwargs são copiados aqui separadamente para garantir que não haja efeitos colaterais indesejados durante o processo de serialização.
        task_kwargs = task.get("kwargs", {})
        task_with_serializable_params["kwargs"] = task_kwargs.copy()

        if "params" in task_kwargs and isinstance(task_kwargs["params"], VideoParams):
            task_with_serializable_params["kwargs"]["params"] = task_kwargs[
                "params"
            ].model_dump(warnings=False)

        # Converter um objeto de função em seu nome
        task_with_serializable_params["func"] = task["func"].__name__
        self.redis_client.rpush(self.queue, json.dumps(task_with_serializable_params))

    def dequeue(self):
        task_json = self.redis_client.lpop(self.queue)
        if task_json:
            task_info = json.loads(task_json)
            # Converter o nome da função de volta em objeto de função
            task_info["func"] = FUNC_MAP[task_info["func"]]

            if "params" in task_info["kwargs"] and isinstance(
                task_info["kwargs"]["params"], dict
            ):
                task_info["kwargs"]["params"] = VideoParams(
                    **task_info["kwargs"]["params"]
                )

            return task_info
        return None

    def is_queue_empty(self):
        return self.redis_client.llen(self.queue) == 0

    def queue_size(self):
        return self.redis_client.llen(self.queue)
