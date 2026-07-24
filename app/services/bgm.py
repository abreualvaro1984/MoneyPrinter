import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from loguru import logger

from app.utils import file_security, utils


# Streamlit permite arquivos carregados maiores por padrão, mas a música de fundo normalmente tem apenas alguns MB. Defina claramente aqui
# O limite superior no lado do servidor evita que a API ou WebUI grave completamente arquivos extremamente grandes no disco e afete tarefas de vídeo no mesmo processo.
MAX_BGM_UPLOAD_BYTES = 30 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_INTERNAL_UPLOAD_PREFIX = ".bgm-upload-"
_WINDOWS_INVALID_FILENAME_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_FILENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
# Em última análise, o MoviePy decodifica a música de fundo via FFmpeg, então não há necessidade de limitá-la artificialmente ao MP3. Só abre aqui
# Uma extensão de áudio convencional e semanticamente clara para evitar o upload por engano de contêineres de vídeo, como MP4, como música de fundo.
# A tupla também serve como fonte de dados única para o controle de upload da WebUI, portanto, não haverá inconsistência entre o front e o back-end ao adicionar ou excluir formatos posteriormente.
SUPPORTED_BGM_EXTENSIONS = (
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
)


class BgmUploadError(ValueError):
    """Indica que o arquivo enviado não atende aos requisitos de segurança ou formato para música de fundo."""


class BgmServiceError(RuntimeError):
    """Indica falha de execução no lado do servidor, como FFmpeg ou indisponibilidade do sistema de arquivos."""


def should_use_bgm(bgm_type: str | None, bgm_volume: float | None) -> bool:
    """Determine de forma unificada se a tarefa atual requer o processamento de qualquer música de fundo.

    Esta regra não tem nada a ver com a fonte específica: quando nenhuma fonte é selecionada, o volume é ilegal ou o volume não é maior que 0, aleatório,
    Fornecedores personalizados, Sonilo e futuros devem pular a análise de arquivos, a geração externa e a mixagem final.
    Colocá-lo em um serviço universal de BGM evita a duplicação de um conjunto de julgamentos de volume 0 para cada provedor adicional."""
    if not str(bgm_type or "").strip():
        return False
    try:
        normalized_volume = float(bgm_volume or 0)
    except (TypeError, ValueError):
        return False
    return math.isfinite(normalized_volume) and normalized_volume > 0


def uploaded_bgm_dir(create: bool = True) -> str:
    """Retorna o diretório persistente da música de fundo do usuário.

    As músicas integradas pertencem aos recursos de código e continuam a ser colocadas em recursos/músicas; o conteúdo carregado pelo usuário pertence aos dados de tempo de execução.
    Ele deve ser colocado no armazenamento montado do Docker. Ele pode ser retido após a reconstrução do contêiner e não poluirá o espaço de trabalho do Git."""
    return utils.storage_dir("bgm", create=create)


def _remove_staged_file(file_path: str) -> None:
    """Faça o seu melhor para limpar o upload de arquivos temporários sem substituir a exceção original que está sendo tratada pelo chamador."""
    if not file_path or not os.path.exists(file_path):
        return
    try:
        os.remove(file_path)
    except OSError as exc:
        # Arquivos temporários usam prefixos reservados e não entrarão na lista BGM; a falha na limpeza não deve causar "áudio ilegal"
        # Aguarde até que a exceção original mais precisa seja coberta, mas o caminho e os erros do sistema devem ser deixados para localização pela operação e manutenção.
        logger.warning(
            f"failed to remove staged background music: path={file_path}, "
            f"error={str(exc)}"
        )


def sanitize_upload_filename(filename: str) -> str:
    """Extraia nomes de arquivos de áudio que podem ser exibidos em várias plataformas e rejeite nomes ilegais e extensões não suportadas."""
    safe_name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if (
        not safe_name
        or safe_name in {".", ".."}
        or len(safe_name) > 255
        or any(ord(character) < 32 for character in safe_name)
        or any(character in _WINDOWS_INVALID_FILENAME_CHARS for character in safe_name)
        or safe_name.lower().startswith(_INTERNAL_UPLOAD_PREFIX)
    ):
        raise BgmUploadError("invalid background music filename")

    # O Windows reconhecerá o primeiro parágrafo antes da extensão como o nome do dispositivo, como CON.mp3 e LPT1.wav.
    # Não pode ser criado como um arquivo normal. Mesmo que o servidor acabe usando UUIDs, a rejeição antecipada de tais nomes pode
    # Certifique-se de que o comportamento de entrada da API seja consistente em diferentes plataformas.
    windows_basename = safe_name.split(".", 1)[0].rstrip(" .").upper()
    if windows_basename in _WINDOWS_RESERVED_FILENAMES:
        raise BgmUploadError("invalid background music filename")
    if Path(safe_name).suffix.lower() not in SUPPORTED_BGM_EXTENSIONS:
        supported_formats = ", ".join(
            extension.removeprefix(".").upper()
            for extension in SUPPORTED_BGM_EXTENSIONS
        )
        raise BgmUploadError(
            f"unsupported background music format; supported formats: {supported_formats}"
        )
    return safe_name


def _validate_audio(file_path: str, timeout_seconds: int = 30) -> None:
    """Use apenas o FFmpeg atualmente configurado para o projeto para verificar se o arquivo contém um fluxo de áudio totalmente decodificável.

    O projeto permite que imageio-ffmpeg forneça FFmpeg portátil. Este método de instalação não garante a existência simultânea.
    FFprobe, portanto, não pode adicionar dependências binárias independentes. `-map 0:a:0` falhará se não houver fluxo de áudio,
    `-xerror` promoverá erros de decodificação a falhas; a decodificação completa também pode interceptar arquivos criptografados ou dados aleatórios acidentalmente
    Erro de julgamento ao atingir o cabeçalho do quadro de áudio. O arquivo pode conter fluxos adicionais, como a capa do álbum, mas apenas o primeiro fluxo de áudio é verificado."""
    try:
        decoded = subprocess.run(
            [
                utils.get_ffmpeg_binary(),
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-i",
                file_path,
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BgmServiceError("FFmpeg background music validation timed out") from exc
    except OSError as exc:
        raise BgmServiceError("failed to run FFmpeg for background music validation") from exc
    if decoded.returncode != 0:
        raise BgmUploadError("uploaded file must contain a decodable audio stream")


def validate_audio_file(file_path: str, timeout_seconds: int = 120) -> None:
    """Verifique se os arquivos de áudio no disco podem ser totalmente decodificados pelo Projeto FFmpeg.

    A simulação de upload normalmente leva apenas 30 segundos; As trilhas sonoras geradas pelo Sonilo podem ter até 6 minutos de duração, portanto estão disponíveis externamente
    Reutilize a entrada com tempo limite ajustável. O serviço depende apenas do FFmpeg e não requer instalação adicional do FFprobe no sistema."""
    if not os.path.isfile(file_path) or os.path.getsize(file_path) <= 0:
        raise BgmUploadError("background music file is empty or missing")
    _validate_audio(file_path, timeout_seconds=timeout_seconds)


def _stage_bgm_upload(filename: str, source: BinaryIO) -> tuple[str, str, int]:
    """Grave o fluxo de upload em um arquivo temporário no mesmo diretório e retorne o nome do arquivo seguro, o caminho temporário e o número de bytes.

    A simulação de upload e a persistência final da WebUI devem usar exatamente as mesmas leituras em partes, limites de tamanho e nomes de arquivo
    Regras, caso contrário pode haver uma divisão de estado onde a interface exibe disponível, mas é rejeitada pelo servidor após clicar para gerar.
    Os arquivos temporários são excluídos ou substituídos atomicamente pelo chamador após a conclusão da sondagem de áudio."""
    safe_name = sanitize_upload_filename(filename)
    try:
        target_dir = uploaded_bgm_dir(create=True)
    except OSError as exc:
        raise BgmServiceError("failed to prepare background music storage") from exc
    temp_path = ""
    total_bytes = 0

    try:
        try:
            source.seek(0)
        except (AttributeError, OSError) as exc:
            raise BgmUploadError("background music upload is not seekable") from exc

        # Manter a extensão original permite que o FFmpeg escolha a correta para formatos como AAC sem cabeçalhos de contêiner.
        # desmultiplicador; os arquivos temporários ainda são colocados no diretório de destino para garantir que a operação os.replace final seja atômica.
        descriptor, temp_path = tempfile.mkstemp(
            prefix=_INTERNAL_UPLOAD_PREFIX,
            suffix=Path(safe_name).suffix.lower(),
            dir=target_dir,
        )
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = source.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise BgmUploadError("background music upload must be binary")
                total_bytes += len(chunk)
                if total_bytes > MAX_BGM_UPLOAD_BYTES:
                    raise BgmUploadError("background music file exceeds the 30 MB limit")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())

        if total_bytes == 0:
            raise BgmUploadError("background music file is empty")
        return safe_name, temp_path, total_bytes
    except Exception as exc:
        _remove_staged_file(temp_path)
        if isinstance(exc, BgmUploadError):
            raise
        if isinstance(exc, OSError):
            raise BgmServiceError("failed to stage background music upload") from exc
        raise
    finally:
        # Streamlit também precisa usar o mesmo UploadedFile para escuta do navegador; restaurar o ponteiro do arquivo pode
        # Evite que conteúdo vazio seja lido pelo jogador ou salvo final após verificação.
        try:
            source.seek(0)
        except (AttributeError, OSError):
            pass


def validate_bgm_upload(filename: str, source: BinaryIO) -> str:
    """Valida completamente o áudio enviado, mas não o persiste, usado para comprovação da WebUI antes de exibir "Pronto"."""
    safe_name, temp_path, total_bytes = _stage_bgm_upload(filename, source)
    try:
        _validate_audio(temp_path)
        logger.debug(
            f"background music upload validated: name={safe_name}, "
            f"size={total_bytes} bytes"
        )
        return safe_name
    finally:
        _remove_staged_file(temp_path)


def save_bgm_upload(filename: str, source: BinaryIO) -> str:
    """Salve a música de fundo do usuário em métodos de substituição fragmentados, limitados e atômicos.

    Os cenários de uso incluem FastAPI UploadFile e Streamlit UploadedFile, ambos fornecendo binário
    Interface de arquivo. Primeiro escreva o arquivo temporário no mesmo diretório e verifique-o e, em seguida, use os.replace para copiá-lo atomicamente para o disco, o que pode evitar
    Uploads simultâneos ou interrupções de processo deixam metade do arquivo de áudio, o que também fará com que uploads com o mesmo nome obtenham chaves de armazenamento UUID diferentes.
    Portanto, tarefas enfileiradas ou em execução sempre fazem referência ao arquivo imutável original."""
    safe_name, temp_path, total_bytes = _stage_bgm_upload(filename, source)
    stored_name = f"{uuid4().hex}{Path(safe_name).suffix.lower()}"
    target_path = os.path.join(os.path.dirname(temp_path), stored_name)

    try:
        _validate_audio(temp_path)
        try:
            os.replace(temp_path, target_path)
        except OSError as exc:
            raise BgmServiceError("failed to persist background music upload") from exc
        temp_path = ""
        logger.info(
            f"background music uploaded: original_name={safe_name}, "
            f"stored_name={stored_name}, size={total_bytes} bytes"
        )
        return stored_name
    finally:
        _remove_staged_file(temp_path)


def list_bgm_files() -> list[str]:
    """Lista músicas de fundo disponíveis carregadas pelo usuário e integradas."""
    files_by_name: dict[str, str] = {}
    for directory in (utils.song_dir(), uploaded_bgm_dir(create=True)):
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory), key=str.lower):
            # O upload da simulação e o salvamento final criarão brevemente arquivos no mesmo diretório. Embora o arquivo temporário tenha valor legal
            # A extensão de áudio ainda não foi verificada e não pode ser pré-selecionada pela lista aleatória de BGM.
            if name.startswith(_INTERNAL_UPLOAD_PREFIX):
                continue
            if Path(name).suffix.lower() not in SUPPORTED_BGM_EXTENSIONS:
                continue
            file_path = os.path.join(directory, name)
            try:
                # Os resultados da enumeração também precisam ser verificados pelo caminho real. Caso contrário, um invasor poderá colocar em um diretório permitido
                # Um link simbólico de áudio apontando para um arquivo externo e, em seguida, fornecendo-o ao MoviePy com um caminho BGM aleatório.
                resolved_path = file_security.resolve_path_within_directory(
                    directory, file_path
                )
            except ValueError as exc:
                logger.warning(
                    f"skip unsafe background music file: name={name}, error={str(exc)}"
                )
                continue
            files_by_name[name] = resolved_path
    return [files_by_name[name] for name in sorted(files_by_name, key=str.lower)]


def resolve_bgm_file(unsafe_path: str) -> str:
    """Analise o BGM no diretório de upload do usuário e no diretório de músicas integrado e rejeite caminhos fora das duas listas de permissões.

    Os nomes dos arquivos chegam primeiro ao diretório do usuário, mantendo `output000.mp3`, caminhos absolutos da lista de permissões e
    `./resource/songs/output000.mp3` e outros usos antigos. Arquivos recém-carregados usam UUID. Em circunstâncias normais
    Não haverá nomes duplicados com músicas integradas ou uploads históricos."""
    if (
        not unsafe_path
        or Path(unsafe_path).suffix.lower() not in SUPPORTED_BGM_EXTENSIONS
    ):
        raise ValueError("unsupported background music path")

    candidates = [unsafe_path]
    if not os.path.isabs(unsafe_path):
        candidates.append(os.path.join(utils.root_dir(), unsafe_path))

    last_error = ValueError("background music file does not exist")
    for directory in (uploaded_bgm_dir(create=True), utils.song_dir()):
        for candidate in candidates:
            try:
                return file_security.resolve_path_within_directory(directory, candidate)
            except ValueError as exc:
                last_error = exc
    raise ValueError(str(last_error)) from last_error
