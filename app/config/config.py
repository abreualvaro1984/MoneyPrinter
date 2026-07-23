import os
import shutil
import socket
import tempfile
import threading
from contextlib import contextmanager

import toml
from loguru import logger

from app import __version__

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
config_file = f"{root_dir}/config.toml"
_CONTAINER_CGROUP_MARKERS = ("docker", "containerd", "kubepods", "libpod", "podman")
_DOCKER_HOST_GATEWAY_NAME = "host.docker.internal"
_config_save_lock = threading.RLock()
_MISSING = object()


class _SynchronizedConfig(dict):
    """Mantenha o uso do dict inalterado e faça com que as operações de gravação da configuração do tempo de execução obedeçam ao mesmo bloqueio."""

    def __setitem__(self, key, value):
        # Streamlit reescreverá o valor de controle atual de volta à configuração sempre que a página inteira for executada novamente. realização de tarefa de vídeo
        # Quando runtime_config_lock, se o valor não mudar, esta escrita não terá efeitos colaterais e
        # A página atualizada não deve ficar presa no meio do formulário. As escritas que realmente alteram a configuração ainda vão para o bloqueio inferior,
        # Portanto, você não pode trocar de provedor, chave ou outras configurações globais no meio da geração de um vídeo.
        current = super().get(key, _MISSING)
        if current is not _MISSING and current == value:
            return
        with _config_save_lock:
            super().__setitem__(key, value)

    def __delitem__(self, key):
        with _config_save_lock:
            super().__delitem__(key)

    def clear(self):
        if not self:
            return
        with _config_save_lock:
            super().clear()

    def pop(self, key, default=_MISSING):
        # ``pop(key, default)`` também não altera a configuração quando a chave não existe. Uso da WebUI
        # Esta forma de escrever expressa "adotar a política padrão", que deve ser concluída diretamente durante a atualização.
        if key not in self:
            if default is _MISSING:
                raise KeyError(key)
            return default
        with _config_save_lock:
            if default is _MISSING:
                return super().pop(key)
            return super().pop(key, default)

    def setdefault(self, key, default=None):
        # Assim como __setitem__, setdefault para uma chave existente é uma operação somente leitura. Voltar mais cedo
        # Você pode fazer atualizações de página que leiam apenas a configuração padrão, não afetada por longos bloqueios de configuração de tarefas.
        current = super().get(key, _MISSING)
        if current is not _MISSING:
            return current
        with _config_save_lock:
            return super().setdefault(key, default)

    def update(self, *args, **kwargs):
        changes = dict(*args, **kwargs)
        if all(
            (current := dict.get(self, key, _MISSING)) is not _MISSING
            and current == value
            for key, value in changes.items()
        ):
            return
        with _config_save_lock:
            super().update(changes)


@contextmanager
def runtime_config_lock():
    """Evite que outras sessões WebUI substituam a configuração durante uma operação completa que depende da configuração global.

    O projeto atual vincula o endereço de loopback local por padrão e a configuração ainda é uma configuração global de usuário único. Esta fechadura leve principalmente
    Proteja operações longas, como geração e escuta, para evitar que outra guia troque de provedor ou chave no meio da operação."""
    with _config_save_lock:
        yield


@contextmanager
def try_runtime_config_lock():
    """Tenta adquirir um bloqueio de configuração de tempo de execução e retorna imediatamente se for bem-sucedido.

    A audição da WebUI é uma operação curta acionada pelo usuário e não deve esperar vários minutos enquanto a tarefa de vídeo em segundo plano está bloqueada.
    O chamador pode solicitar ao usuário que tente novamente mais tarde quando o bloqueio não for adquirido; após adquirir o bloqueio com sucesso, o período de escuta ainda pode ser garantido.
    A configuração do provedor, da chave e do modelo não será modificada por outras sessões."""
    acquired = _config_save_lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _config_save_lock.release()


def is_running_in_container(
    dockerenv_path: str = "/.dockerenv",
    containerenv_path: str = "/run/.containerenv",
    cgroup_path: str = "/proc/1/cgroup",
) -> bool:
    """Determine se o processo atual está em execução no contêiner.

    Este julgamento é usado principalmente para seleção de endereço padrão do Ollama:
    - Ao rodar em uma máquina local normal, `localhost` aponta para a própria máquina do usuário;
    - No contêiner Docker, `localhost` aponta para o próprio contêiner e acessa o host Ollama
      Normalmente você precisa usar `host.docker.internal`.

    Você não pode simplesmente determinar se `/proc/1/cgroup` existe, porque o Linux comum também terá este arquivo.
    Aqui, True só é retornado quando uma tag de contêiner explícita é detectada para evitar ferir acidentalmente usuários que não sejam do Docker Linux.
    Os parâmetros são reservados como caminhos injetáveis ​​para facilitar o teste de unidade e cobrir diferentes ambientes operacionais."""
    if os.path.isfile(dockerenv_path) or os.path.isfile(containerenv_path):
        return True

    try:
        with open(cgroup_path, mode="r", encoding="utf-8") as fp:
            cgroup_content = fp.read().lower()
    except OSError:
        return False

    return any(marker in cgroup_content for marker in _CONTAINER_CGROUP_MARKERS)


def _can_resolve_hostname(hostname: str) -> bool:
    try:
        socket.gethostbyname(hostname)
    except OSError:
        return False
    return True


def _decode_linux_route_gateway(hex_gateway: str) -> str:
    # O gateway em /proc/net/route é hexadecimal little endian, por exemplo, 010011AC significa
    # 172.17.0.1. Ele é analisado separadamente aqui para usá-lo quando o Linux Docker nativo não tiver
    # host.docker.internal DNS, ele também pode tentar acessar o host no gateway padrão do contêiner.
    if len(hex_gateway) != 8:
        raise ValueError("invalid gateway length")

    octets = [
        str(int(hex_gateway[index : index + 2], 16))
        for index in range(6, -1, -2)
    ]
    return ".".join(octets)


def get_container_default_gateway_ip(route_path: str = "/proc/net/route") -> str:
    """Leia o IP do gateway padrão no contêiner do Linux.

    Docker Desktop geralmente fornece `host.docker.internal`, mas o Docker nativo do Linux
    Este nome DNS não é necessariamente fornecido por padrão. O gateway padrão geralmente pode ser usado para acessar serviços de host.
    Endereço secreto; se o Ollama do usuário escuta apenas 127.0.0.1, o usuário ainda precisa deixar
    Ollama escuta a placa de rede host ou configura `ollama_base_url` manualmente."""
    try:
        with open(route_path, mode="r", encoding="utf-8") as fp:
            route_lines = fp.readlines()
    except OSError:
        return ""

    for line in route_lines[1:]:
        fields = line.strip().split()
        if len(fields) < 3:
            continue

        destination = fields[1]
        gateway = fields[2]
        if destination != "00000000" or gateway == "00000000":
            continue

        try:
            return _decode_linux_route_gateway(gateway)
        except ValueError:
            logger.warning(f"invalid container gateway route entry: {line.strip()}")
            return ""

    return ""


def get_default_ollama_base_url() -> str:
    """Retorna o base_url padrão compatível com OpenAI do Ollama.

    Os usuários não irão aqui ao configurar explicitamente `ollama_base_url`; isso só lida com "não configurado"
    Melhor padrão". O contêiner aponta para o host por padrão, e a máquina local normal é executada para localhost por padrão."""
    if not is_running_in_container():
        return "http://localhost:11434/v1"

    if _can_resolve_hostname(_DOCKER_HOST_GATEWAY_NAME):
        return f"http://{_DOCKER_HOST_GATEWAY_NAME}:11434/v1"

    gateway_ip = get_container_default_gateway_ip()
    if gateway_ip:
        logger.info(
            "host.docker.internal is not resolvable, fallback to container "
            f"default gateway for Ollama: {gateway_ip}"
        )
        return f"http://{gateway_ip}:11434/v1"

    logger.warning(
        "failed to resolve host.docker.internal and container default gateway; "
        "fallback to host.docker.internal for Ollama"
    )
    return f"http://{_DOCKER_HOST_GATEWAY_NAME}:11434/v1"


def load_config():
    # fix: IsADirectoryError: [Errno 21] Is a directory: '/MoneyPrinterTurbo/config.toml'
    if os.path.isdir(config_file):
        shutil.rmtree(config_file)

    if not os.path.isfile(config_file):
        example_file = f"{root_dir}/config.example.toml"
        if os.path.isfile(example_file):
            shutil.copyfile(example_file, config_file)
            logger.info("copy config.example.toml to config.toml")

    logger.info(f"load config from file: {config_file}")

    try:
        _config_ = toml.load(config_file)
    except Exception as e:
        logger.warning(f"load config failed: {str(e)}, try to load as utf-8-sig")
        with open(config_file, mode="r", encoding="utf-8-sig") as fp:
            _cfg_content = fp.read()
            _config_ = toml.loads(_cfg_content)
    return _config_


def save_config():
    """Salvamento atômico da configuração do tempo de execução.

    Diferentes sessões do Streamlit podem acionar salvamentos de configuração em momentos semelhantes. Ao substituir config.toml diretamente,
    Outro thread pode ler o conteúdo TOML que foi escrito apenas parcialmente. A serialização de bloqueio reentrante em processo é usada aqui
    Salve, primeiro grave no arquivo temporário no mesmo diretório e, em seguida, substitua atomicamente o arquivo de destino por meio de os.replace.

    Isso ainda mantém a semântica de configuração global de usuário único existente do projeto, sem introduzir um sistema de configuração multiusuário complexo adicional;
    Usado principalmente para evitar danos aos arquivos de configuração durante páginas com várias guias ou repetições rápidas."""
    with _config_save_lock:
        config_to_save = dict(_cfg)
        config_to_save["app"] = dict(app)
        config_to_save["azure"] = dict(azure)
        config_to_save["siliconflow"] = dict(siliconflow)
        config_to_save["elevenlabs"] = dict(elevenlabs)
        config_to_save["chatterbox"] = dict(chatterbox)
        config_to_save["ui"] = dict(ui)
        serialized_config = toml.dumps(config_to_save)

        # Save será chamado no final de uma nova execução completa do WebUI. Retorne diretamente quando o conteúdo não tiver mudado para evitar cada vez
        # Clicar em um controle normal causará gravação no disco e fsync.
        try:
            with open(config_file, mode="r", encoding="utf-8") as f:
                if f.read() == serialized_config:
                    _cfg.clear()
                    _cfg.update(config_to_save)
                    return
        except (OSError, UnicodeError):
            pass

        temp_path = ""
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix=".config-",
                suffix=".toml.tmp",
                dir=root_dir,
            )
            with os.fdopen(fd, mode="w", encoding="utf-8") as f:
                f.write(serialized_config)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, config_file)
            _cfg.clear()
            _cfg.update(config_to_save)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)


_cfg = load_config()
app = _SynchronizedConfig(_cfg.get("app", {}))
whisper = _cfg.get("whisper", {})
proxy = _cfg.get("proxy", {})
azure = _SynchronizedConfig(_cfg.get("azure", {}))
siliconflow = _SynchronizedConfig(_cfg.get("siliconflow", {}))
elevenlabs = _SynchronizedConfig(_cfg.get("elevenlabs", {}))
chatterbox = _SynchronizedConfig(_cfg.get("chatterbox", {}))
ui = _SynchronizedConfig(
    _cfg.get(
        "ui",
        {
            "hide_log": False,
        },
    )
)

hostname = socket.gethostname()

log_level = _cfg.get("log_level", "DEBUG")
listen_host = _cfg.get("listen_host", "0.0.0.0")
listen_port = _cfg.get("listen_port", 8080)
project_name = _cfg.get("project_name", "MoneyPrinterTurbo")
project_description = _cfg.get(
    "project_description",
    "<a href='https://github.com/harry0703/MoneyPrinterTurbo'>https://github.com/harry0703/MoneyPrinterTurbo</a>",
)
project_version = _cfg.get("project_version", __version__)
reload_debug = False

app["redis_host"] = os.getenv(
    "MPT_APP_REDIS_HOST",
    os.getenv("REDIS_HOST", app.get("redis_host", "localhost")),
)

ffmpeg_path = app.get("ffmpeg_path", "")
if ffmpeg_path and os.path.isfile(ffmpeg_path):
    os.environ["IMAGEIO_FFMPEG_EXE"] = ffmpeg_path

logger.info(f"{project_name} v{project_version}")
