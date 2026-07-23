import os
import threading

from loguru import logger


PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
LOG_RECORD_FORMAT = (
    "<green>{time:%Y-%m-%d %H:%M:%S}</> | "
    "<level>{level}</> | "
    '"{file.path}:{line}":<blue> {function}</> '
    "- <level>{message}</>\n"
)
# Na inicialização, o handler de terminal padrão do Loguru tem ID 0. Ao recarregar a WebUI,
# só esse handler base pode ser substituído; logger.remove() sem filtro apagaria também o
# sink temporário que coleta logs da WebUI para tarefas em execução.
_terminal_handler_id: int | None = 0
_terminal_handler_lock = threading.RLock()


def format_log_record(record):
    """
    Formata logs de terminal e WebUI de forma uniforme.

    O Loguru entrega o mesmo registro a vários sinks. O primeiro pode já ter convertido
    caminhos absolutos em relativos ao projeto; aqui aceitamos ambos e também paths com ``./``.
    O sink da WebUI desliga cores, mas hora, nível, origem e mensagem coincidem com o terminal.
    """
    file_path = record["file"].path
    if os.path.isabs(file_path):
        relative_path = os.path.relpath(file_path, PROJECT_ROOT)
        record["file"].path = f"./{relative_path}"

    # Mensagens podem incluir caminhos absolutos de arquivos de tarefa; encurtar para relativo
    # ao projeto evita duas apresentações diferentes entre WebUI e terminal.
    record["message"] = record["message"].replace(PROJECT_ROOT, ".")
    return LOG_RECORD_FORMAT


def configure_terminal_logger(sink, level: str, colorize: bool = True) -> int:
    """
    Substitui com segurança o handler de terminal do processo, preservando handlers de tarefa.

    O Streamlit pode reinicializar logs após hot reload ou invalidação de cache. Removemos
    só o handler de terminal pelo ID registrado, sem interromper sinks da WebUI em tarefas
    em background. O lock protege a atualização do ID quando várias sessões inicializam juntas.
    """
    global _terminal_handler_id

    with _terminal_handler_lock:
        if _terminal_handler_id is not None:
            try:
                logger.remove(_terminal_handler_id)
            except ValueError:
                # Testes ou outro entrypoint podem já ter removido o handler; seguimos criando
                # nova saída de terminal sem afetar outros sinks válidos.
                pass

        _terminal_handler_id = logger.add(
            sink,
            level=level,
            format=format_log_record,
            colorize=colorize,
        )
        return _terminal_handler_id
