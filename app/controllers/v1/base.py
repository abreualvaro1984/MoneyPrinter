from fastapi import APIRouter


def new_router(dependencies=None):
    router = APIRouter()
    router.tags = ["V1"]
    router.prefix = "/api/v1"
    # Aplica dependências de autenticação a todas as rotas
    if dependencies:
        router.dependencies = dependencies
    return router
