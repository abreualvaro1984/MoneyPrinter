from fastapi import APIRouter, Request

router = APIRouter()


@router.get(
    "/ping",
    tags=["Health Check"],
    description="Verifica a disponibilidade do serviço",
    response_description="pong",
)
def ping(request: Request) -> str:
    return "pong"
