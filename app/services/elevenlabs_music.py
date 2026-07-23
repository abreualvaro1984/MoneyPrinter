import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import requests
from loguru import logger

from app.config import config
from app.services import bgm as bgm_service
from app.utils import utils


DEFAULT_BASE_URL = "https://api.elevenlabs.io"
VIDEO_TO_MUSIC_PATH = "/v1/music/video-to-music"
SUBSCRIPTION_PATH = "/v1/user/subscription"
DEFAULT_MODEL_ID = "music_v2"
SUPPORTED_MODEL_IDS = frozenset({"music_v1", "music_v2"})
MAX_VIDEO_DURATION_SECONDS = 600
MAX_PROMPT_LENGTH = 1000
MAX_PROXY_BYTES = 200 * 1024 * 1024
MAX_GENERATED_AUDIO_BYTES = 50 * 1024 * 1024


class ElevenLabsMusicError(RuntimeError):
    """Indica que uma solicitação de trilha sonora do ElevenLabs, geração de proxy ou verificação de áudio de retorno falhou."""


class ElevenLabsPaidPlanRequiredError(ElevenLabsMusicError):
    """Indica que a chave é válida, mas o pacote da conta atual não inclui a API ElevenLabs Music."""


class ElevenLabsAuthenticationError(ElevenLabsMusicError):
    """Indica que a chave API ElevenLabs está faltando ou foi rejeitada pelo servidor."""


def get_api_key() -> str:
    """Leia a chave de API compartilhada do ElevenLabs.

    A trilha sonora usa a mesma configuração de conta do TTS ElevenLabs existente para evitar manutenção repetida por usuários na WebUI.
    Duas cópias da Chave; variáveis ​​de ambiente são usadas apenas como fonte de backup quando a configuração local não está preenchida."""
    configured_key = str(config.elevenlabs.get("api_key", "") or "").strip()
    return configured_key or os.getenv("ELEVENLABS_API_KEY", "").strip()


def is_enabled() -> bool:
    return bool(get_api_key())


def _base_url() -> str:
    return str(
        config.elevenlabs.get("music_base_url", DEFAULT_BASE_URL)
        or DEFAULT_BASE_URL
    ).rstrip("/")


def _model_id() -> str:
    """Somente modelos atualmente expostos pelo Video-to-Music oficial são permitidos, com alternativa segura em caso de configuração incorreta."""
    model_id = str(
        config.elevenlabs.get("music_model_id", DEFAULT_MODEL_ID)
        or DEFAULT_MODEL_ID
    ).strip()
    return model_id if model_id in SUPPORTED_MODEL_IDS else DEFAULT_MODEL_ID


def _request_timeout() -> tuple[int, int]:
    """Limite o tempo limite de leitura da trilha sonora, levando em consideração a produção demorada de vídeos longos e a possibilidade de recuperação de configurações incorretas."""
    raw_timeout = config.elevenlabs.get("music_timeout", 600)
    try:
        read_timeout = float(raw_timeout)
    except (TypeError, ValueError):
        read_timeout = 600
    if not math.isfinite(read_timeout) or read_timeout <= 0:
        read_timeout = 600
    return 15, max(1, math.ceil(min(read_timeout, 1800)))


def _safe_response_error(response: requests.Response) -> str:
    """Trunque corpos de erros de terceiros, preservando informações de localização, mas não permitindo que páginas HTML poluam o log de tarefas."""
    body = (response.text or "").strip().replace("\n", " ")[:500]
    return body or response.reason or "request failed"


def test_connection() -> dict[str, Any]:
    """Use a interface de assinatura que não consome créditos de geração de música para verificar a chave API e o pacote da conta.

    Esta interface só pode confirmar que a chave pode acessar as informações da assinatura e que a conta não é um pacote gratuito, mas não pode provar que a chave atual
    Deve ter permissão de endpoint de música. ElevenLabs permite restrições por endpoint, cota e IP
    Key, portanto, o prompt de sucesso da IU deve preservar esse limite e as permissões reais ainda serão confirmadas pela solicitação de build.
    Os detalhes da fatura e do uso na resposta não serão gravados no log para evitar o registro da privacidade da conta."""
    api_key = get_api_key()
    if not api_key:
        raise ElevenLabsAuthenticationError("ElevenLabs API key is required")
    try:
        response = requests.get(
            f"{_base_url()}{SUBSCRIPTION_PATH}",
            headers={"xi-api-key": api_key},
            timeout=(15, 30),
        )
    except requests.RequestException as exc:
        raise ElevenLabsMusicError(
            f"failed to connect to ElevenLabs: {exc}"
        ) from exc
    if response.status_code == 401:
        raise ElevenLabsAuthenticationError(
            "ElevenLabs API key was rejected (401): "
            f"{_safe_response_error(response)}"
        )
    if not response.ok:
        raise ElevenLabsMusicError(
            f"ElevenLabs account check failed ({response.status_code}): "
            f"{_safe_response_error(response)}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ElevenLabsMusicError(
            "ElevenLabs returned an invalid subscription response"
        ) from exc
    if not isinstance(payload, dict):
        raise ElevenLabsMusicError(
            "ElevenLabs returned an unexpected subscription response"
        )
    tier = str(payload.get("tier") or "").strip().lower()
    if not tier:
        raise ElevenLabsMusicError(
            "ElevenLabs subscription response does not include an account tier"
        )
    if tier == "free":
        raise ElevenLabsPaidPlanRequiredError(
            "ElevenLabs Music API requires a paid plan; "
            "the current account is on the free tier"
        )
    logger.info(f"ElevenLabs account and plan check succeeded: tier={tier}")
    return payload


def validate_generation_access() -> None:
    """Elimine contas que não consigam gerar trilhas sonoras antes do início do caro pipeline de vídeo.

    Planos gratuitos e chaves inválidas são erros determinísticos e devem ser encerrados imediatamente para evitar consumir LLM e TTS primeiro.
    e cota de serviços materiais. A interface de assinatura também pode ser afetada pelo escopo do endpoint somente música, restrições de IP ou
    Inacessível devido a problema temporário de rede; esses resultados não provam que a API Music esteja indisponível, portanto, apenas um aviso é registrado,
    Continue deixando a solicitação de build real determinar o resultado e evite interceptar erros de chave restritos, mas disponíveis."""
    try:
        test_connection()
    except (ElevenLabsPaidPlanRequiredError, ElevenLabsAuthenticationError):
        raise
    except ElevenLabsMusicError as exc:
        logger.warning(
            "ElevenLabs account preflight was inconclusive; "
            f"generation will verify Music API access: error={exc}"
        )


def _remove_file(file_path: str) -> None:
    """Faça o seu melhor para limpar os arquivos intermediários do ElevenLabs e não substituir a exceção original que está sendo tratada pelo chamador."""
    if not file_path or not os.path.exists(file_path):
        return
    try:
        os.remove(file_path)
    except OSError as exc:
        logger.warning(
            "failed to remove ElevenLabs temporary file: "
            f"path={file_path}, error={exc}"
        )


def _create_video_proxy(video_path: str) -> str:
    """Gera um vídeo proxy H.264 sem trilha de áudio e 1280 pixels no lado mais longo.

    O Video-to-Music analisa apenas a imagem, o upload do filme original em alta definição não melhorará a trilha sonora nem aumentará o tráfego.
    e tempo de espera. Os proxies são estritamente limitados ao limite oficial de 200 MB e são excluídos ao final da solicitação."""
    descriptor, proxy_path = tempfile.mkstemp(
        prefix=".elevenlabs-music-proxy-",
        suffix=".mp4",
        dir=os.path.dirname(os.path.abspath(video_path)),
    )
    os.close(descriptor)
    command = [
        utils.get_ffmpeg_binary(),
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        video_path,
        "-vf",
        (
            "scale=w=1280:h=1280:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2"
        ),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "30",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        proxy_path,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _remove_file(proxy_path)
        raise ElevenLabsMusicError(
            "ElevenLabs video proxy generation timed out"
        ) from exc
    except OSError as exc:
        _remove_file(proxy_path)
        raise ElevenLabsMusicError(
            "failed to run FFmpeg for ElevenLabs video proxy"
        ) from exc
    if result.returncode != 0:
        _remove_file(proxy_path)
        detail = (result.stderr or "").strip().replace("\n", " ")[-500:]
        raise ElevenLabsMusicError(
            f"failed to generate ElevenLabs video proxy: {detail}"
        )
    proxy_size = os.path.getsize(proxy_path) if os.path.isfile(proxy_path) else 0
    if proxy_size <= 0 or proxy_size > MAX_PROXY_BYTES:
        _remove_file(proxy_path)
        raise ElevenLabsMusicError(
            "ElevenLabs video proxy is empty or exceeds the 200 MB limit"
        )
    logger.info(
        "ElevenLabs video proxy prepared: "
        f"source={video_path}, size={proxy_size} bytes"
    )
    return proxy_path


def _stream_audio(response: requests.Response, temp_audio_path: str) -> int:
    """Salve o áudio em partes e limite o tamanho máximo para evitar que respostas anormais esgotem o disco local."""
    total_bytes = 0
    with open(temp_audio_path, "wb") as output:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            total_bytes += len(chunk)
            if total_bytes > MAX_GENERATED_AUDIO_BYTES:
                raise ElevenLabsMusicError(
                    "ElevenLabs audio exceeds the 50 MB limit"
                )
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if total_bytes <= 0:
        raise ElevenLabsMusicError("ElevenLabs returned no audio data")
    return total_bytes


def _request_bgm(video_path: str, output_path: str, prompt: str) -> str:
    """Solicite a trilha sonora do ElevenLabs, baixe-a completa e passe na verificação do FFmpeg antes de lançá-la atomicamente."""
    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)
    descriptor, temp_audio_path = tempfile.mkstemp(
        prefix=".elevenlabs-music-",
        suffix=Path(output_path).suffix or ".mp3",
        dir=output_dir,
    )
    os.close(descriptor)
    try:
        model_id = _model_id()
        logger.info(
            "requesting ElevenLabs background music: "
            f"video={video_path}, model={model_id}, "
            f"prompt_provided={bool(prompt)}"
        )
        request_data = {"model_id": model_id}
        if prompt:
            request_data["description"] = prompt
        try:
            with open(video_path, "rb") as video_file:
                response = requests.post(
                    f"{_base_url()}{VIDEO_TO_MUSIC_PATH}",
                    headers={"xi-api-key": get_api_key()},
                    params={"output_format": "mp3_44100_128"},
                    files=[
                        (
                            # A documentação oficial mostra o array do formulário como ``videos[]``, mas 2026-07-18
                            # A interface de produção retornará 422 para este campo, e o parâmetro Starlette real é denominado
                            # ``vídeos``. Ao fazer upload repetidamente, as solicitações podem continuar adicionando campos com o mesmo nome.
                            "videos",
                            (Path(video_path).name, video_file, "video/mp4"),
                        )
                    ],
                    data=request_data,
                    stream=True,
                    timeout=_request_timeout(),
                )
                with response:
                    if not response.ok:
                        raise ElevenLabsMusicError(
                            "ElevenLabs generation failed "
                            f"({response.status_code}): "
                            f"{_safe_response_error(response)}"
                        )
                    total_bytes = _stream_audio(response, temp_audio_path)
        except requests.RequestException as exc:
            # A desconexão durante a fase de download também é considerada uma falha na solicitação. A lógica de downgrade da tarefa deve ser inserida e metade da mensagem não pode ser deixada.
            # O áudio pode fazer com que o vídeo gerado falhe devido a flutuações de rede de terceiros.
            raise ElevenLabsMusicError(
                f"failed to request ElevenLabs music: {exc}"
            ) from exc

        try:
            bgm_service.validate_audio_file(temp_audio_path, timeout_seconds=120)
        except (bgm_service.BgmUploadError, bgm_service.BgmServiceError) as exc:
            raise ElevenLabsMusicError(
                "ElevenLabs returned audio that FFmpeg cannot decode"
            ) from exc
        os.replace(temp_audio_path, output_path)
        temp_audio_path = ""
        logger.info(
            "ElevenLabs background music generated: "
            f"output={output_path}, size={total_bytes} bytes"
        )
        return output_path
    finally:
        _remove_file(temp_audio_path)


def generate_bgm(
    video_path: str,
    output_path: str,
    video_duration: float,
    prompt: str = "",
) -> str:
    """Gere música de fundo do ElevenLabs para um vídeo emendado que corresponda à duração e à imagem."""
    if not get_api_key():
        raise ElevenLabsMusicError("ElevenLabs API key is required")
    if not os.path.isfile(video_path):
        raise ElevenLabsMusicError("ElevenLabs input video does not exist")
    try:
        duration = float(video_duration)
    except (TypeError, ValueError) as exc:
        raise ElevenLabsMusicError(
            "ElevenLabs video duration is invalid"
        ) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ElevenLabsMusicError("ElevenLabs video duration is invalid")
    if duration > MAX_VIDEO_DURATION_SECONDS:
        raise ElevenLabsMusicError(
            "ElevenLabs supports videos up to 600 seconds"
        )
    prompt = str(prompt or "").strip()
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ElevenLabsMusicError(
            "ElevenLabs music prompt exceeds 1000 characters"
        )

    proxy_path = ""
    try:
        proxy_path = _create_video_proxy(video_path)
        return _request_bgm(proxy_path, output_path, prompt)
    except ElevenLabsMusicError:
        raise
    except OSError as exc:
        raise ElevenLabsMusicError(
            f"ElevenLabs local file operation failed: {exc}"
        ) from exc
    finally:
        _remove_file(proxy_path)
