import math
import os
import re
import socket
import threading
import time
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from functools import partial
from os import path
from uuid import uuid4

from loguru import logger

from app.config import config
from app.models import const
from app.models.schema import VideoConcatMode, VideoParams
from app.services import bgm as bgm_service
from app.services import (
    elevenlabs_music,
    llm,
    material,
    sonilo,
    subtitle,
    twelvelabs,
    video,
    voice,
)
from app.services import upload_post
from app.services import state as sm
from app.utils import file_security, utils


# A solicitação de publicação pode esperar vários minutos e não pode continuar a ocupar a cota simultânea da tarefa de geração de vídeo.
# Um pool de threads de tamanho fixo limita a taxa de transferência de publicação dentro de uma faixa controlável, ao mesmo tempo que permite que produtos de vídeo sejam gerados após
# Entre imediatamente no estado de conclusão.
_cross_post_executor = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="mpt-cross-post",
)
_cross_post_max_pending_tasks = max(
    1,
    int(config.app.get("upload_post_max_pending_tasks", 10)),
)
_cross_post_slots = threading.BoundedSemaphore(_cross_post_max_pending_tasks)
_cross_post_registry_lock = threading.RLock()
_cross_post_futures: dict[str, Future] = {}
_cross_post_process_owner = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"
_ACTIVE_CROSS_POST_STATES = {
    const.CROSS_POST_STATE_PENDING,
    const.CROSS_POST_STATE_PROCESSING,
}
_CROSS_POST_STATE_WRITE_ATTEMPTS = 3
_CROSS_POST_STATE_RETRY_DELAY_SECONDS = 0.1
_INTERRUPTED_CROSS_POST_ERROR = (
    "cross-posting was interrupted before the process completed"
)
# O serviço de trilha sonora de vídeo só precisa implementar ``is_enabled`` e ``generate_bgm``. As diferenças entre fornecedores concentram-se em
# Extensões de arquivo, exceções de domínio e códigos de aviso WebUI; orquestração de tarefas, curto-circuito de volume 0 e degradação de falhas
# Todos reutilizam o mesmo caminho para evitar a manutenção de vários processos semelhantes ao adicionar novos fornecedores posteriormente.
_VIDEO_MUSIC_PROVIDERS = {
    "sonilo": {
        "service": sonilo,
        "error_type": sonilo.SoniloError,
        "suffix": ".m4a",
        "warning_code": "sonilo_bgm_failed",
        "display_name": "Sonilo",
    },
    "elevenlabs": {
        "service": elevenlabs_music,
        "error_type": elevenlabs_music.ElevenLabsMusicError,
        "suffix": ".mp3",
        "warning_code": "elevenlabs_bgm_failed",
        "display_name": "ElevenLabs",
    },
}


def _get_video_music_prompt(params: VideoParams) -> str:
    """Leia as palavras reais usadas pelo provedor de trilha sonora de vídeo atual.

    Novas tarefas usam uniformemente campos neutros em relação ao fornecedor; antigos parâmetros CLI do Sonilo e tarefas históricas ainda podem apenas
    ``sonilo_bgm_prompt``, portanto o campo antigo só é lido se o campo comum do Sonilo estiver vazio."""
    prompt = str(params.video_music_prompt or "").strip()
    if params.bgm_type == "sonilo" and not prompt:
        prompt = str(params.sonilo_bgm_prompt or "").strip()
    return prompt


def is_task_busy(task: dict | None) -> bool:
    """Determine se a tarefa ainda está sendo gerada ou liberada para reutilização por todas as entradas de exclusão."""
    if not task:
        return False

    state = task.get("state")
    try:
        state = int(state)
    except (TypeError, ValueError):
        pass

    # Tanto a geração de vídeo quanto a publicação multiplataforma podem continuar lendo o diretório de tarefas. unificado como um estado ocupado,
    # Isso pode evitar a ocorrência de um permitindo a exclusão e outro proibindo as regras após a API e a WebUI manterem as regras separadamente.
    # Comportamento inconsistente removido.
    return (
        state == const.TASK_STATE_PROCESSING
        or task.get("cross_post_state") in _ACTIVE_CROSS_POST_STATES
    )


def _register_cross_post_future(task_id: str, future: Future) -> None:
    """Registre o futuro publicado mantido pelo processo atual para recuperação e teste de inicialização para determinar o status real de execução."""
    with _cross_post_registry_lock:
        _cross_post_futures[task_id] = future


def _unregister_cross_post_future(task_id: str, future: Future | None = None) -> None:
    """Apenas o Future correspondente é removido para evitar que retornos de chamada antigos excluam acidentalmente novos trabalhos registrados posteriormente com a mesma tarefa."""
    with _cross_post_registry_lock:
        current = _cross_post_futures.get(task_id)
        if current is None or (future is not None and current is not future):
            return
        _cross_post_futures.pop(task_id, None)


def _is_cross_post_active_in_process(task_id: str) -> bool:
    """Determine se o processo atual ainda contém tarefas de publicação inacabadas."""
    with _cross_post_registry_lock:
        future = _cross_post_futures.get(task_id)
        return future is not None and not future.done()


def _is_windows_process_alive(process_id: int) -> bool:
    """Determine o status do processo por meio da API Win32 somente leitura para evitar o encerramento acidental do processo com os.kill."""
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # ctypes trata valores de retorno não declarados como inteiros de 32 bits por padrão. O identificador de processo do Windows de 64 bits pode
    # Portanto, é truncado e a assinatura da função Win32 deve ser declarada explicitamente antes da chamada.
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        process_id,
    )
    if not handle:
        error_code = ctypes.get_last_error()
        if error_code == error_invalid_parameter:
            return False
        if error_code == error_access_denied:
            # Quando um processo existe, mas o usuário atual não tem permissões de consulta, ele deve ser considerado conservadoramente como ativo para evitar erros.
            # Recicle tarefas de publicação executadas por outras contas.
            return True
        logger.warning(
            "failed to open cross-post owner process on Windows, "
            f"process_id: {process_id}, error_code: {error_code}"
        )
        return True

    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            error_code = ctypes.get_last_error()
            logger.warning(
                "failed to read cross-post owner process state on Windows, "
                f"process_id: {process_id}, error_code: {error_code}"
            )
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _is_cross_post_owner_alive(owner: str | None) -> bool:
    """Determine se o processo nativo da tarefa de publicação persistente ainda existe."""
    if not owner:
        return False

    try:
        hostname, process_id_text, _ = owner.split(":", 2)
        process_id = int(process_id_text)
    except (TypeError, ValueError):
        logger.warning(f"invalid cross-post owner metadata: {owner}")
        return False

    # Os processos em outros hosts não podem ser detectados de forma confiável. Seja conservador em implantações multi-host que compartilham Redis
    # Ele é considerado ainda em execução para evitar que o nó atual exclua acidentalmente o arquivo de vídeo que está sendo lido por outro nó.
    if hostname != socket.gethostname():
        return True

    # Se ainda há trabalho de publicação real no processo atual foi determinado com precisão pelo registro Futuro. corra para
    # Isso mostra que não há Future correspondente no registro. Mesmo que o proprietário seja totalmente consistente com o processo atual, ele deve
    # Tratado como interrompido; isso pode abranger cenários em que as gravações do estado final continuam falhando e o Futuro terminou.
    if process_id == os.getpid():
        return False

    # Os.kill(pid, 0) do Windows tem semântica diferente do POSIX e pode encerrar diretamente o processo de destino.
    # Use a API Win32 que solicita apenas permissões de consulta e não envia nenhum sinal ao processo de destino.
    if os.name == "nt":
        return _is_windows_process_alive(process_id)

    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        logger.warning(
            f"failed to inspect cross-post owner process, owner: {owner}, error: {exc}"
        )
        return True
    return True


def _mark_task_failed(task_id: str, stage: str, error: str) -> dict:
    """Registre informações estruturadas sobre falhas e retenha o progresso alcançado antes da falha da tarefa."""
    existing_task = None
    try:
        existing_task = sm.state.get_task(task_id)
    except Exception as exc:
        logger.warning(f"failed to read task state before failure update: {exc}")

    # Funções de serviço concretas geralmente têm causas de erros mais precisas do que a camada de orquestração. Verificações subsequentes de resultados vazios
    # Ele não pode mais ser substituído por uma cópia genérica, caso contrário, os chamadores da API ainda verão apenas informações obscuras.
    if (
        existing_task
        and existing_task.get("state") == const.TASK_STATE_FAILED
        and existing_task.get("error")
    ):
        return existing_task

    message = str(error or "unknown task error").strip()
    progress = int((existing_task or {}).get("progress", 0) or 0)
    logger.error(
        f"task failed, task_id: {task_id}, stage: {stage}, error: {message}"
    )
    failure = {
        "task_id": task_id,
        "state": const.TASK_STATE_FAILED,
        "progress": progress,
        "failed_stage": stage,
        "error": message,
    }
    sm.state.update_task(
        task_id,
        state=failure["state"],
        progress=failure["progress"],
        failed_stage=failure["failed_stage"],
        error=failure["error"],
    )
    return failure


def generate_script(task_id, params):
    logger.info("\n\n## generating video script")
    video_script = params.video_script.strip()
    if not video_script:
        video_script = llm.generate_script(
            video_subject=params.video_subject,
            language=params.video_language,
            paragraph_number=params.paragraph_number,
            video_script_prompt=params.video_script_prompt,
            custom_system_prompt=params.custom_system_prompt,
        )
    else:
        logger.debug(f"video script: \n{video_script}")

    if not video_script:
        _mark_task_failed(task_id, "script", "failed to generate video script")
        return None

    return video_script


def generate_terms(task_id, params, video_script):
    logger.info("\n\n## generating video terms")
    video_terms = params.video_terms
    if not video_terms:
        # Após os materiais serem ativados e combinados na ordem de redação, as próprias palavras-chave também devem ser geradas na ordem narrativa do roteiro;
        # Caso contrário, mesmo que você baixe e junte sequencialmente no futuro, só poderá reutilizar um conjunto de palavras-chave globais.
        # O problema de “a tela do seguinte conteúdo aparece mais cedo” não pode ser melhorado.
        video_terms = llm.generate_terms(
            video_subject=params.video_subject,
            video_script=video_script,
            amount=8 if params.match_materials_to_script else 5,
            match_script_order=params.match_materials_to_script,
        )
    else:
        if isinstance(video_terms, str):
            video_terms = [term.strip() for term in re.split(r"[,，]", video_terms)]
        elif isinstance(video_terms, list):
            video_terms = [term.strip() for term in video_terms]
        else:
            raise ValueError("video_terms must be a string or a list of strings.")

        logger.debug(f"video terms: {utils.to_json(video_terms)}")

    if not video_terms:
        _mark_task_failed(
            task_id,
            "terms",
            "failed to generate video search terms",
        )
        return None

    # Reordenação semântica opcional do TwelveLabs Marengo: retorna a ordem original quando não habilitada, sem quaisquer efeitos colaterais.
    # No modo de correspondência sequencial, a ordem das palavras-chave em si é a ordem narrativa do roteiro e deve permanecer a mesma, por isso é ignorada.
    if not params.match_materials_to_script:
        video_terms = twelvelabs.rerank_terms_by_subject(
            video_subject=params.video_subject,
            search_terms=video_terms,
        )

    return video_terms


def save_script_data(task_id, video_script, video_terms, params):
    script_file = path.join(utils.task_dir(task_id), "script.json")
    script_data = {
        "script": video_script,
        "search_terms": video_terms,
        "params": params,
    }

    with open(script_file, "w", encoding="utf-8") as f:
        f.write(utils.to_json(script_data))


def resolve_custom_audio_file(task_id: str, custom_audio_file: str | None) -> str:
    requested_file = (custom_audio_file or "").strip()
    if not requested_file:
        return ""

    task_dir = utils.task_dir(task_id)
    try:
        return file_security.resolve_path_within_directory(
            task_dir,
            requested_file,
        )
    except ValueError as exc:
        task_dir_error = exc

    server_audio_file = path.realpath(
        requested_file
        if path.isabs(requested_file)
        else path.join(utils.root_dir(), requested_file)
    )
    if not path.isabs(requested_file):
        project_root = path.realpath(utils.root_dir())
        try:
            if path.commonpath([project_root, server_audio_file]) != project_root:
                raise ValueError(
                    "relative custom audio paths must stay within the project directory"
                )
        except ValueError as exc:
            raise ValueError(
                "custom audio file must be task-local or an existing server-side file"
            ) from exc

    if not path.isfile(server_audio_file):
        raise ValueError(
            "custom audio file does not exist or is not a file"
        ) from task_dir_error

    return server_audio_file


def _resolve_reusable_voice_preview(
    task_id: str,
    params,
    video_script: str,
    voice_preview: dict | None,
) -> tuple[str, float, object] | None:
    """Verifique e analise o cache de audição completo enviado pela WebUI.

    Essa carga útil não é um parâmetro de API público e só pode vir da WebUI do processo atual. Mesmo assim, tarefas em segundo plano
    Ainda verifique novamente os direitos autorais e todos os parâmetros de dublagem e limite o áudio para estar no diretório de tarefas atual; quaisquer inconsistências serão
    Reverta para o TTS normal para evitar que testes expirados contaminem o filme final."""
    if not voice_preview:
        return None

    expected_values = {
        "script": str(video_script or "").strip(),
        "voice_name": params.voice_name,
        "voice_rate": float(params.voice_rate),
        "voice_volume": float(params.voice_volume),
    }
    if not math.isclose(float(params.voice_volume), 1.0) or any(
        voice_preview.get(key) != value for key, value in expected_values.items()
    ):
        logger.info(
            f"skip stale voice preview cache, task_id: {task_id}, "
            "reason: voice parameters changed"
        )
        return None

    preview_file = path.realpath(str(voice_preview.get("audio_file") or ""))
    task_root = path.realpath(utils.task_dir(task_id))
    try:
        preview_is_task_local = path.commonpath([task_root, preview_file]) == task_root
    except ValueError:
        preview_is_task_local = False

    duration = voice_preview.get("duration")
    sub_maker = voice_preview.get("sub_maker")
    if (
        not preview_is_task_local
        or not path.isfile(preview_file)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
        or sub_maker is None
    ):
        logger.warning(
            f"skip invalid voice preview cache, task_id: {task_id}, "
            f"audio_file: {preview_file or '<empty>'}"
        )
        return None

    logger.info(
        f"using full voice preview audio, task_id: {task_id}, duration: {duration:.2f}s"
    )
    return preview_file, math.ceil(duration), sub_maker


def generate_audio(task_id, params, video_script, voice_preview=None):
    """
    Generate audio for the video script.
    If a custom audio file is provided, it will be used directly.
    There will be no subtitle maker object returned in this case.
    Otherwise, TTS will be used to generate the audio.
    Returns:
        - audio_file: path to the generated or provided audio file
        - audio_duration: duration of the audio in seconds
        - sub_maker: subtitle maker object if TTS is used, None otherwise
    """
    logger.info("\n\n## generating audio")
    # Os modelos de solicitação /audio e /subtitle não contêm custom_audio_file,
    # A leitura compatível é realizada aqui para evitar erros de atributos ao ajustar diretamente a interface.
    requested_custom_audio_file = getattr(params, "custom_audio_file", None)
    try:
        custom_audio_file = resolve_custom_audio_file(
            task_id, requested_custom_audio_file
        )
    except ValueError as exc:
        _mark_task_failed(
            task_id,
            "audio",
            f"invalid custom audio file: {exc}",
        )
        return None, None, None

    if not custom_audio_file:
        reusable_preview = _resolve_reusable_voice_preview(
            task_id,
            params,
            video_script,
            voice_preview,
        )
        if reusable_preview:
            return reusable_preview

        logger.info("no custom audio file provided, using TTS to generate audio.")
        audio_file = path.join(utils.task_dir(task_id), "audio.mp3")
        sub_maker = voice.tts(
            text=video_script,
            voice_name=voice.parse_voice_name(params.voice_name),
            voice_rate=params.voice_rate,
            voice_file=audio_file,
        )
        if sub_maker is None:
            _mark_task_failed(
                task_id,
                "audio",
                "failed to synthesize audio; verify the selected voice and TTS connectivity",
            )
            return None, None, None
        audio_duration = math.ceil(voice.get_audio_duration(sub_maker))
        if audio_duration == 0:
            _mark_task_failed(task_id, "audio", "generated audio duration is zero")
            return None, None, None
        return audio_file, audio_duration, sub_maker
    else:
        logger.info(f"using custom audio file: {custom_audio_file}")
        audio_duration = voice.get_audio_duration(custom_audio_file)
        if audio_duration == 0:
            _mark_task_failed(
                task_id,
                "audio",
                "custom audio duration is zero",
            )
            return None, None, None
        return custom_audio_file, audio_duration, None

def generate_subtitle(task_id, params, video_script, sub_maker, audio_file):
    '''
    Generate subtitle for the video script.
    If subtitle generation is disabled or no subtitle maker is provided, it will return an empty string.
    Otherwise, it will generate the subtitle using the specified provider.
    Returns:
        - subtitle_path: path to the generated subtitle file
    '''
    logger.info("\n\n## generating subtitle")
    if not params.subtitle_enabled:
        return ""

    subtitle_path = path.join(utils.task_dir(task_id), "subtitle.srt")
    subtitle_provider = config.app.get("subtitle_provider", "edge").strip().lower()
    logger.info(f"\n\n## generating subtitle, provider: {subtitle_provider}")

    if not subtitle_provider:
        logger.info("subtitle provider is empty, skip subtitle generation")
        return ""

    if sub_maker is None and subtitle_provider != "whisper":
        # O áudio personalizado não passará pelo TTS, portanto não há retorno de TTS do Edge/Azure etc.
        # linha do tempo do sub_maker. Somente o Whisper pode transcrever legendas diretamente de arquivos de áudio;
        # Outros provedores de legendas continuam mantendo seu antigo comportamento e evitando gerar cronogramas erroneamente vazios.
        logger.warning(
            "subtitle maker is missing, skip subtitle generation for provider: "
            f"{subtitle_provider}"
        )
        return ""

    if subtitle_provider == "edge":
        voice.create_subtitle(
            text=video_script, sub_maker=sub_maker, subtitle_file=subtitle_path
        )
        if not os.path.exists(subtitle_path):
            # As legendas de borda ocasionalmente não produzem arquivos porque a linha do tempo e a cópia não coincidem. Aqui não
            # Mude automaticamente para o Whisper, caso contrário, uma falha na primeira vez pode baixar gigabytes sem que o usuário saiba
            # modelo. O carregamento do modelo só é permitido quando o Whisper está configurado explicitamente e será retido se o Edge falhar.
            # Remova a legenda do vídeo e documente o motivo para evitar sobrecarga inesperada de rede e disco.
            logger.warning(
                "edge subtitle generation did not produce a subtitle file; "
                "skip subtitles without falling back to whisper"
            )
            return ""

    if subtitle_provider == "whisper":
        subtitle.create(audio_file=audio_file, subtitle_file=subtitle_path)
        logger.info("\n\n## correcting subtitle")
        subtitle.correct(subtitle_file=subtitle_path, video_script=video_script)

    subtitle_lines = subtitle.file_to_subtitles(subtitle_path)
    if not subtitle_lines:
        logger.warning(f"subtitle file is invalid: {subtitle_path}")
        return ""

    return subtitle_path


def get_video_materials(task_id, params, video_terms, audio_duration):
    if params.video_source == "local":
        logger.info("\n\n## preprocess local materials")
        materials = video.preprocess_video(
            materials=params.video_materials, clip_duration=params.video_clip_duration
        )
        if not materials:
            _mark_task_failed(
                task_id,
                "materials",
                "no valid local video materials were found",
            )
            return None
        return [material_info.url for material_info in materials]
    else:
        logger.info(f"\n\n## downloading videos from {params.video_source}")
        # O modo de correspondência sequencial só entra em vigor quando o usuário o ativa explicitamente. Aqui é obrigatório baixar os materiais por ordem de palavras-chave
        # A pesquisa evita que uma determinada palavra-chave inicial baixe muito material e extraia os temas de script subsequentes da linha do tempo final.
        downloaded_videos = material.download_videos(
            task_id=task_id,
            search_terms=video_terms,
            source=params.video_source,
            video_aspect=params.video_aspect,
            video_concat_mode=(
                VideoConcatMode.sequential
                if params.match_materials_to_script
                else params.video_concat_mode
            ),
            audio_duration=audio_duration * params.video_count,
            max_clip_duration=params.video_clip_duration,
            match_script_order=params.match_materials_to_script,
        )
        if not downloaded_videos:
            _mark_task_failed(
                task_id,
                "materials",
                f"failed to download video materials from {params.video_source}",
            )
            return None
        return downloaded_videos


def generate_final_videos(
    task_id, params, downloaded_videos, audio_file, subtitle_path, audio_duration
):
    final_video_paths = []
    combined_video_paths = []
    warnings = []
    video_music_provider = _VIDEO_MUSIC_PROVIDERS.get(params.bgm_type)
    video_music_requested = (
        video_music_provider is not None
        and bgm_service.should_use_bgm(params.bgm_type, params.bgm_volume)
    )
    # Por padrão, a geração de vários vídeos espalhará os materiais para aumentar as diferenças; mas "combinar os materiais na ordem da cópia" persegue
    # Estabilidade e interpretabilidade da linha do tempo, para que todas as saídas usem emenda sequencial após serem ativadas.
    if params.match_materials_to_script:
        video_concat_mode = VideoConcatMode.sequential
    elif params.video_count == 1:
        video_concat_mode = params.video_concat_mode
    else:
        video_concat_mode = VideoConcatMode.random
    video_transition_mode = params.video_transition_mode

    _progress = 50
    for i in range(params.video_count):
        index = i + 1
        combined_video_path = path.join(
            utils.task_dir(task_id), f"combined-{index}.mp4"
        )
        logger.info(f"\n\n## combining video: {index} => {combined_video_path}")
        video.combine_videos(
            combined_video_path=combined_video_path,
            video_paths=downloaded_videos,
            audio_file=audio_file,
            video_aspect=params.video_aspect,
            video_concat_mode=video_concat_mode,
            video_transition_mode=video_transition_mode,
            max_clip_duration=params.video_clip_duration,
            threads=params.n_threads,
            clip_speed=params.video_clip_speed,
        )

        _progress += 50 / params.video_count / 2
        sm.state.update_task(task_id, progress=_progress)

        final_video_path = path.join(utils.task_dir(task_id), f"final-{index}.mp4")

        # O modo de trilha sonora de vídeo primeiro desativa explicitamente a análise BGM padrão para evitar que o bgm_file restante de tarefas antigas seja
        # uso indevido. Somente quando o volume for maior que 0 o agente será gerado e a API paga será chamada; se o volume for 0, ele será ignorado uniformemente.
        bgm_file_override = "" if video_music_provider else None
        if video_music_requested:
            service = video_music_provider["service"]
            display_name = video_music_provider["display_name"]
            warning_code = video_music_provider["warning_code"]
            generated_bgm_path = path.join(
                utils.task_dir(task_id),
                (f"{params.bgm_type}-bgm-{index}{video_music_provider['suffix']}"),
            )
            try:
                service.generate_bgm(
                    video_path=combined_video_path,
                    output_path=generated_bgm_path,
                    video_duration=audio_duration,
                    prompt=_get_video_music_prompt(params),
                )
                bgm_file_override = generated_bgm_path
            except video_music_provider["error_type"] as exc:
                # Quando o vídeo, a narração e as legendas forem gerados, a falha temporária da trilha sonora de terceiros não deve desperdiçar todo o tempo.
                # Tarefa. O BGM é explicitamente desabilitado para o vídeo atual e o resultado do downgrade é retornado à WebUI para lembrar o usuário.
                logger.warning(
                    f"{display_name} BGM generation failed: task_id={task_id}, "
                    f"video_index={index}, error={exc}"
                )
                bgm_file_override = ""
                warnings.append({"code": warning_code, "video_index": index})

        logger.info(f"\n\n## generating video: {index} => {final_video_path}")
        bgm_mix_succeeded = video.generate_video(
            video_path=combined_video_path,
            audio_path=audio_file,
            subtitle_path=subtitle_path,
            output_file=final_video_path,
            params=params,
            bgm_file_override=bgm_file_override,
        )
        if (
            video_music_provider is not None
            and bgm_file_override
            and not bgm_mix_succeeded
        ):
            # O terceiro retornou com sucesso e passou na verificação do FFmpeg, mas a mixagem final do MoviePy ainda pode
            # Falha devido ao ambiente operacional. O serviço de vídeo manterá o filme finalizado sem BGM; quando a geração da API falha
            # override está vazio, portanto o aviso não será anexado repetidamente.
            warnings.append(
                {
                    "code": video_music_provider["warning_code"],
                    "video_index": index,
                }
            )

        _progress += 50 / params.video_count / 2
        sm.state.update_task(task_id, progress=_progress)

        final_video_paths.append(final_video_path)
        combined_video_paths.append(combined_video_path)

    return final_video_paths, combined_video_paths, warnings


def _patch_cross_post_state(task_id: str, **kwargs) -> bool | None:
    """Campos de liberação de atualização de segurança; tentativas limitadas em caso de falha de back-end de estado transitório."""
    for attempt in range(1, _CROSS_POST_STATE_WRITE_ATTEMPTS + 1):
        try:
            return sm.state.patch_task(task_id, **kwargs)
        except Exception as exc:
            # Uma breve desconexão do Redis não deve deixar as tarefas pendentes/em processamento para sempre. Status de lançamento
            # A frequência de escrita é muito baixa. Um número fixo de vezes e uma pequena espera podem ser usados ​​para cobrir falhas transitórias. Ao mesmo tempo
            # Evite o bloqueio infinito de threads em segundo plano. A última falha retém a pilha completa para fácil localização.
            if attempt >= _CROSS_POST_STATE_WRITE_ATTEMPTS:
                logger.exception(
                    f"failed to update cross-post state after retries, "
                    f"task_id: {task_id}, fields: {', '.join(kwargs)}, "
                    f"attempts: {attempt}, error: {exc}"
                )
                return None

            logger.warning(
                f"retry cross-post state update, task_id: {task_id}, "
                f"fields: {', '.join(kwargs)}, attempt: {attempt}, error: {exc}"
            )
            time.sleep(_CROSS_POST_STATE_RETRY_DELAY_SECONDS)

    return None


def _record_cross_post_failure(
    task_id: str,
    error: Exception,
    results: list[dict] | None = None,
) -> None:
    """As falhas de publicação são salvas com base no melhor esforço; as informações de diagnóstico são retidas pelo log quando o back-end de status está indisponível."""
    updated = _patch_cross_post_state(
        task_id,
        cross_post_state=const.CROSS_POST_STATE_FAILED,
        cross_post_results=results or None,
        cross_post_error=str(error),
        cross_post_owner=None,
    )
    if updated is False:
        logger.warning(f"discard cross-post failure for missing task: {task_id}")


def _ensure_cross_post_terminal_state(task_id: str) -> None:
    """Depois que o Futuro terminar, as tarefas que ainda estão ativas convergirão para o fracasso."""
    try:
        task = sm.state.get_task(task_id)
    except Exception as exc:
        # Este já é o retorno de chamada final do Futuro e não há chamadores síncronos subsequentes para tratar a exceção.
        # Depois que o back-end do estado for restaurado, o próximo início do processo ainda tratará o estado legado por meio da lógica de recuperação.
        logger.exception(
            f"failed to verify final cross-post state, task_id: {task_id}, error: {exc}"
        )
        return

    if not task or task.get("cross_post_state") not in _ACTIVE_CROSS_POST_STATES:
        return

    logger.warning(
        f"cross-post worker ended without terminal state, task_id: {task_id}, "
        f"state: {task.get('cross_post_state')}"
    )
    _record_cross_post_failure(
        task_id,
        RuntimeError("cross-post worker ended without persisting a terminal state"),
        task.get("cross_post_results"),
    )


def recover_interrupted_cross_posts(page_size: int = 100) -> int | None:
    """Marcar como falhadas as tarefas de publicação que não podem ser recuperadas após o reinício do processo.

    A publicação multiplataforma usa o pool de threads no processo atual, não a fila de tarefas persistentes. Quando o processo começar,
    O restante pendente/processamento no Redis não continuará a ser executado automaticamente; se continuarem a ser tratados como
    Durante a execução, o usuário nunca poderá excluir a tarefa. O status da varredura de paginação aqui processa apenas o processo atual e não
    Corresponde aos registros de atividades do Future e retém os resultados de vídeo gerados."""
    recovered = 0
    page = 1

    while True:
        try:
            tasks, total = sm.state.get_all_tasks(page, page_size)
        except Exception as exc:
            logger.exception(f"failed to recover interrupted cross-post tasks: {exc}")
            return None

        for task in tasks:
            task_id = str(task.get("task_id") or "")
            if (
                not task_id
                or task.get("cross_post_state") not in _ACTIVE_CROSS_POST_STATES
                or _is_cross_post_active_in_process(task_id)
                or _is_cross_post_owner_alive(task.get("cross_post_owner"))
            ):
                continue

            updated = _patch_cross_post_state(
                task_id,
                cross_post_state=const.CROSS_POST_STATE_FAILED,
                cross_post_error=_INTERRUPTED_CROSS_POST_ERROR,
                cross_post_owner=None,
            )
            if updated is True:
                recovered += 1

        if page * page_size >= total or not tasks:
            break
        page += 1

    if recovered:
        logger.warning(f"recovered interrupted cross-post tasks: {recovered}")
    return recovered


def _run_cross_post(
    task_id: str,
    video_paths: tuple[str, ...],
    video_subject: str,
    video_script: str,
    video_language: str,
    platforms: tuple[str, ...],
    youtube_privacy_status: str,
) -> None:
    """A publicação entre plataformas é executada em segundo plano e apenas campos de tarefas relacionados à publicação são adicionados."""
    results = []
    try:
        state_updated = _patch_cross_post_state(
            task_id,
            cross_post_state=const.CROSS_POST_STATE_PROCESSING,
            cross_post_error=None,
            cross_post_owner=_cross_post_process_owner,
        )
        if state_updated is not True:
            # False significa que a tarefa foi excluída, None significa que o back-end de status está temporariamente indisponível. Em ambos os casos
            # A interface de terceiros não deve continuar a ser chamada, caso contrário o usuário não poderá consultar ou controlar esta versão.
            if state_updated is False:
                logger.warning(f"skip cross-post for missing task: {task_id}")
            else:
                _record_cross_post_failure(
                    task_id,
                    RuntimeError("failed to persist cross-post processing state"),
                )
            return

        logger.info(
            f"cross-post started, task_id: {task_id}, platforms: {', '.join(platforms)}"
        )
        youtube_extra = None
        if any(platform.startswith("youtube") for platform in platforms):
            metadata = llm.generate_social_metadata(
                video_subject=video_subject,
                video_script=video_script,
                language=video_language or "",
                platform="youtube_shorts",
            )
            youtube_extra = {
                "youtube_title": metadata.get("title", video_subject),
                "youtube_description": metadata.get("caption", ""),
                "tags": metadata.get("hashtags", []),
                "privacyStatus": youtube_privacy_status,
                "containsSyntheticMedia": True,
            }

        for video_path in video_paths:
            result = upload_post.cross_post_video(
                video_path=video_path,
                title=video_subject or "Check out this video! #shorts #viral",
                platforms=list(platforms),
                youtube_extra=youtube_extra,
            )
            if not isinstance(result, dict):
                result = {
                    "success": False,
                    "error": "Upload-Post returned an invalid response",
                }
            results.append(result)

        failures = [result for result in results if not result.get("success")]
        if failures:
            error_messages = [
                str(
                    result.get("error")
                    or result.get("message")
                    or "unknown upload error"
                )
                for result in failures
            ]
            cross_post_state = const.CROSS_POST_STATE_FAILED
            cross_post_error = "; ".join(error_messages)
            logger.warning(
                f"cross-post completed with failures, task_id: {task_id}, "
                f"failed: {len(failures)}, total: {len(results)}"
            )
        else:
            cross_post_state = const.CROSS_POST_STATE_COMPLETE
            cross_post_error = None
            logger.success(
                f"cross-post completed, task_id: {task_id}, videos: {len(results)}"
            )

        state_updated = _patch_cross_post_state(
            task_id,
            cross_post_state=cross_post_state,
            cross_post_results=results,
            cross_post_error=cross_post_error,
            cross_post_owner=None,
        )
        if state_updated is False:
            logger.warning(f"discard cross-post result for missing task: {task_id}")
        elif state_updated is None:
            # Quando o upload terminar, mas os resultados não persistirem, o processamento não poderá continuar.
            # As gravações de status com falha serão repetidas novamente com tentativas limitadas, permitindo pelo menos que o chamador obtenha um estado final claro.
            _record_cross_post_failure(
                task_id,
                RuntimeError("failed to persist final cross-post result"),
                results,
            )
    except Exception as exc:
        # A falha na publicação afeta apenas o status de publicação e não pode substituir reversamente as tarefas de vídeo concluídas.
        # O texto original da exceção é gravado no status da tarefa e o chamador da API pode localizar o problema sem acessar o log do servidor.
        logger.exception(f"cross-post failed, task_id: {task_id}, error: {exc}")
        _record_cross_post_failure(task_id, exc, results)


def _run_cross_post_with_slot(*args) -> None:
    """Execute tarefas de publicação e garanta que a capacidade da fila seja retornada em caso de sucesso, falha ou exceção."""
    try:
        _run_cross_post(*args)
    except Exception as exc:
        # _run_cross_post tratou a exceção esperada; esta é a última linha de proteção contra adições futuras
        # As exceções lançadas pela lógica são armazenadas apenas em Futures que ninguém pode ler.
        task_id = str(args[0]) if args else "unknown"
        logger.exception(
            f"cross-post worker crashed, task_id: {task_id}, error: {exc}"
        )
        if args:
            _record_cross_post_failure(task_id, exc)
    finally:
        _cross_post_slots.release()


def _finalize_cross_post_future(task_id: str, future: Future) -> None:
    """Limpe registros futuros e garanta que cancelamentos, exceções e falhas de gravação de status convirjam."""
    _unregister_cross_post_future(task_id, future)

    try:
        error = future.exception()
    except CancelledError:
        logger.warning(f"cross-post future was cancelled, task_id: {task_id}")
        # Quando o Future é cancelado antes de iniciar a execução, o trabalhador finalmente não será executado, então ele precisa
        # Retorne a capacidade da fila no retorno de chamada e altere o status de persistência para falha.
        _cross_post_slots.release()
        _record_cross_post_failure(
            task_id,
            RuntimeError("cross-post job was cancelled before execution"),
        )
        return
    except Exception as exc:
        logger.exception(
            f"failed to inspect cross-post future, task_id: {task_id}, error: {exc}"
        )
        _ensure_cross_post_terminal_state(task_id)
        return

    if error is not None:
        logger.error(
            f"cross-post future failed, task_id: {task_id}, "
            f"error: {type(error).__name__}: {error}"
        )

    _ensure_cross_post_terminal_state(task_id)


def _schedule_cross_post(
    task_id: str,
    video_paths: list[str],
    params: VideoParams,
    video_script: str,
    platforms: list[str],
    youtube_privacy_status: str,
) -> str | None:
    """Envie uma tarefa de publicação em segundo plano; retorne None se for bem-sucedido e retorne um motivo de erro consultável se o agendamento falhar."""
    if not _cross_post_slots.acquire(blocking=False):
        error = "cross-post queue is full; publishing was skipped"
        logger.warning(
            f"skip cross-post because queue is full, task_id: {task_id}, "
            f"capacity: {_cross_post_max_pending_tasks}"
        )
        _patch_cross_post_state(
            task_id,
            cross_post_state=const.CROSS_POST_STATE_FAILED,
            cross_post_error=error,
            cross_post_owner=None,
        )
        return error

    try:
        future = _cross_post_executor.submit(
            _run_cross_post_with_slot,
            task_id,
            tuple(video_paths),
            params.video_subject or "",
            video_script,
            params.video_language or "",
            tuple(platforms),
            youtube_privacy_status,
        )
        _register_cross_post_future(task_id, future)
        future.add_done_callback(partial(_finalize_cross_post_future, task_id))
    except RuntimeError as exc:
        _unregister_cross_post_future(task_id)
        _cross_post_slots.release()
        logger.exception(
            f"failed to schedule cross-post, task_id: {task_id}, error: {exc}"
        )
        _patch_cross_post_state(
            task_id,
            cross_post_state=const.CROSS_POST_STATE_FAILED,
            cross_post_error=f"failed to schedule cross-post: {exc}",
            cross_post_owner=None,
        )
        return f"failed to schedule cross-post: {exc}"

    return None


def _run_pipeline(
    task_id,
    params: VideoParams,
    stop_at: str = "video",
    voice_preview: dict | None = None,
):
    logger.info(f"start task: {task_id}, stop_at: {stop_at}")
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=5)

    # Os fornecedores de trilhas sonoras de vídeo são necessários apenas para todo o processo de produção. Bloqueie antecipadamente tarefas completas com chaves ausentes para evitar
    # Os créditos LLM, TTS e serviços materiais são consumidos primeiro; a interface do produto intermediário ainda pode ser usada de forma independente.
    video_music_provider = _VIDEO_MUSIC_PROVIDERS.get(params.bgm_type)
    video_music_enabled = (
        stop_at == "video"
        and video_music_provider is not None
        and bgm_service.should_use_bgm(params.bgm_type, params.bgm_volume)
    )
    if video_music_enabled:
        service = video_music_provider["service"]
        display_name = video_music_provider["display_name"]
        if not service.is_enabled():
            return _mark_task_failed(
                task_id,
                "preflight",
                f"{display_name} background music requires an API key",
            )

        # A WebUI limita o comprimento da entrada, mas as tarefas de API, CLI e histórico podem ignorar os controles de front-end.
        # Antes de gerar roteiros, dublagens e materiais, verifique novamente de acordo com o limite superior do fornecedor para evitar a síntese completa do vídeo.
        # Foi apenas o pedido do terceiro que foi rejeitado. A camada de serviço ainda mantém a mesma validação que a última linha de defesa ao chamar diretamente.
        music_prompt = _get_video_music_prompt(params)
        max_prompt_length = int(getattr(service, "MAX_PROMPT_LENGTH", 0) or 0)
        if max_prompt_length and len(music_prompt) > max_prompt_length:
            return _mark_task_failed(
                task_id,
                "preflight",
                (f"{display_name} music prompt exceeds {max_prompt_length} characters"),
            )

        # Os fornecedores podem optar por fornecer pré-verificação de conta gratuitamente. Funções de verificação só devem lançar funções determinísticas
        # Erro; quando as flutuações da rede ou o intervalo de permissão não podem ser confirmados, a camada de serviço registra um aviso e continua a geração real.
        validate_access = getattr(service, "validate_generation_access", None)
        if callable(validate_access):
            try:
                validate_access()
            except video_music_provider["error_type"] as exc:
                return _mark_task_failed(task_id, "preflight", str(exc))

    # 1. Generate script
    video_script = generate_script(task_id, params)
    if not video_script or "Error: " in video_script:
        error = (
            video_script.removeprefix("Error: ").strip()
            if isinstance(video_script, str) and "Error: " in video_script
            else "failed to generate video script"
        )
        return _mark_task_failed(task_id, "script", error)

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=10)

    if stop_at == "script":
        sm.state.update_task(
            task_id, state=const.TASK_STATE_COMPLETE, progress=100, script=video_script
        )
        return {"script": video_script}

    # 2. Generate terms
    video_terms = ""
    if params.video_source != "local":
        video_terms = generate_terms(task_id, params, video_script)
        if not video_terms:
            return _mark_task_failed(
                task_id,
                "terms",
                "failed to generate video search terms",
            )

    save_script_data(task_id, video_script, video_terms, params)

    if stop_at == "terms":
        sm.state.update_task(
            task_id, state=const.TASK_STATE_COMPLETE, progress=100, terms=video_terms
        )
        return {"script": video_script, "terms": video_terms}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=20)

    # 3. Generate audio
    audio_file, audio_duration, sub_maker = generate_audio(
        task_id,
        params,
        video_script,
        voice_preview=voice_preview,
    )
    if not audio_file:
        return _mark_task_failed(
            task_id,
            "audio",
            "failed to prepare narration audio",
        )

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=30)

    if stop_at == "audio":
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            audio_file=audio_file,
        )
        return {"audio_file": audio_file, "audio_duration": audio_duration}

    # 4. Generate subtitle
    subtitle_path = generate_subtitle(
        task_id, params, video_script, sub_maker, audio_file
    )

    if stop_at == "subtitle":
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            subtitle_path=subtitle_path,
        )
        return {"subtitle_path": subtitle_path}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=40)

    # 5. Get video materials
    downloaded_videos = get_video_materials(
        task_id, params, video_terms, audio_duration
    )
    if not downloaded_videos:
        return _mark_task_failed(
            task_id,
            "materials",
            "failed to prepare video materials",
        )

    if stop_at == "materials":
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            materials=downloaded_videos,
        )
        return {"materials": downloaded_videos}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=50)

    # Somente o processo completo de geração de vídeo precisa lidar com o modo de junção de vídeo;
    # Isso evita solicitações como /subtitle e /audio acessando campos inexistentes.
    if type(params.video_concat_mode) is str:
        params.video_concat_mode = VideoConcatMode(params.video_concat_mode)

    # 6. Generate final videos
    final_video_paths, combined_video_paths, generation_warnings = generate_final_videos(
        task_id,
        params,
        downloaded_videos,
        audio_file,
        subtitle_path,
        audio_duration,
    )

    if not final_video_paths:
        return _mark_task_failed(
            task_id,
            "video",
            "failed to generate final video",
        )

    logger.success(
        f"task {task_id} finished, generated {len(final_video_paths)} videos."
    )

    # 7. Conclua primeiro a tarefa de geração de vídeo e depois envie-o para publicação em plataforma cruzada, conforme necessário. Uploads de terceiros podem ser demorados
    # Não deve bloquear o retorno dos resultados dos vídeos por vários minutos, nem deve afetar negativamente os vídeos já gerados.
    cross_post_enabled = (
        upload_post.upload_post_service.is_configured()
        and upload_post.upload_post_service.auto_upload
    )
    platforms = (
        list(upload_post.upload_post_service.platforms)
        if cross_post_enabled
        else []
    )
    should_cross_post = cross_post_enabled and bool(platforms)
    if cross_post_enabled and not platforms:
        logger.warning(
            f"skip cross-post because no platforms are configured, task_id: {task_id}"
        )
    cross_post_state = const.CROSS_POST_STATE_PENDING if should_cross_post else None

    kwargs = {
        "videos": final_video_paths,
        "combined_videos": combined_video_paths,
        "script": video_script,
        "terms": video_terms,
        "audio_file": audio_file,
        "audio_duration": audio_duration,
        "subtitle_path": subtitle_path,
        "materials": downloaded_videos,
        "cross_post_state": cross_post_state,
        "cross_post_results": None,
        "cross_post_error": None,
        "cross_post_owner": _cross_post_process_owner if should_cross_post else None,
        "warnings": generation_warnings or None,
    }
    sm.state.update_task(
        task_id, state=const.TASK_STATE_COMPLETE, progress=100, **kwargs
    )

    if should_cross_post:
        scheduling_error = _schedule_cross_post(
            task_id=task_id,
            video_paths=final_video_paths,
            params=params,
            video_script=video_script,
            platforms=platforms,
            youtube_privacy_status=(
                upload_post.upload_post_service.youtube_privacy_status
            ),
        )
        # A fila está cheia ou o conjunto de threads está fechado, o que é uma falha de agendamento com reconhecimento de sincronização. O status da tarefa foi determinado pela função de agendamento
        # Atualização, o instantâneo de retorno é corrigido de forma síncrona aqui para evitar que o chamador receba uma pendência que seja inconsistente com as consultas subsequentes.
        if scheduling_error:
            kwargs["cross_post_state"] = const.CROSS_POST_STATE_FAILED
            kwargs["cross_post_error"] = scheduling_error
            kwargs["cross_post_owner"] = None

    return kwargs


def start(
    task_id,
    params: VideoParams,
    stop_at: str = "video",
    voice_preview: dict | None = None,
):
    """Execute o pipeline de tarefas e garanta que exceções inesperadas também sejam convertidas em status de falha consultável."""
    try:
        return _run_pipeline(
            task_id,
            params,
            stop_at=stop_at,
            voice_preview=voice_preview,
        )
    except Exception as exc:
        logger.exception(
            f"unexpected task pipeline failure, task_id: {task_id}, error: {exc}"
        )
        return _mark_task_failed(
            task_id,
            "pipeline",
            f"{type(exc).__name__}: {exc}",
        )


if __name__ == "__main__":
    task_id = "task_id"
    params = VideoParams(
        video_subject="金钱的作用",
        voice_name="zh-CN-XiaoyiNeural-Female",
        voice_rate=1.0,
    )
    start(task_id, params, stop_at="video")
