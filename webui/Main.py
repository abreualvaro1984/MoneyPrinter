import hashlib
import html
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import requests
import streamlit as st
from loguru import logger
from streamlit_tour import Tour

# Quando o WebUI é executado como um portal independente, o diretório raiz do projeto precisa ser adicionado ao caminho de pesquisa do módulo.
root_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if root_dir not in sys.path:
    sys.path.append(root_dir)

from app.config import config
from app.models import const
from app.models.llm_provider import (
    DEFAULT_LLM_PROVIDER_ID,
    LLM_PROVIDER_REGISTRY,
    get_llm_provider,
    normalize_provider_override,
)
from app.models.schema import (
    MaterialInfo,
    VideoAspect,
    VideoConcatMode,
    VideoParams,
    VideoTransitionMode,
)
from app.services import bgm as bgm_service
from app.services import cache_manager, llm, video, voice, webui_task
from app.services import elevenlabs_music as elevenlabs_music_service
from app.services import sonilo as sonilo_service
from app.services import state as sm
from app.services import task as tm
from app.services import version_checker
from app.utils.logging_utils import configure_terminal_logger
from app.utils import utils

st.set_page_config(
    page_title="MoneyPrinterTurbo",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="auto",
    menu_items={
        "Report a bug": "https://github.com/harry0703/MoneyPrinterTurbo/issues",
        "About": "# MoneyPrinterTurbo\nSimply provide a topic or keyword for a video, and it will "
        "automatically generate the video copy, video materials, video subtitles, "
        "and video background music before synthesizing a high-definition short "
        "video.\n\nhttps://github.com/harry0703/MoneyPrinterTurbo",
    },
)


# O Streamlit 1.59 exibirá entradas de plataforma, como Implantação e ajuste de habilidades por padrão, no canto superior direito da página.
# MoneyPrinterTurbo é uma ferramenta nativa para usuários finais, essas entradas deixarão um grande espaço em branco na parte superior,
# Também pode confundir novos usuários, fazendo-os pensar que precisam instalar componentes adicionais. A barra de ferramentas da plataforma Streamlit está uniformemente oculta aqui.
# E comprima o espaço superior do contêiner principal para deixar apenas o título do projeto, a seleção de idioma e a área de configurações de negócios.
style_file = Path(__file__).with_name("styles.css")
streamlit_style = f"<style>{style_file.read_text(encoding='utf-8')}</style>"
st.markdown(streamlit_style, unsafe_allow_html=True)
# Definir diretório de recursos
font_dir = os.path.join(root_dir, "resource", "fonts")
song_dir = os.path.join(root_dir, "resource", "songs")
i18n_dir = os.path.join(root_dir, "webui", "i18n")
config_file = os.path.join(root_dir, "webui", ".streamlit", "webui.toml")
# A lista de idiomas deve estar disponível antes da inicialização do estado da sessão para que a localidade do navegador possa ser mapeada para
# Idiomas verdadeiramente suportados pelo projeto; os resultados do reconhecimento automático entram apenas na sessão atual e não modificam a configuração global.
locales = utils.load_locales(i18n_dir)
DEFAULT_CHATTERBOX_BASE_URL = "http://127.0.0.1:4123/v1"
DEFAULT_CHATTERBOX_MODEL = "chatterbox"
DEFAULT_CHATTERBOX_VOICES = ["default-Female"]
ONBOARDING_TOUR_KEY = "mpt-onboarding-v1"
VOICE_MODE_TTS = "tts"
VOICE_MODE_UPLOAD = "upload"
VOICE_MODE_NONE = "none"
# "Padrão" é um sentinela específico da WebUI que não será gravado no config.toml ou passado para o FFmpeg.
# O backend continua a usar libx264 estável quando video_codec não está configurado; deixar esta sentinela sozinha pode diferenciar
# "Seguir a política padrão do projeto" e "O usuário corrige explicitamente a libx264" para facilitar futuros ajustes de segurança na política padrão.
DEFAULT_VIDEO_CODEC_OPTION = "__default__"
DEFAULT_SUBTITLE_SETTINGS = {
    "subtitle_enabled": True,
    "font_name": "MicrosoftYaHeiBold.ttc",
    "subtitle_position": "bottom",
    "custom_position": 70.0,
    "text_fore_color": "#FFFFFF",
    "font_size": 60,
    "stroke_color": "#000000",
    "stroke_width": 1.5,
    "subtitle_background_enabled": False,
    "subtitle_background_color": "#000000",
    "rounded_subtitle_background": False,
}
LOCAL_MATERIAL_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".avi",
    ".flv",
    ".mkv",
    ".jpg",
    ".jpeg",
    ".png",
}
CUSTOM_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
_FINAL_VIDEO_PATTERN = re.compile(
    r"^final-(?P<index>\d+)\.(?P<extension>mp4|mov|mkv|webm)$",
    re.IGNORECASE,
)


# -----------------------------------------------------------------------------
# Configuração de inicialização, estado da sessão e localização
# -----------------------------------------------------------------------------


def _parse_chatterbox_voices(voices):
    # Chatterbox é um serviço auto-hospedado e as listas de patches são inseridas manualmente pelo usuário na WebUI.
    # Isso é uniformemente compatível com strings separadas por vírgula em matrizes TOML e caixas de entrada para evitar caixas suspensas,
    # O botão de audição e o processo de geração subsequente usam formatos diferentes, resultando em status inconsistente.
    if isinstance(voices, str):
        return [v.strip() for v in voices.split(",") if v.strip()]
    return [str(v).strip() for v in voices or [] if str(v).strip()]


def _sync_chatterbox_config_from_session_state():
    # O botão do Streamlit acionará uma nova execução de página inteira e a caixa de entrada de configuração do Chatterbox estará localizada
    # Após o botão "Ouvir síntese de fala". Se você leu apenas config.chatterbox durante a audição, talvez não consiga obtê-lo.
    # O base_url/model/voices que o usuário acabou de preencher na caixa de entrada. Primeiro sincronize uma vez de session_state,
    # Pode-se garantir que a lógica do botão e a lógica de exibição da caixa de entrada usem a mesma configuração mais recente.
    config.chatterbox["base_url"] = (
        st.session_state.get(
            "chatterbox_base_url_input",
            config.chatterbox.get("base_url") or DEFAULT_CHATTERBOX_BASE_URL,
        )
        or ""
    ).strip()
    config.chatterbox["api_key"] = st.session_state.get(
        "chatterbox_api_key_input", config.chatterbox.get("api_key", "")
    )
    config.chatterbox["model_id"] = (
        st.session_state.get(
            "chatterbox_model_input",
            config.chatterbox.get("model_id") or DEFAULT_CHATTERBOX_MODEL,
        )
        or DEFAULT_CHATTERBOX_MODEL
    ).strip()
    config.chatterbox["voices"] = _parse_chatterbox_voices(
        st.session_state.get(
            "chatterbox_voices_input",
            config.chatterbox.get("voices") or DEFAULT_CHATTERBOX_VOICES,
        )
    )


def _detect_audio_mime(audio_file: str, audio_bytes: bytes) -> str:
    # Alguns serviços TTS compatíveis com OpenAI, como travisvn/chatterbox-tts-api,
    # Mesmo que response_format=mp3 seja solicitado, o conteúdo WAV será retornado. Audição WebUI se corrigida
    # Com áudio/mp3, o navegador pode não conseguir reproduzi-lo, então aqui o formato real é identificado pelo cabeçalho do arquivo.
    header = audio_bytes[:12]
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "audio/wav"
    if header.startswith(b"ID3") or header[:2] in (
        b"\xff\xfb",
        b"\xff\xf3",
        b"\xff\xf2",
    ):
        return "audio/mp3"
    if header.startswith(b"OggS"):
        return "audio/ogg"
    ext = os.path.splitext(audio_file)[1].lower()
    return {
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(ext, "audio/mp3")


def _build_uploaded_file_path(uploaded_file, target_dir, allowed_extensions, prefix):
    """Gere caminhos de salvamento controlados no servidor para arquivos carregados pelo navegador."""
    original_name = os.path.basename(str(uploaded_file.name or ""))
    extension = os.path.splitext(original_name)[1].lower()
    if extension not in allowed_extensions:
        logger.warning(
            f"reject unsupported uploaded file extension: {original_name or '<empty>'}"
        )
        raise ValueError("unsupported uploaded file type")

    normalized_target_dir = os.path.realpath(target_dir)
    os.makedirs(normalized_target_dir, exist_ok=True)
    # Não reutilize o nome do arquivo passado pelo navegador e evite sobrescrever separadores de caminho, caracteres de controle ou mesmo nome. UUID é usado apenas para
    # O download do lado do servidor não altera o nome original visto pelo usuário no controle de upload.
    file_path = os.path.realpath(
        os.path.join(normalized_target_dir, f"{prefix}-{uuid4().hex}{extension}")
    )
    if os.path.commonpath([normalized_target_dir, file_path]) != normalized_target_dir:
        logger.warning(f"invalid uploaded file path: {file_path}")
        raise ValueError("invalid uploaded file path")
    return file_path


def _initialize_session_state():
    """Inicialize centralmente o estado da página que é preservado nas repetições."""
    if not st.session_state.get("cross_post_recovery_checked"):
        # WebUI pode ser executado de forma independente sem FastAPI, portanto, também precisa ser processado durante a inicialização da primeira sessão
        # Status de publicação deixado para trás pela reinicialização do processo. Quando a recuperação falha, nenhuma marca é escrita e as repetições subsequentes tentarão novamente.
        recovered = tm.recover_interrupted_cross_posts()
        if recovered is not None:
            st.session_state["cross_post_recovery_checked"] = True

    saved_ui_language = config.ui.get("language", "")
    browser_locale = st.context.locale
    initial_ui_language = utils.resolve_ui_language(
        saved_language=saved_ui_language,
        browser_locale=browser_locale,
        supported_languages=locales.keys(),
    )

    defaults = {
        "video_subject": "",
        "video_script": "",
        "video_terms": "",
        "video_script_prompt": "",
        "custom_system_prompt": llm.DEFAULT_SCRIPT_SYSTEM_PROMPT,
        "match_materials_to_script": bool(
            config.app.get("match_materials_to_script", False)
        ),
        "ui_language": initial_ui_language,
        # Os materiais locais que foram colocados no disco permitem que os usuários continuem a reutilizá-los após modificar apenas a cópia.
        "local_video_materials": [],
        # Para gerar um retorno de chamada de botão, registre a tarefa primeiro para que a entrada superior possa exibir imediatamente a quantidade em execução.
        "active_generation_tasks": {},
        # A tarefa mais recente enviada da página atual. Após a geração ser alterada para execução em segundo plano, o fragmento da página
        # Status da consulta por este ID; a atualização não depende mais da execução do script da página antiga.
        "current_generation_task_id": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


_initialize_session_state()


def tr(key):
    loc = locales.get(st.session_state["ui_language"], {})
    return loc.get("Translation", {}).get(key, key)


# -----------------------------------------------------------------------------
# Gerenciamento de tarefas: verificação histórica, status de execução, recuperação de parâmetros e interação de lista
# -----------------------------------------------------------------------------


def _format_task_time(timestamp):
    if not timestamp:
        return "-"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _format_task_subject(subject, max_length=30):
    subject = str(subject or "").replace("\n", " ").strip()
    if len(subject) <= max_length:
        return subject or "-"
    return f"{subject[:max_length]}..."


def _safe_load_task_script(task_path):
    script_file = os.path.join(task_path, "script.json")
    if not os.path.isfile(script_file):
        return {}

    try:
        with open(script_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"failed to read task script data: {script_file}, {e}")
        return {}


def _find_final_task_video(task_path: str) -> str:
    """
    Retorne o filme final com o menor número de sequência no diretório de tarefas.

    O processo de composição também produz arquivos temporários combinados, clipe temporário e MoviePy, que não podem ser
    Indica que a tarefa foi concluída com sucesso, portanto apenas ``final-<número de série>.<extensão>`` é aceito aqui.
    """
    try:
        files = os.listdir(task_path)
    except OSError:
        return ""

    candidates = []
    for file_name in files:
        match = _FINAL_VIDEO_PATTERN.fullmatch(file_name)
        if match:
            candidates.append((int(match.group("index")), file_name))

    if not candidates:
        return ""

    _, file_name = min(candidates, key=lambda item: item[0])
    return os.path.join(task_path, file_name)


def _build_restore_upload_requirements(params: Mapping) -> dict:
    """
    Registre dependências de arquivos carregados em tarefas históricas que não podem ser restauradas automaticamente pelo Streamlit.

    O navegador não permite que o programa preencha novamente o file_uploader, portanto, um log local separado é necessário ao restaurar a tarefa
    Dependências de materiais e áudio personalizadas e verifique se elas foram ativamente complementadas ou substituídas antes da regeneração do usuário.
    """
    return {
        "local_materials": params.get("video_source") == "local",
        "custom_audio": bool(params.get("custom_audio_file")),
        "original_voice_name": params.get("voice_name") or "",
    }


def _get_unmet_restore_upload_requirements(
    requirements: Mapping | None,
    *,
    video_source: str,
    voice_name: str,
    has_local_materials: bool,
    has_custom_audio: bool,
    voice_mode: str | None = None,
) -> set[str]:
    """Retorna dependências históricas de arquivos carregados que ainda não foram atendidas pelo formulário atual."""
    requirements = requirements or {}
    unmet = set()

    if (
        requirements.get("local_materials")
        and video_source == "local"
        and not has_local_materials
    ):
        unmet.add("local_materials")

    if requirements.get("custom_audio") and not has_custom_audio:
        if voice_mode is not None:
            # A nova versão do WebUI usa narração explícita. O usuário alterna para dublagem automática ou sem dublagem, indicando
            # O áudio carregado historicamente foi substituído ativamente; o reenvio só será necessário se o modo de upload continuar selecionado.
            if voice_mode == VOICE_MODE_UPLOAD:
                unmet.add("custom_audio")
        elif voice_name == requirements.get("original_voice_name", ""):
            # Mantenha o comportamento de compatibilidade do chamador antigo com base no timbre para evitar afetar a API e as ferramentas de teste existentes.
            unmet.add("custom_audio")

    return unmet


def _queue_task_restore(task_id):
    # A lista de tarefas é executada em um fragmento e não pode modificar diretamente o estado do controle de formulário principal criado.
    # Aqui apenas as tarefas candidatas são registradas e uma nova execução de página inteira é acionada. A confirmação e a recuperação de parâmetros são tratadas uniformemente pela página principal.
    st.session_state["task_restore_candidate_id"] = task_id
    st.session_state["task_manager_popover_nonce"] = (
        st.session_state.get("task_manager_popover_nonce", 0) + 1
    )
    st.rerun(scope="app")


def _normalize_task_state(state):
    if state in (
        const.TASK_STATE_COMPLETE,
        const.TASK_STATE_FAILED,
        const.TASK_STATE_PROCESSING,
    ):
        return state
    try:
        return int(state)
    except (TypeError, ValueError):
        return state


def _active_generation_tasks():
    tasks = st.session_state.setdefault("active_generation_tasks", {})
    if not isinstance(tasks, dict):
        tasks = {}
        st.session_state["active_generation_tasks"] = tasks
    return tasks


def _add_active_generation_task(task_id, subject=None):
    tasks = _active_generation_tasks()
    task = tasks.setdefault(task_id, {})
    task["subject"] = subject or task.get("subject") or task_id
    task["mtime"] = task.get("mtime") or datetime.now().timestamp()


def _remove_active_generation_task(task_id):
    tasks = _active_generation_tasks()
    if task_id in tasks:
        del tasks[task_id]
    if st.session_state.get("pending_generation_task_id") == task_id:
        del st.session_state["pending_generation_task_id"]


def _prepare_generation_task():
    # O on_click do st.button será acionado antes que o script da página seja executado novamente. Gere o ID da tarefa antecipadamente aqui,
    # A entrada superior de gerenciamento de tarefas pode exibir o número de "gerações" na mesma reexecução.
    task_id = str(uuid4())
    st.session_state["pending_generation_task_id"] = task_id
    subject = st.session_state.get("video_subject") or st.session_state.get(
        "video_script"
    )
    _add_active_generation_task(task_id, subject=subject)


def _task_state_label(state, has_video):
    normalized_state = _normalize_task_state(state)
    if normalized_state == const.TASK_STATE_COMPLETE:
        return tr("Task Status Complete")
    if normalized_state == const.TASK_STATE_FAILED:
        return tr("Task Status Failed")
    if normalized_state == const.TASK_STATE_PROCESSING:
        return tr("Task Status Processing")
    if has_video:
        return tr("Task Status Complete")
    return tr("Task Status History")


def _task_state_filter_key(task):
    normalized_state = _normalize_task_state(task.get("state"))
    if normalized_state == const.TASK_STATE_PROCESSING:
        return "processing"
    if normalized_state == const.TASK_STATE_FAILED:
        return "failed"
    if normalized_state == const.TASK_STATE_COMPLETE or task["video_file"]:
        return "complete"
    return "history"


def _scan_history_tasks(limit=30):
    tasks_root = utils.task_dir()
    if not os.path.isdir(tasks_root):
        return []

    # O fragmento de gerenciamento de tarefas é atualizado a cada dois segundos. Primeiro leia apenas metadados de diretório de baixo custo e intercepte os mais recentes
    # tarefa e, em seguida, analise o script.json e a lista de vídeos para evitar a verificação repetida de todo o conteúdo quando houver muitas tarefas históricas.
    task_entries = []
    try:
        with os.scandir(tasks_root) as entries:
            for entry in entries:
                try:
                    if entry.name.startswith(".") or not entry.is_dir(
                        follow_symlinks=False
                    ):
                        continue
                    task_entries.append(
                        (
                            entry.stat(follow_symlinks=False).st_mtime,
                            entry.name,
                            entry.path,
                        )
                    )
                except OSError as e:
                    # Diretórios de tarefas individuais podem estar sendo excluídos e isso não deve inutilizar todo o painel de tarefas.
                    logger.debug(f"skip unavailable task directory: {entry.path}, {e}")
    except OSError as e:
        logger.warning(f"failed to scan task directory: {tasks_root}, {e}")
        return []

    task_entries.sort(key=lambda item: item[0], reverse=True)
    tasks = []
    for mtime, name, task_path in task_entries[:limit]:
        script_data = _safe_load_task_script(task_path)
        params_data = script_data.get("params", {}) if script_data else {}
        video_file = _find_final_task_video(task_path)
        subject = (
            params_data.get("video_subject")
            or script_data.get("script", "")[:40]
            or name
        )
        tasks.append(
            {
                "task_id": name,
                "subject": subject,
                "state": const.TASK_STATE_COMPLETE if video_file else None,
                "progress": 100 if video_file else 0,
                "mtime": mtime,
                "task_path": task_path,
                "video_file": video_file,
                "source": "history",
            }
        )

    return tasks


def _collect_task_summaries(limit=20):
    history_tasks = {task["task_id"]: task for task in _scan_history_tasks(limit=50)}

    try:
        runtime_tasks, _ = sm.state.get_all_tasks(1, 50)
    except Exception as e:
        logger.warning(f"failed to load runtime tasks: {e}")
        runtime_tasks = []

    for task in runtime_tasks:
        task_id = task.get("task_id", "")
        if not task_id:
            continue

        task_path = os.path.join(utils.task_dir(), task_id)
        history_task = history_tasks.get(task_id, {})
        video_files = task.get("videos") or []
        video_file = (
            video_files[0] if video_files else history_task.get("video_file", "")
        )
        subject = (
            task.get("video_subject")
            or history_task.get("subject")
            or (task.get("script", "")[:40] if task.get("script") else "")
            or task_id
        )

        history_tasks[task_id] = {
            "task_id": task_id,
            "subject": subject,
            "state": task.get("state"),
            "cross_post_state": task.get("cross_post_state"),
            "progress": int(task.get("progress", 0) or 0),
            "mtime": os.path.getmtime(task_path)
            if os.path.isdir(task_path)
            else history_task.get("mtime", 0),
            "task_path": task_path,
            "video_file": video_file,
            "source": "runtime",
        }

    for task_id, active_task in _active_generation_tasks().items():
        history_task = history_tasks.get(task_id, {})
        if history_task and _task_state_filter_key(history_task) in {
            "complete",
            "failed",
        }:
            # A tag ativa na sessão é responsável apenas por cobrir a janela muito curta antes de a tarefa ser enviada ao armazenamento de estado.
            # Após o término da tarefa em segundo plano, o estado final real deve prevalecer e as tarefas com falha não podem ser exibidas novamente como sendo geradas.
            continue

        task_path = os.path.join(utils.task_dir(), task_id)
        history_tasks[task_id] = {
            "task_id": task_id,
            "subject": active_task.get("subject")
            or history_task.get("subject")
            or task_id,
            "state": const.TASK_STATE_PROCESSING,
            "progress": history_task.get("progress", 0),
            "mtime": active_task.get("mtime")
            or history_task.get("mtime", datetime.now().timestamp()),
            "task_path": task_path,
            "video_file": history_task.get("video_file", ""),
            "source": "active",
        }

    tasks = list(history_tasks.values())
    return sorted(tasks, key=lambda item: item["mtime"], reverse=True)[:limit]


def _open_task_path(task_path):
    tasks_root = os.path.abspath(utils.task_dir())
    normalized_path = os.path.abspath(task_path)
    if not normalized_path.startswith(tasks_root + os.sep):
        logger.warning(f"invalid task folder path: {normalized_path}")
        return
    if os.path.isdir(normalized_path):
        webbrowser.open(f"file://{normalized_path}")


def _open_task_video(video_file):
    tasks_root = os.path.abspath(utils.task_dir())
    normalized_file = os.path.abspath(video_file)

    # Os caminhos de vídeo vêm de verificações de diretório de tarefas ou status de tempo de execução. Ainda há uma restrição de que apenas o diretório de tarefas pode ser aberto.
    # arquivos dentro da UI para evitar que as operações da UI sejam expandidas por caminhos anormais em recursos arbitrários de abertura de arquivos locais.
    if not normalized_file.startswith(tasks_root + os.sep):
        logger.warning(f"invalid task video path: {normalized_file}")
        return
    if not os.path.isfile(normalized_file):
        logger.warning(f"task video does not exist: {normalized_file}")
        return

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", normalized_file])
        elif sys.platform.startswith("win"):
            os.startfile(normalized_file)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", normalized_file])
    except Exception as e:
        logger.error(f"failed to open task video: {normalized_file}, {e}")


def _delete_task(task_id, task_path, task_state=None):
    # O status de exibição da página pode ficar atrasado em relação às tarefas em segundo plano. Verifique também o status de entrada e a sessão atual antes de excluir
    # Tarefas ativas e status mais recente para evitar exclusão acidental quando uma tarefa acabou de ser iniciada ou um vídeo intermediário foi produzido.
    current_task = None
    try:
        current_task = sm.state.get_task(task_id)
    except Exception as e:
        logger.exception(f"failed to verify task state before deletion: {task_id}, {e}")
        return False

    task_snapshot = dict(current_task or {})
    task_snapshot.setdefault("state", task_state)
    if task_id in _active_generation_tasks():
        task_snapshot["state"] = const.TASK_STATE_PROCESSING

    if tm.is_task_busy(task_snapshot):
        logger.warning(f"refused to delete running task: {task_id}")
        return False

    tasks_root = os.path.abspath(utils.task_dir())
    normalized_path = os.path.abspath(task_path)

    # A exclusão de uma tarefa remove o status da tarefa e os arquivos de compilação locais. Isso deve ser limitado ao armazenamento/tarefas
    # para evitar a exclusão acidental de outros diretórios locais causada por task_path anormal.
    if not normalized_path.startswith(tasks_root + os.sep):
        logger.warning(f"invalid task folder path for deletion: {normalized_path}")
        return False

    try:
        if hasattr(sm.state, "delete_task"):
            sm.state.delete_task(task_id)
        if os.path.isdir(normalized_path):
            shutil.rmtree(normalized_path)
        logger.info(f"deleted task: {task_id}")
        return True
    except Exception as e:
        logger.exception(f"failed to delete task: {task_id}, {e}")
        return False


def _count_processing_tasks(tasks):
    # O portal de gerenciamento de tarefas superior só precisa exibir o número de tarefas "geradoras".
    # O julgamento da chave de estado interno é reutilizado aqui para evitar depender de direitos autorais de exibição em vários idiomas para causar inconsistência estatística em diferentes idiomas.
    processing_task_ids = {
        task["task_id"]
        for task in tasks
        if _task_state_filter_key(task) == "processing"
    }
    return len(processing_task_ids)


def _task_manager_label(processing_count):
    label = tr("Task Manager")
    if processing_count <= 0:
        return label
    return f"{label} · {processing_count}"


def _render_task_table(filtered_tasks, key_prefix):
    with st.container(key=f"task_table_header_{key_prefix}"):
        header_cols = st.columns([1.1, 1.7, 3.0, 0.8, 1.6], vertical_alignment="center")
        header_cols[0].caption(tr("Task Status"))
        header_cols[1].caption(tr("Task Updated At"))
        header_cols[2].caption(tr("Task Subject"))
        header_cols[3].caption(tr("Task Progress"))
        header_cols[4].caption(tr("Task Actions"))

    if not filtered_tasks:
        st.info(tr("No Tasks Match Filter"))
        return

    visible_tasks = filtered_tasks[:12]
    list_height = min(390, max(96, len(visible_tasks) * 58))
    with st.container(height=list_height, border=False):
        for task in visible_tasks:
            task_id = task["task_id"]
            has_video = bool(task["video_file"] and os.path.isfile(task["video_file"]))
            is_processing = _task_state_filter_key(task) == "processing"
            is_busy = is_processing or tm.is_task_busy(task)
            has_restore_data = os.path.isfile(
                os.path.join(task["task_path"], "script.json")
            )
            safe_task_key = "".join(ch if ch.isalnum() else "_" for ch in task_id)[:40]

            # Use o contêiner com borda nativo Streamlit + colunas para preservar as operações por linha.
            # Comparado com tabelas HTML/CSS personalizadas, este método é mais estável para alterações de versão do Streamlit;
            # Comparado ao dataframe, ele pode reter ações embutidas, como reproduzir, abrir diretórios e excluir.
            with st.container(
                key=f"task_row_{key_prefix}_{safe_task_key}", border=True
            ):
                row_cols = st.columns(
                    [1.1, 1.7, 3.0, 0.8, 1.6],
                    vertical_alignment="center",
                )
                row_cols[0].write(_task_state_label(task["state"], has_video))
                row_cols[1].write(_format_task_time(task["mtime"]))
                row_cols[2].write(_format_task_subject(task["subject"]))
                row_cols[3].write(f"{task['progress']}%")

                action_cols = row_cols[4].columns(
                    4,
                    vertical_alignment="center",
                    gap="small",
                )
                with action_cols[0]:
                    play_label = tr("Play")
                    if st.button(
                        play_label,
                        key=f"play_task_{key_prefix}_{task_id}",
                        use_container_width=True,
                        icon=":material/play_arrow:",
                        help=play_label,
                        disabled=not has_video,
                    ):
                        _open_task_video(task["video_file"])

                with action_cols[1]:
                    open_label = tr("Open Task Folder")
                    if st.button(
                        open_label,
                        key=f"open_task_{key_prefix}_{task_id}",
                        use_container_width=True,
                        icon=":material/folder_open:",
                        help=open_label,
                    ):
                        _open_task_path(task["task_path"])

                with action_cols[2]:
                    restore_label = tr("Regenerate Task")
                    if st.button(
                        restore_label,
                        key=f"restore_task_{key_prefix}_{task_id}",
                        use_container_width=True,
                        icon=":material/replay:",
                        help=restore_label,
                        disabled=is_processing or not has_restore_data,
                    ):
                        _queue_task_restore(task_id)

                with action_cols[3]:
                    delete_label = tr("Delete Task")
                    delete_help = (
                        f"{delete_label} ({tr('Task Status Processing')})"
                        if is_busy
                        else delete_label
                    )
                    if st.button(
                        delete_label,
                        key=f"delete_task_{key_prefix}_{task_id}",
                        use_container_width=True,
                        icon=":material/delete:",
                        help=delete_help,
                        disabled=is_busy,
                    ):
                        if _delete_task(task_id, task["task_path"], task["state"]):
                            st.toast(tr("Task Deleted"))
                            st.rerun()
                        else:
                            st.error(tr("Task Delete Failed"))


def _render_task_manager_panel(tasks=None):
    tasks = tasks if tasks is not None else _collect_task_summaries()
    if not tasks:
        st.info(tr("No Tasks Yet"))
        return

    # Streamlit 1.59 oferece suporte à renderização lenta de guias com estado. Somente a lista atual é reconstruída ao alternar,
    # Evite fragmentos programados para criar repetidamente quatro conjuntos de linhas de tarefas e botões de ação a cada dois segundos.
    status_tabs = [
        ("all", tr("All Tasks")),
        ("processing", tr("Task Status Processing")),
        ("complete", tr("Task Status Complete")),
        ("failed", tr("Task Status Failed")),
    ]
    tabs = st.tabs(
        [label for _, label in status_tabs],
        key="task_manager_status_tabs",
        on_change="rerun",
    )
    for (status_key, _), tab in zip(status_tabs, tabs):
        if not tab.open:
            continue
        with tab:
            filtered_tasks = [
                task
                for task in tasks
                if status_key == "all" or _task_state_filter_key(task) == status_key
            ]
            _render_task_table(filtered_tasks, status_key)


@st.fragment(run_every="2s")
def _render_task_manager_entry():
    # As tarefas podem ser acionadas pela página atual ou por outras páginas. A entrada é atualizada regularmente usando apenas fragmento.
    # Apenas o número da tarefa e o conteúdo do popover são atualizados, sem interromper a entrada do formulário da página principal.
    task_summaries = _collect_task_summaries()
    processing_task_count = _count_processing_tasks(task_summaries)
    with st.container(key="task_manager_entry", width="content"):
        with st.popover(
            _task_manager_label(processing_task_count),
            width="content",
            key=(
                "task_manager_popover_"
                f"{st.session_state.get('task_manager_popover_nonce', 0)}"
            ),
        ):
            _render_task_manager_panel(task_summaries)


def _load_task_restore_payload(task_id):
    tasks_root = os.path.realpath(utils.task_dir())
    task_path = os.path.realpath(os.path.join(tasks_root, str(task_id)))
    try:
        if os.path.commonpath([tasks_root, task_path]) != tasks_root:
            raise ValueError("task path is outside the task directory")
    except ValueError as e:
        logger.warning(f"invalid task restore path: {task_id}, {e}")
        return None

    script_data = _safe_load_task_script(task_path)
    raw_params = script_data.get("params")
    if not isinstance(raw_params, dict):
        logger.warning(f"task has no restorable parameters: {task_id}")
        return None

    params_input = dict(raw_params)
    if script_data.get("script"):
        params_input["video_script"] = script_data["script"]
    if script_data.get("search_terms"):
        params_input["video_terms"] = script_data["search_terms"]

    try:
        params = VideoParams.model_validate(params_input).model_dump(mode="json")
    except Exception as e:
        logger.warning(f"failed to validate task restore parameters: {task_id}, {e}")
        return None

    return {
        "task_id": str(task_id),
        "subject": params.get("video_subject") or script_data.get("script") or task_id,
        "params": params,
    }


def _infer_tts_server_from_voice(voice_name):
    if voice.is_no_voice(voice_name):
        return voice.NO_VOICE_NAME
    if voice.is_siliconflow_voice(voice_name):
        return "siliconflow"
    if voice.is_gemini_voice(voice_name):
        return "gemini-tts"
    if voice.is_mimo_voice(voice_name):
        return "mimo-tts"
    if voice.is_elevenlabs_voice(voice_name):
        return "elevenlabs"
    if voice.is_chatterbox_voice(voice_name):
        return "chatterbox"
    if voice.is_azure_v2_voice(voice_name):
        return "azure-tts-v2"
    return "azure-tts-v1"


def _set_stable_widget_value(key, value):
    if value is not None:
        st.session_state[localized_widget_key(key)] = value


def _apply_pending_task_restore():
    payload = st.session_state.pop("task_restore_payload", None)
    if not payload:
        return False

    params = payload["params"]
    video_terms = params.get("video_terms") or ""
    if isinstance(video_terms, list):
        video_terms = ", ".join(str(term) for term in video_terms)

    # Redação e configurações avançadas de script.
    st.session_state["video_subject"] = params.get("video_subject") or ""
    st.session_state["video_script"] = params.get("video_script") or ""
    st.session_state["video_terms"] = str(video_terms)
    _set_stable_widget_value(
        "script_language_select", params.get("video_language") or ""
    )
    st.session_state["paragraph_number_input"] = params.get("paragraph_number", 1)
    st.session_state["video_script_prompt"] = params.get("video_script_prompt") or ""
    st.session_state["custom_system_prompt"] = (
        params.get("custom_system_prompt") or llm.DEFAULT_SCRIPT_SYSTEM_PROMPT
    )

    # Configurações de vídeo. O controle de upload de material não pode ser gravado pelo servidor, portanto, os materiais locais precisam ser selecionados novamente pelo usuário.
    video_source = params.get("video_source") or "pexels"
    _set_stable_widget_value("video_source_select", video_source)
    _set_stable_widget_value(
        "video_concat_mode_select", params.get("video_concat_mode") or "random"
    )
    _set_stable_widget_value(
        "video_transition_mode_select",
        params.get("video_transition_mode") or VideoTransitionMode.none.value,
    )
    _set_stable_widget_value(
        f"video_aspect_for_{video_source}",
        params.get("video_aspect") or VideoAspect.portrait.value,
    )
    _set_stable_widget_value(
        "video_clip_duration_select", params.get("video_clip_duration", 3)
    )
    _set_stable_widget_value(
        "video_clip_speed_slider",
        # A API pode ser escrita mais rápido do que a WebUI pode suportar, e a fase de geração de tarefas é normalizada com segurança, mas
        # A história ainda pode manter seu valor original. Normalize novamente antes de retomar a tarefa para evitar dar Streamlit
        # A injeção de valores fora dos limites, NaN ou valores infinitos no controle deslizante causa status de controle anormal.
        utils.normalize_clip_speed(params.get("video_clip_speed", 1.0)),
    )
    _set_stable_widget_value("video_count_select", params.get("video_count", 1))
    st.session_state["match_materials_to_script"] = bool(
        params.get("match_materials_to_script", False)
    )

    # Configurações de áudio. O servidor TTS não grava tarefas antigas, inferidas com base no histórico voice_name.
    voice_name = params.get("voice_name") or voice.NO_VOICE_NAME
    tts_server = _infer_tts_server_from_voice(voice_name)
    if params.get("custom_audio_file"):
        voice_mode = VOICE_MODE_UPLOAD
    elif voice.is_no_voice(voice_name):
        voice_mode = VOICE_MODE_NONE
    else:
        voice_mode = VOICE_MODE_TTS
    _set_stable_widget_value("voice_mode_control", voice_mode)
    if tts_server != voice.NO_VOICE_NAME:
        _set_stable_widget_value("tts_server_select", tts_server)
        _set_stable_widget_value(f"speech_synthesis_select_{tts_server}", voice_name)
    _set_stable_widget_value("voice_volume_select", params.get("voice_volume", 1.0))
    _set_stable_widget_value("voice_rate_select", params.get("voice_rate", 1.0))
    bgm_type = params.get("bgm_type") or ""
    _set_stable_widget_value("bgm_type_select", bgm_type)
    _set_stable_widget_value("bgm_volume_select", params.get("bgm_volume", 0.2))
    st.session_state["custom_bgm_file_input"] = params.get("bgm_file") or ""
    st.session_state["sonilo_bgm_prompt_input"] = (
        params.get("video_music_prompt") or params.get("sonilo_bgm_prompt") or ""
    )
    st.session_state["elevenlabs_music_prompt_input"] = (
        params.get("video_music_prompt") or ""
    )

    # Configurações de legenda. Minimize os valores fora dos limites em tarefas antigas para evitar que o Slider falhe na inicialização.
    st.session_state["subtitle_enabled_checkbox"] = bool(
        params.get("subtitle_enabled", True)
    )
    _set_stable_widget_value("font_name_select", params.get("font_name") or "")
    _set_stable_widget_value(
        "subtitle_position_select", params.get("subtitle_position") or "bottom"
    )
    custom_position = min(100.0, max(0.0, float(params.get("custom_position", 70.0))))
    st.session_state["custom_position_input"] = str(custom_position)
    st.session_state["font_color_picker"] = params.get("text_fore_color") or "#FFFFFF"
    st.session_state["font_size_slider"] = min(
        100, max(30, int(params.get("font_size", 60)))
    )
    st.session_state["stroke_color_picker"] = params.get("stroke_color") or "#000000"
    st.session_state["stroke_width_slider"] = min(
        10.0, max(0.0, float(params.get("stroke_width", 1.5)))
    )
    background_color = params.get("text_background_color")
    background_enabled = bool(background_color)
    st.session_state["subtitle_background_enabled_checkbox"] = background_enabled
    if isinstance(background_color, str):
        st.session_state["subtitle_background_color_picker"] = background_color
    st.session_state["rounded_subtitle_background_checkbox"] = bool(
        params.get("rounded_subtitle_background", False) and background_enabled
    )

    st.session_state.pop("local_video_materials_uploader", None)
    # As tarefas históricas salvam apenas os caminhos dos materiais e não há garantia de que esses arquivos ainda existirão no ambiente atual.
    # Ao mesmo tempo, limpe os materiais carregados em cache na página atual para evitar o uso indevido de arquivos de outra tarefa após a recuperação.
    st.session_state["local_video_materials"] = []
    st.session_state.pop("custom_audio_file_uploader", None)
    st.session_state.pop("custom_bgm_uploader", None)
    st.session_state.pop("custom_bgm_validation", None)
    st.session_state["task_restore_upload_requirements"] = (
        _build_restore_upload_requirements(params)
    )

    st.session_state["task_restore_succeeded"] = True
    logger.info(f"restored task configuration: {payload['task_id']}")
    return True


def _dismiss_task_restore_dialog():
    st.session_state.pop("task_restore_candidate_id", None)


@st.dialog(
    tr("Regenerate Task"),
    width="small",
    on_dismiss=_dismiss_task_restore_dialog,
)
def _render_task_restore_dialog(task_id):
    payload = _load_task_restore_payload(task_id)
    if payload is None:
        st.error(tr("Task Restore Failed"))
        if st.button(tr("Cancel"), key="cancel_invalid_task_restore"):
            st.session_state.pop("task_restore_candidate_id", None)
            st.rerun(scope="app")
        return

    st.write(tr("Regenerate Task Confirmation"))
    st.caption(_format_task_subject(payload["subject"], max_length=80))
    cancel_col, load_col = st.columns(2)
    if cancel_col.button(
        tr("Cancel"),
        key="cancel_task_restore",
        use_container_width=True,
    ):
        st.session_state.pop("task_restore_candidate_id", None)
        st.rerun(scope="app")
    if load_col.button(
        tr("Load Task Configuration"),
        key="confirm_task_restore",
        type="primary",
        use_container_width=True,
    ):
        st.session_state["task_restore_payload"] = payload
        st.session_state.pop("task_restore_candidate_id", None)
        st.rerun(scope="app")


def _dismiss_settings_dialog():
    """Feche o pop-up de configurações e certifique-se de que a próxima reexecução da página inteira não a abra automaticamente novamente."""
    st.session_state["settings_dialog_open"] = False


def _render_brand(available_update: str | None = None):
    """Renderize o nome do projeto, a versão atual e a entrada de atualização opcional."""
    update_link = ""
    if available_update:
        update_label = html.escape(
            tr("Update Available").format(version=available_update)
        )
        # Streamlit continuará analisando o HTML recebido usando Markdown. Mantenha o link como uma única linha aqui,
        # Impede que o recuo de strings multilinhas seja reconhecido como blocos de código, fazendo com que a página exiba diretamente o código-fonte HTML.
        update_link = (
            '<a class="mpt-brand__update" '
            f'href="{version_checker.LATEST_RELEASE_PAGE_URL}" '
            'target="_blank" rel="noopener noreferrer" '
            f'aria-label="{update_label}" title="{update_label}">'
            f"{update_label}</a>"
        )
    st.markdown(
        f"""
        <h1 class="mpt-brand">
            <span class="mpt-brand__name">MoneyPrinterTurbo</span>
            <a class="mpt-brand__version"
               href="https://github.com/harry0703/MoneyPrinterTurbo"
               target="_blank"
               rel="noopener noreferrer"
               aria-label="Open MoneyPrinterTurbo on GitHub"
               title="Open project on GitHub">v{html.escape(str(config.project_version))}</a>
            {update_link}
        </h1>
        """,
        unsafe_allow_html=True,
    )


@st.fragment(run_every="1s")
def _render_pending_version_check():
    """Atualize a área da marca somente quando a verificação não for concluída para evitar o bloqueio ou a execução repetida de todo o formulário da página."""
    snapshot = version_checker.poll_available_update(config.project_version)
    if snapshot.complete:
        # Após a conclusão da verificação, atualize a página inteira, altere a barra superior para renderização estática e interrompa a pesquisa de fragmentos.
        # Essa atualização ocorre após a conclusão da solicitação em segundo plano e não atrasa outros conteúdos da página inicial.
        st.rerun(scope="app")
    _render_brand()


def _render_top_bar():
    """Renderize a barra superior da página que consiste em marca, gerenciamento de tarefas, configurações e troca de idioma."""
    # A barra superior está dividida em duas áreas independentes: área de marca e área de operação. Tela estreita da Streamlit
    # Envolva as duas áreas como um todo e, em seguida, envolva automaticamente o interior da área de operação de acordo com a largura restante.
    with st.container(key="top_bar"):
        brand_col, actions_col = st.columns(
            [3.5, 2.0],
            vertical_alignment="center",
            gap="small",
        )

    with brand_col:
        update_snapshot = version_checker.poll_available_update(
            config.project_version
        )
        if update_snapshot.complete:
            _render_brand(update_snapshot.available_version)
        else:
            _render_pending_version_check()

    with actions_col:
        with st.container(
            key="top_bar_actions",
            horizontal=True,
            horizontal_alignment="right",
            vertical_alignment="center",
            gap="small",
            width="stretch",
        ):
            _render_task_manager_entry()

            if st.button(
                tr("Settings"),
                key="open_settings_dialog_button",
                type="secondary",
                icon=":material/settings:",
                width="content",
            ):
                st.session_state["settings_dialog_open"] = True

            language_codes = list(locales.keys())
            selected_index = 0
            for i, code in enumerate(language_codes):
                if code == st.session_state.get("ui_language", ""):
                    selected_index = i

            selected_language_code = st.selectbox(
                "Language / Idioma",
                options=language_codes,
                index=selected_index,
                format_func=lambda code: locales[code].get("Language", code),
                key="top_language_code_selector",
                label_visibility="collapsed",
                width=180,
            )
            if selected_language_code:
                previous_language = st.session_state.get("ui_language", "")
                if selected_language_code != previous_language:
                    logger.info(
                        "UI language changed by user: "
                        f"previous_language={previous_language or '<empty>'}, "
                        f"selected_language={selected_language_code}"
                    )
                    st.session_state["ui_language"] = selected_language_code
                    # O reconhecimento automático do navegador afeta apenas a sessão atual; somente quando o usuário alterna ativamente a caixa suspensa
                    # Escreva em config.toml e as novas sessões subsequentes terão precedência sobre esta seleção explícita.
                    config.ui["language"] = selected_language_code
                    config.save_config()
                    # Forçar a atualização após mudar de idioma para evitar que a caixa de seleção continue exibindo a cópia do idioma antigo.
                    st.rerun()


support_locales = [
    "zh-CN",
    "zh-HK",
    "zh-TW",
    "de-DE",
    "en-US",
    "es-ES",
    "fr-FR",
    "ru-RU",
    "vi-VN",
    "th-TH",
    "tr-TR",
]


# -----------------------------------------------------------------------------
# Componentes de UI comuns, cache de recursos e registro em log
# -----------------------------------------------------------------------------


@st.cache_data(ttl=30, show_spinner=False)
def get_all_fonts():
    # O diretório de fontes raramente muda, mas o Streamlit executa novamente a página sempre que há interação com o controle. cache de curto prazo
    # Ele pode evitar a repetição contínua de os.walk e garantir que a fonte recém-adicionada possa ser descoberta em até 30 segundos.
    fonts = []
    for root, dirs, files in os.walk(font_dir):
        for file in files:
            if file.endswith(".ttf") or file.endswith(".ttc"):
                fonts.append(file)
    fonts.sort()
    return fonts


@st.cache_data(ttl=30, show_spinner=False)
def get_all_songs():
    # A música de fundo e as fontes usam a mesma estratégia de ciclo curto, sem cache permanente, levando em consideração o desempenho de reexecução e
    # Cenário em que o usuário adiciona manualmente arquivos de música durante o tempo de execução.
    songs = []
    for root, dirs, files in os.walk(song_dir):
        for file in files:
            if file.endswith(".mp3"):
                songs.append(file)
    return songs


def open_task_folder(task_id):
    try:
        # task_id deve sempre ser um UUID gerado pelo servidor. Aqui fazemos primeiro a verificação do formato para evitar valores discrepantes.
        # Acesse locais fora do diretório de tarefas por meio da emenda de caminho e evite o acionamento quando o diretório for aberto posteriormente.
        # A interpretação de caracteres especiais do shell da plataforma.
        normalized_task_id = str(UUID(str(task_id)))
        tasks_root = os.path.abspath(os.path.join(root_dir, "storage", "tasks"))
        path = os.path.abspath(os.path.join(tasks_root, normalized_task_id))

        # Mesmo que a verificação do UUID seja aprovada, confirme novamente se o caminho final ainda está no diretório raiz da tarefa para evitar
        # O risco de cruzamento de caminho será introduzido quando o chamador ajustar a fonte de task_id no futuro.
        if not path.startswith(tasks_root + os.sep):
            logger.warning(f"invalid task folder path: {path}")
            return

        if os.path.isdir(path):
            webbrowser.open(f"file://{path}")
    except Exception as e:
        logger.exception(f"failed to open task folder: task_id={task_id}, error={e}")


@st.cache_resource
def init_log():
    # O manipulador de log básico é um recurso em nível de processo, não um estado de sessão de página. Streamlit por componente
    # A interação executará novamente o script da página e o recarregamento a quente do código também poderá invalidar o cache. A inicialização do log só pode
    # Substitui exatamente o manipulador de terminal e não pode limpar o manipulador temporário WebUI usado pela tarefa que está sendo gerada.
    _lvl = "DEBUG"

    return configure_terminal_logger(
        sys.stdout,
        level=_lvl,
        colorize=True,
    )


init_log()


def tr_optional(key, fallback_language=""):
    loc = locales.get(st.session_state["ui_language"], {})
    value = loc.get("Translation", {}).get(key, "")
    if not value and fallback_language:
        fallback_loc = locales.get(fallback_language, {})
        value = fallback_loc.get("Translation", {}).get(key, "")
    return value if value else ""


def render_onboarding_tour():
    # O guia cobre apenas as três entradas estáveis ​​e não tenta controlar caixas de diálogo, guias ou formulários comerciais. Isto permitirá
    # Novos usuários entendem o processo completo e não associam o estado de inicialização ao ciclo de vida dinâmico do componente Streamlit.
    steps = [
        Tour.bind(
            "open_settings_dialog_button",
            title=tr("Onboarding Model Settings Title"),
            desc=tr("Onboarding Model Settings Description"),
            side="bottom",
            align="end",
        ),
        Tour.bind(
            "main_settings_grid",
            title=tr("Onboarding Creation Settings Title"),
            desc=tr("Onboarding Creation Settings Description"),
            side="top",
            align="center",
        ),
        Tour.bind(
            "generate_video_button",
            title=tr("Onboarding Generate Video Title"),
            desc=tr("Onboarding Generate Video Description"),
            side="top",
            align="center",
        ),
    ]

    # streamlit-tour 1.1.0 não expõe a cópia de navegação nos parâmetros de construção do Python, mas o subjacente
    # Driver.js suporta a substituição do texto do botão na configuração popover em cada etapa. A localização é injetada uniformemente aqui
    # Copie e escape HTML do conteúdo porque o componente renderizará esses campos por meio de innerHTML.
    previous_text = html.escape(tr("Onboarding Previous"))
    next_text = html.escape(tr("Onboarding Next"))
    done_text = html.escape(tr("Onboarding Done"))
    for index, step in enumerate(steps):
        step.popover["prevBtnText"] = f"&larr; {previous_text}"
        # Driver.js substituirá o modelo de progresso que substituiu as variáveis ​​ao mesclar a configuração de etapa única, portanto, diretamente
        # Escreva a etapa atual e o número total de etapas para evitar que a página mostre espaços reservados {{atual}} não resolvidos.
        step.popover["progressText"] = f"{index + 1} / {len(steps)}"
        if index == len(steps) - 1:
            step.popover["doneBtnText"] = done_text
        else:
            step.popover["nextBtnText"] = f"{next_text} &rarr;"

    tour = Tour(
        steps=steps,
        key=ONBOARDING_TOUR_KEY,
        show_progress=True,
        animate=True,
        overlay_opacity=0.55,
        one_time_tour=True,
    )

    # Cada sessão do Streamlit é iniciada ativamente apenas uma vez. Se foi concluído é determinado pelo componente por meio do navegador.
    # Julgamento localStorage para evitar reexecução de página ou interação de controle comum de aparecer repetidamente na inicialização.
    auto_start_key = f"{ONBOARDING_TOUR_KEY}-auto-started"
    if not st.session_state.get(auto_start_key, False):
        st.session_state[auto_start_key] = True
        tour.start()


def _render_generation_logs(task_id):
    """Renderiza instantâneos de log de tarefas em segundo plano sem acessar o estado da sessão Streamlit a partir de threads de trabalho."""
    if config.ui.get("hide_log", False):
        return

    log_records = webui_task.get_task_logs(task_id)
    if not log_records:
        return

    st.code("\n".join(log_records))


def _render_generation_task_snapshot(task_id, task):
    """Renderize o progresso, o motivo da falha ou o filme final com base em instantâneos no armazenamento de estado."""
    if not task:
        st.info(tr("Generating Video"))
        _render_generation_logs(task_id)
        return

    state = _normalize_task_state(task.get("state"))
    progress = max(0, min(100, int(task.get("progress", 0) or 0)))
    if state == const.TASK_STATE_PROCESSING:
        st.info(tr("Generating Video"))
        st.progress(
            progress,
            text=f"{tr('Task Progress')}: {progress}%",
        )
        _render_generation_logs(task_id)
        return

    if state == const.TASK_STATE_FAILED:
        error = str(task.get("error") or "").strip()
        message = tr("Video Generation Failed")
        st.error(f"{message}: {error}" if error else message)
        _render_generation_logs(task_id)
        return

    video_files = task.get("videos") or []
    if state != const.TASK_STATE_COMPLETE or not video_files:
        st.error(tr("Video Generation Failed"))
        _render_generation_logs(task_id)
        return

    st.success(tr("Video Generation Completed"))
    for warning in task.get("warnings") or []:
        if isinstance(warning, Mapping) and warning.get("code") == "sonilo_bgm_failed":
            st.warning(
                tr("Sonilo BGM Fallback Warning").format(
                    index=warning.get("video_index", "")
                )
            )
        elif (
            isinstance(warning, Mapping)
            and warning.get("code") == "elevenlabs_bgm_failed"
        ):
            st.warning(
                tr("ElevenLabs BGM Fallback Warning").format(
                    index=warning.get("video_index", "")
                )
            )
        else:
            st.warning(str(warning))

    try:
        player_cols = st.columns(len(video_files) * 2 + 1)
        for i, url in enumerate(video_files):
            player_cols[i * 2 + 1].video(url)
    except Exception as exc:
        logger.exception(
            f"failed to render generated video preview: task_id={task_id}, "
            f"video_files={video_files}, error={exc}"
        )

    _render_generation_logs(task_id)
    if st.session_state.get("opened_generation_task_id") != task_id:
        # O processo de sincronização original abrirá automaticamente o diretório de tarefas após a conclusão da geração. O fragmento pode ser executado repetidamente,
        # Portanto, use tags de sessão para garantir que cada tarefa seja aberta apenas uma vez para evitar pop-ups contínuos do Finder/Explorer.
        st.session_state["opened_generation_task_id"] = task_id
        open_task_folder(task_id)
        logger.info(f"{tr('Video Generation Completed')}: task_id={task_id}")


@st.fragment(run_every=webui_task.TASK_LOG_REFRESH_INTERVAL_SECONDS)
def _render_running_generation_task(task_id):
    """Pesquise apenas enquanto a tarefa está em execução; volte para resultados estáticos após a conclusão da tarefa para interromper atualizações agendadas desnecessárias."""
    try:
        task = sm.state.get_task(task_id)
    except Exception as exc:
        logger.exception(
            f"failed to query WebUI generation task: task_id={task_id}, error={exc}"
        )
        st.error(tr("Video Generation Failed"))
        return

    state = _normalize_task_state((task or {}).get("state"))
    if state in {const.TASK_STATE_COMPLETE, const.TASK_STATE_FAILED}:
        _remove_active_generation_task(task_id)
        # Scripts de página inteira agora não têm lógica de geração demorada e podem ser executados novamente com segurança e alterar os resultados para estáticos
        # renderizar. Dessa forma, o navegador não reterá permanentemente um fragmento de pesquisa de dois segundos após a conclusão da tarefa.
        st.rerun(scope="app")

    _render_generation_task_snapshot(task_id, task)


def _render_current_generation_task():
    """Restaure a UI consultável das tarefas enviadas mais recentemente para a página atual abaixo do botão gerar."""
    task_id = st.session_state.get("current_generation_task_id", "")
    if not task_id:
        return

    try:
        task = sm.state.get_task(task_id)
    except Exception as exc:
        logger.exception(
            f"failed to query current WebUI task: task_id={task_id}, error={exc}"
        )
        st.error(tr("Video Generation Failed"))
        return

    state = _normalize_task_state((task or {}).get("state"))
    if state in {const.TASK_STATE_COMPLETE, const.TASK_STATE_FAILED}:
        _remove_active_generation_task(task_id)
        _render_generation_task_snapshot(task_id, task)
        return

    _render_running_generation_task(task_id)


def get_llm_provider_tips(provider_id, **kwargs):
    # A cópia da descrição do provedor LLM usa uniformemente a regra `llm_provider_tips.<provider_id>`.
    # Desta forma, ao adicionar um provedor, basta preencher a cópia no locale; se não houver cópia, o bloco de prompt não será exibido.
    # Evite empilhar um grande número de instruções codificadas em chinês e inglês em Main.py.
    provider = get_llm_provider(provider_id)
    if provider is None:
        return ""

    # As instruções de configuração do provedor mantêm atualmente dois conjuntos de modelos padrão em chinês e inglês; outras linguagens de interface
    # Use o inglês uniformemente para evitar a dessincronização de longo prazo após copiar o inglês na localidade. Um determinado idioma será concluído mais tarde.
    # Depois de totalmente traduzido, ele será adicionado ao escopo de manutenção independente aqui.
    ui_language = st.session_state.get("ui_language", "en")
    tips_language = ui_language if ui_language in {"zh", "en"} else "en"
    tips = (
        locales.get(tips_language, {}).get("Translation", {}).get(provider.tips_key, "")
    )
    if not tips:
        return tips

    format_context = {
        "api_key_url": provider.api_key_url,
        "default_model": provider.default_model,
        "default_base_url": provider.default_base_url,
        **{
            f"default_{field.config_suffix}": field.default_value
            for field in provider.extra_fields
        },
        **kwargs,
    }
    try:
        return tips.format(**format_context)
    except Exception as e:
        logger.warning(f"format llm provider tips failed: {provider_id}, {e}")
        return tips


def get_llm_provider_label(provider):
    return tr_optional(provider.label_key) or provider.default_label


def get_tts_provider_tips(provider_id):
    # As instruções de configuração do TTS adotam a mesma estratégia de manutenção do LLM Provider: apenas chinês e inglês são mantidos.
    # Outros idiomas da interface voltam ao inglês para evitar a dessincronização de longo prazo após a cópia.
    ui_language = st.session_state.get("ui_language", "en")
    tips_language = ui_language if ui_language in {"zh", "en"} else "en"
    return (
        locales.get(tips_language, {})
        .get("Translation", {})
        .get(f"tts_provider_tips.{provider_id}", "")
    )


def localized_widget_key(name, *parts):
    # Algumas caixas de seleção do Streamlit usam chaves estáveis ​​para lembrar o estado da seleção, mas exibem texto do local.
    # Ao trocar de idioma, coloque o idioma na tecla para forçar a reconstrução do controle para evitar que o item selecionado ainda exiba o idioma antigo.
    language = st.session_state.get("ui_language", config.ui.get("language", ""))
    suffix_parts = [name, language, *[str(part) for part in parts if part]]
    return "_".join(suffix_parts)


def stable_selectbox(label, options, default_value, key, format_func=None, **kwargs):
    # Streamlit 1.59 é mais sensível à reutilização do estado selectbox: se o controle não tiver uma chave fixa,
    # Ou as opções reais são apenas um conjunto de subscritos temporários, que são facilmente substituídos pelo índice recalculado após a página ser executada novamente.
    # O problema é que a primeira seleção do usuário não entra em vigor e precisa ser selecionada novamente. Este ajudante usa valores de negócios estáveis ​​de maneira uniforme
    # Como opção real, salve o valor em session_state; exibir cópia somente através de format_func
    # Transforme para evitar que a cópia da tradução, a ordem das opções ou as alterações de configuração upstream afetem o status da seleção.
    options = list(options)
    if not options:
        raise ValueError(f"selectbox options cannot be empty: {key}")

    if default_value not in options:
        default_value = options[0]

    widget_key = localized_widget_key(key)
    selected_value = st.session_state.get(widget_key)
    if selected_value not in options:
        # Se as opções upstream mudarem (por exemplo, a lista de sons mudar após mudar de provedor de TTS),
        # O valor antigo não é mais válido. Inicialize session_state diretamente antes de o controle ser criado e deixe apenas a chave
        # O status de gerenciamento não é mais passado para o índice ao mesmo tempo. Isso evita Streamlit quando executado novamente
        # O valor recém-selecionado pelo usuário é substituído pelo índice recalculado, fazendo com que a primeira seleção não tenha efeito.
        st.session_state[widget_key] = default_value

    if format_func is None:
        format_func = str

    return st.selectbox(
        label,
        options=options,
        format_func=format_func,
        key=widget_key,
        **kwargs,
    )


def sync_script_order_concat_mode():
    """Corrigido o uso de emenda sequencial quando a correspondência de sequência de cópia está ativada e restaura a seleção original quando desativada."""
    widget_key = localized_widget_key("video_concat_mode_select")
    previous_key = "video_concat_mode_before_script_order_match"
    match_script_order = bool(st.session_state.get("match_materials_to_script", False))

    if match_script_order:
        current_mode = st.session_state.get(widget_key, VideoConcatMode.random.value)
        if current_mode != VideoConcatMode.sequential.value:
            st.session_state[previous_key] = current_mode
        st.session_state[widget_key] = VideoConcatMode.sequential.value
        return

    previous_mode = st.session_state.pop(previous_key, None)
    if previous_mode in {
        VideoConcatMode.sequential.value,
        VideoConcatMode.random.value,
    }:
        st.session_state[widget_key] = previous_mode


def reset_script_system_prompt():
    """Restaure as palavras do prompt do sistema nas configurações avançadas de script para o conteúdo padrão da versão atual."""
    st.session_state["custom_system_prompt"] = llm.DEFAULT_SCRIPT_SYSTEM_PROMPT


def reset_subtitle_settings():
    """Restaure os valores padrão nos controles de legenda do WebUI e na configuração de persistência."""
    defaults = DEFAULT_SUBTITLE_SETTINGS
    st.session_state["subtitle_enabled_checkbox"] = defaults["subtitle_enabled"]
    _set_stable_widget_value("font_name_select", defaults["font_name"])
    _set_stable_widget_value("subtitle_position_select", defaults["subtitle_position"])
    st.session_state["custom_position_input"] = str(defaults["custom_position"])
    st.session_state["font_color_picker"] = defaults["text_fore_color"]
    st.session_state["font_size_slider"] = defaults["font_size"]
    st.session_state["stroke_color_picker"] = defaults["stroke_color"]
    st.session_state["stroke_width_slider"] = defaults["stroke_width"]
    st.session_state["subtitle_background_enabled_checkbox"] = defaults[
        "subtitle_background_enabled"
    ]
    st.session_state["subtitle_background_color_picker"] = defaults[
        "subtitle_background_color"
    ]
    st.session_state["rounded_subtitle_background_checkbox"] = defaults[
        "rounded_subtitle_background"
    ]

    # A sincronização de opções de UI persistentes garante que as configurações padrão permaneçam ao atualizar a página após a recuperação.
    for key in (
        "font_name",
        "subtitle_position",
        "custom_position",
        "text_fore_color",
        "font_size",
        "subtitle_background_enabled",
        "subtitle_background_color",
        "rounded_subtitle_background",
    ):
        config.ui[key] = defaults[key]


@st.dialog(tr("Final Prompt Preview"), width="large")
def render_script_prompt_preview(prompt):
    """Exibe a palavra completa do prompt de geração do script que será enviada ao modelo grande."""
    st.code(prompt, language="markdown", wrap_lines=True)


def stable_segmented_control(
    label, options, default_value, key, format_func=None, **kwargs
):
    """Use valores de negócios estáveis ​​para criar controles segmentados de seleção de rádio para evitar que o status seja substituído pela cópia de exibição após a troca de idioma."""
    options = list(options)
    if not options:
        raise ValueError(f"segmented control options cannot be empty: {key}")

    if default_value not in options:
        default_value = options[0]

    widget_key = localized_widget_key(key)
    if st.session_state.get(widget_key) not in options:
        st.session_state[widget_key] = default_value

    return st.segmented_control(
        label,
        options=options,
        selection_mode="single",
        required=True,
        format_func=format_func or str,
        key=widget_key,
        **kwargs,
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_groq_model_ids(api_key: str, base_url: str) -> list[str]:
    if not api_key:
        return []

    normalized_base_url = (
        (base_url or "https://api.groq.com/openai/v1").strip().rstrip("/")
    )
    models_url = f"{normalized_base_url}/models"

    try:
        response = requests.get(
            models_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", [])

        model_ids = []
        for item in data:
            if isinstance(item, dict):
                model_id = item.get("id")
                if isinstance(model_id, str) and model_id.strip():
                    model_ids.append(model_id.strip())

        return sorted(set(model_ids))
    except Exception as e:
        logger.warning(f"failed to fetch groq models: {e}")
        return []


def _get_material_api_keys(config_key):
    """Converta a chave de API do material na configuração em uma string editável da WebUI."""
    api_keys = config.app.get(config_key, [])
    if isinstance(api_keys, str):
        api_keys = [api_keys]
    return ", ".join(api_keys)


def _save_material_api_keys(config_key, value):
    """Salve chaves de API de materiais separadas por vírgula e permita que o usuário limpe explicitamente a configuração antiga."""
    normalized_value = value.replace(" ", "")
    config.app[config_key] = normalized_value.split(",") if normalized_value else []


def _format_file_size(size_bytes):
    """Formate a contagem de bytes em texto compacto adequado para exibição na página de configurações."""
    size = float(max(0, size_bytes))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


@st.cache_data(ttl=30, show_spinner=False)
def _get_video_cache_stats(max_age_days=None):
    """
    Armazene estatísticas de diretório em cache em um curto período para evitar a verificação repetida de um grande número de arquivos, interagindo com controles comuns na janela pop-up.

    A chave de cache contém dias de limpeza, portanto, os intervalos de troca serão verificados apenas uma vez por intervalo; atualizar ou limpar proativamente
    Ele será explicitamente limpo quando concluído e o cache por até 30 segundos não afetará a verificação secundária durante a exclusão real.
    """
    return cache_manager.get_video_cache_stats(max_age_days=max_age_days)


def _render_cache_management_settings(panel):
    """Renderizar estatísticas, visualizar e operações de limpeza de segurança para o cache padrão de material de vídeo online."""
    with panel:
        cleanup_message = st.session_state.pop("video_cache_cleanup_message", None)
        if cleanup_message:
            message_type, message = cleanup_message
            if message_type == "success":
                st.success(message)
            else:
                st.warning(message)

        st.caption(tr("Video Cache Directory"))
        st.code(cache_manager.video_cache_dir(), language="text")

        total_stats = _get_video_cache_stats()
        metric_count, metric_size, metric_oldest = st.columns(3)
        metric_count.metric(tr("Cache File Count"), total_stats.file_count)
        metric_size.metric(
            tr("Cache Total Size"), _format_file_size(total_stats.total_size)
        )
        oldest_text = (
            datetime.fromtimestamp(total_stats.oldest_mtime).strftime("%Y-%m-%d")
            if total_stats.oldest_mtime is not None
            else "-"
        )
        metric_oldest.metric(tr("Oldest Cache Date"), oldest_text)

        st.caption(tr("Video Cache Management Help"))
        cleanup_options = (30, 7, 90, None)
        cleanup_labels = {
            30: tr("Cache Older Than 30 Days"),
            7: tr("Cache Older Than 7 Days"),
            90: tr("Cache Older Than 90 Days"),
            None: tr("All Video Cache"),
        }
        max_age_days = st.selectbox(
            tr("Cache Cleanup Range"),
            options=cleanup_options,
            format_func=lambda value: cleanup_labels[value],
            key="video_cache_cleanup_range",
        )
        cleanup_preview = _get_video_cache_stats(max_age_days=max_age_days)
        st.info(
            tr("Cache Cleanup Preview").format(
                count=cleanup_preview.file_count,
                size=_format_file_size(cleanup_preview.total_size),
            )
        )

        confirm_nonce = st.session_state.get("video_cache_cleanup_confirm_nonce", 0)
        confirmed = st.checkbox(
            tr("Confirm Cache Cleanup"),
            key=f"video_cache_cleanup_confirm_{confirm_nonce}",
        )
        refresh_col, open_col, cleanup_col = st.columns(3)
        if refresh_col.button(
            tr("Refresh Cache Stats"),
            key="refresh_video_cache_stats",
            use_container_width=True,
            icon=":material/refresh:",
        ):
            _get_video_cache_stats.clear()
            st.rerun(scope="fragment")

        if open_col.button(
            tr("Open Cache Directory"),
            key="open_video_cache_directory",
            use_container_width=True,
            icon=":material/folder_open:",
        ):
            webbrowser.open(Path(cache_manager.video_cache_dir()).as_uri())

        cleanup_disabled = not confirmed or cleanup_preview.file_count == 0
        if cleanup_col.button(
            tr("Clean Cache Now"),
            key="clean_video_cache_now",
            type="primary",
            disabled=cleanup_disabled,
            use_container_width=True,
            icon=":material/delete_sweep:",
        ):
            result = cache_manager.clean_video_cache(max_age_days=max_age_days)
            message_key = (
                "Cache Cleanup Completed With Failures"
                if result.failed_count
                else "Cache Cleanup Completed"
            )
            st.session_state["video_cache_cleanup_message"] = (
                "warning" if result.failed_count else "success",
                tr(message_key).format(
                    count=result.deleted_count,
                    size=_format_file_size(result.deleted_size),
                    failed=result.failed_count,
                ),
            )
            # Streamlit não permite que session_state com o mesmo nome seja modificado após o controle ser instanciado. incrementando
            # nonce permite que a próxima execução do próximo fragmento crie novos controles não verificados para evitar a limpeza após a conclusão
            # O status de confirmação de perigo é mantido.
            st.session_state["video_cache_cleanup_confirm_nonce"] = confirm_nonce + 1
            _get_video_cache_stats.clear()
            st.rerun(scope="fragment")


# -----------------------------------------------------------------------------
# Janela pop-up de configurações e prompt de palavra
# -----------------------------------------------------------------------------


# A configuração é uma operação de baixa frequência. Utilize uma caixa de diálogo de tamanho médio para evitar ocupar por muito tempo o espaço vertical da página principal.
# Ao mesmo tempo, controle a largura da linha de leitura para evitar que a janela pop-up pareça muito solta em dispositivos de tela ampla.
# A caixa de diálogo herda o comportamento do fragmento e a interação do controle interno apenas redesenha a janela pop-up; a configuração é salva separadamente no final da função.
# Acione a sincronização de página inteira por meio de retorno de chamada ao fechar para garantir que o processo de geração leia as configurações mais recentes do provedor e da interface.
@st.dialog(
    tr("Settings"),
    width="medium",
    on_dismiss=_dismiss_settings_dialog,
)
def _render_settings_dialog():
    with st.container():
        # Histórico hide_config é usado apenas para ocultar o antigo painel de configurações básicas. Depois de mudar para uma entrada de configuração fixa, o valor
        # Ela não tem mais significado visível ao usuário e é migrada uniformemente para false para evitar que a configuração antiga afete as versões subsequentes.
        config.app["hide_config"] = False
        (
            middle_config_panel,
            right_config_panel,
            cache_config_panel,
            left_config_panel,
        ) = st.tabs(
            [
                tr("LLM Settings Tab"),
                tr("Material API Tab"),
                tr("Cache Management Tab"),
                tr("Interface Settings Tab"),
            ]
        )

        # Painel esquerdo - Configurações de registro
        with left_config_panel:
            hide_log = st.checkbox(
                tr("Hide Log"),
                value=config.ui.get("hide_log", False),
                key="hide_log_checkbox",
            )
            config.ui["hide_log"] = hide_log

        _render_cache_management_settings(cache_config_panel)

        # Painel intermediário - Configuração LLM

        with middle_config_panel:
            # A ordem suspensa, o rótulo padrão e o ID do provedor estável vêm do Registro; localidade
            # Apenas a cópia de exibição é coberta e Main.py não mantém mais uma segunda lista de provedores.
            llm_provider_ids = [
                provider.provider_id for provider in LLM_PROVIDER_REGISTRY
            ]
            llm_provider_labels = {
                provider.provider_id: get_llm_provider_label(provider)
                for provider in LLM_PROVIDER_REGISTRY
            }
            saved_llm_provider = config.app.get(
                "llm_provider", DEFAULT_LLM_PROVIDER_ID
            ).lower()
            if saved_llm_provider not in llm_provider_ids:
                saved_llm_provider = DEFAULT_LLM_PROVIDER_ID

            llm_provider = stable_selectbox(
                tr("LLM Provider"),
                options=llm_provider_ids,
                default_value=saved_llm_provider,
                key="llm_provider_select",
                format_func=lambda provider_id: llm_provider_labels[provider_id],
            )
            # Exiba o formulário de configuração e a descrição do Provedor lado a lado, reduzindo quebras de linha em descrições longas em colunas estreitas.
            # Ao mesmo tempo, aproveite ao máximo o espaço horizontal do painel de configurações básicas.
            llm_form_panel, llm_help_panel = st.columns(
                [0.9, 1.1],
                gap="large",
                vertical_alignment="top",
            )
            llm_helper = llm_help_panel.container()
            config.app["llm_provider"] = llm_provider
            llm_provider_spec = get_llm_provider(llm_provider)
            if llm_provider_spec is None:
                # Em circunstâncias normais, todas as opções suspensas vêm do Registro e não entrarão neste ramo; reservado
                # Erros explícitos são usados ​​para diagnosticar estado de sessão corrompido ou acesso subsequente perdido.
                raise RuntimeError(f"unsupported llm provider: {llm_provider}")

            llm_api_key = config.app.get(llm_provider_spec.config_key("api_key"), "")
            llm_base_url = (
                config.app.get(llm_provider_spec.config_key("base_url"), "")
                or llm_provider_spec.default_base_url
            )
            llm_default_base_url = llm_provider_spec.default_base_url
            llm_model_name = llm_provider_spec.resolve_model_name(
                config.app.get(llm_provider_spec.config_key("model_name"), "")
            )

            provider_tip_context = {}
            if llm_provider == "ollama":
                llm_default_base_url = config.get_default_ollama_base_url()
                if not llm_base_url:
                    llm_base_url = llm_default_base_url
                docker_hint = ""
                if config.is_running_in_container():
                    docker_hint = tr_optional(
                        "llm_provider_tips.ollama.docker_hint",
                        fallback_language="en",
                    )
                provider_tip_context["docker_hint"] = docker_hint

            tips = get_llm_provider_tips(llm_provider, **provider_tip_context)
            if tips:
                with llm_helper:
                    st.info(tips)

            st_llm_api_key = llm_api_key
            if llm_provider_spec.show_api_key:
                st_llm_api_key = llm_form_panel.text_input(
                    tr("API Key"),
                    value=llm_api_key,
                    type="password",
                    key=f"{llm_provider}_api_key_input",
                )

            st_llm_base_url = llm_base_url
            if llm_provider_spec.show_base_url:
                st_llm_base_url = llm_form_panel.text_input(
                    tr("Base Url"),
                    value=llm_base_url,
                    key=f"{llm_provider}_base_url_input",
                )
            st_llm_model_name = ""
            if llm_provider == "groq":
                effective_api_key = st_llm_api_key or llm_api_key
                effective_base_url = st_llm_base_url or llm_base_url
                groq_models = get_groq_model_ids(
                    api_key=effective_api_key,
                    base_url=effective_base_url,
                )

                if groq_models:
                    selected_index = 0
                    if llm_model_name in groq_models:
                        selected_index = groq_models.index(llm_model_name)

                    st_llm_model_name = llm_form_panel.selectbox(
                        tr("Model Name"),
                        options=groq_models,
                        index=selected_index,
                        key="groq_model_name_select",
                    )
                else:
                    st_llm_model_name = llm_form_panel.text_input(
                        tr("Model Name"),
                        value=llm_model_name,
                        key="groq_model_name_input",
                    )
                    if effective_api_key:
                        llm_form_panel.caption(tr("Groq Model List Load Failed"))
                    else:
                        llm_form_panel.caption(
                            tr("Groq API Key Required for Model List")
                        )
            else:
                st_llm_model_name = llm_form_panel.text_input(
                    tr("Model Name"),
                    value=llm_model_name,
                    key=f"{llm_provider}_model_name_input",
                )
            # A caixa de entrada exibe o valor padrão do Registro, mas a configuração salva apenas o valor real de substituição do usuário.
            # Dessa forma, após a atualização do modelo padrão e da URL base, usuários não customizados poderão segui-los automaticamente.
            config.app[llm_provider_spec.config_key("api_key")] = st_llm_api_key
            config.app[llm_provider_spec.config_key("base_url")] = (
                normalize_provider_override(
                    st_llm_base_url,
                    llm_default_base_url,
                )
            )
            config.app[llm_provider_spec.config_key("model_name")] = (
                normalize_provider_override(
                    st_llm_model_name,
                    llm_provider_spec.default_model,
                )
            )

            # Os campos específicos do provedor também são declarados pelo Registro. Por exemplo, Cloudflare AI Gateway
            # O ID da conta é obrigatório; não há necessidade de adicionar julgamento em Main.py ao adicionar campos semelhantes no futuro.
            for field in llm_provider_spec.extra_fields:
                field_config_key = llm_provider_spec.config_key(field.config_suffix)
                field_value = llm_form_panel.text_input(
                    tr(field.label_key),
                    value=(config.app.get(field_config_key, "") or field.default_value),
                    type="password" if field.secret else "default",
                    key=f"{llm_provider}_{field.config_suffix}_input",
                )
                config.app[field_config_key] = normalize_provider_override(
                    field_value,
                    field.default_value,
                )

            if llm_form_panel.button(
                tr("Test LLM Connection"),
                key="test_llm_connection_button",
                use_container_width=True,
                type="secondary",
                icon=":material/network_check:",
            ):
                with llm_form_panel.spinner(tr("Testing LLM Connection")):
                    with config.runtime_config_lock():
                        connection_ok, connection_error, connection_elapsed = (
                            llm.test_connection()
                        )

                if connection_ok:
                    llm_form_panel.success(
                        tr("LLM Connection Test Succeeded").format(
                            provider=llm_provider_labels[llm_provider],
                            model=st_llm_model_name or "-",
                            elapsed=f"{connection_elapsed:.2f}",
                        )
                    )
                else:
                    llm_form_panel.error(
                        tr("LLM Connection Test Failed").format(error=connection_error)
                    )

        # Painel direito - configurações de chave API
        with right_config_panel:
            pexels_api_key = _get_material_api_keys("pexels_api_keys")
            pexels_api_key = st.text_input(
                tr("Pexels API Key"),
                value=pexels_api_key,
                type="password",
                key="pexels_api_keys_input",
            )
            _save_material_api_keys("pexels_api_keys", pexels_api_key)

            pixabay_api_key = _get_material_api_keys("pixabay_api_keys")
            pixabay_api_key = st.text_input(
                tr("Pixabay API Key"),
                value=pixabay_api_key,
                type="password",
                key="pixabay_api_keys_input",
            )
            _save_material_api_keys("pixabay_api_keys", pixabay_api_key)

            coverr_api_key = _get_material_api_keys("coverr_api_keys")
            coverr_api_key = st.text_input(
                tr("Coverr API Key"),
                value=coverr_api_key,
                type="password",
                key="coverr_api_keys_input",
            )
            _save_material_api_keys("coverr_api_keys", coverr_api_key)

    config.save_config()


# -----------------------------------------------------------------------------
# Forma principal de geração: painéis de copywriting, vídeo, áudio e legendas
# -----------------------------------------------------------------------------


def _render_script_settings(panel, params):
    """Renderize as configurações de cópia e atualize os parâmetros de geração."""
    with panel:
        with st.container(border=True):
            st.write(tr("Video Script Settings"))
            params.video_subject = st.text_input(
                tr("Video Subject"),
                placeholder=tr("Video Subject Placeholder"),
                key="video_subject",
            ).strip()

            video_languages = [
                (tr("Auto Detect"), ""),
            ]
            for code in support_locales:
                video_languages.append((code, code))

            selected_language_code = stable_selectbox(
                tr("Script Language"),
                options=[value for _, value in video_languages],
                default_value="",
                key="script_language_select",
                format_func=lambda value: dict(
                    (v, label) for label, v in video_languages
                )[value],
            )
            params.video_language = selected_language_code

            # Use o contêiner local com chave para limitar o estilo de entrada dobrável e manter a interação nativa do expansor.
            # Ao mesmo tempo, evite que os estilos danifiquem acidentalmente outras áreas de dobra, como "Configurações básicas" na parte superior da página.
            with st.container(key="advanced_settings_script"):
                with st.expander(tr("Advanced Script Settings"), expanded=False):
                    st.session_state.setdefault("paragraph_number_input", 1)
                    params.paragraph_number = st.slider(
                        tr("Script Paragraph Number"),
                        min_value=llm.MIN_SCRIPT_PARAGRAPH_NUMBER,
                        max_value=llm.MAX_SCRIPT_PARAGRAPH_NUMBER,
                        key="paragraph_number_input",
                    )
                    params.video_script_prompt = st.text_area(
                        tr("Custom Script Requirements"),
                        height=100,
                        max_chars=llm.MAX_SCRIPT_PROMPT_LENGTH,
                        placeholder=tr("Custom Script Requirements Placeholder"),
                        key="video_script_prompt",
                    ).strip()

                    system_prompt = st.text_area(
                        tr("Custom System Prompt"),
                        height=240,
                        max_chars=llm.MAX_SCRIPT_SYSTEM_PROMPT_LENGTH,
                        key="custom_system_prompt",
                    ).strip()
                    # O conteúdo padrão é mantido uniformemente pela camada de serviço. Embora a interface exiba diretamente as palavras de prompt padrão, ela apenas
                    # Somente as modificações reais feitas pelo usuário são transferidas com a tarefa para evitar que a versão antiga das regras padrão seja solidificada em tarefas históricas.
                    params.custom_system_prompt = (
                        ""
                        if system_prompt == llm.DEFAULT_SCRIPT_SYSTEM_PROMPT.strip()
                        else system_prompt
                    )

                    restore_prompt_col, preview_prompt_col = st.columns(2)
                    if restore_prompt_col.button(
                        tr("Restore Default System Prompt"),
                        key="restore_default_system_prompt",
                        icon=":material/restart_alt:",
                        on_click=reset_script_system_prompt,
                        use_container_width=True,
                    ):
                        st.toast(tr("Default System Prompt Restored"))
                    if preview_prompt_col.button(
                        tr("Preview Final Prompt"),
                        key="preview_final_script_prompt",
                        icon=":material/preview:",
                        use_container_width=True,
                    ):
                        render_script_prompt_preview(
                            llm.build_script_prompt(
                                video_subject=params.video_subject,
                                language=params.video_language,
                                paragraph_number=params.paragraph_number,
                                video_script_prompt=params.video_script_prompt,
                                custom_system_prompt=params.custom_system_prompt,
                            )
                        )

            if st.button(
                tr("Generate Video Script and Keywords"),
                key="auto_generate_script",
                use_container_width=True,
                type="secondary",
                icon=":material/auto_awesome:",
            ):
                if not params.video_subject:
                    # Os tópicos de vídeo são entradas necessárias para a geração de scripts, e a interceptação antecipada pode evitar chamadas de modelo sem sentido.
                    st.toast(tr("Please Enter the Video Subject First"))
                    st.warning(tr("Please Enter the Video Subject First"))
                else:
                    with st.spinner(tr("Generating Video Script and Keywords")):
                        with config.runtime_config_lock():
                            script = llm.generate_script(
                                video_subject=params.video_subject,
                                language=params.video_language,
                                paragraph_number=params.paragraph_number,
                                video_script_prompt=params.video_script_prompt,
                                custom_system_prompt=params.custom_system_prompt,
                            )
                            terms = llm.generate_terms(
                                params.video_subject,
                                script,
                                amount=8 if params.match_materials_to_script else 5,
                                match_script_order=params.match_materials_to_script,
                            )
                        if "Error: " in script:
                            st.error(tr(script))
                        elif "Error: " in terms:
                            st.error(tr(terms))
                        else:
                            st.session_state["video_script"] = script
                            st.session_state["video_terms"] = ", ".join(terms)
            params.video_script = st.text_area(
                tr("Video Script"),
                help=tr("Video Script Help"),
                height=180,
                key="video_script",
            )
            if st.button(
                tr("Generate Video Keywords"),
                key="auto_generate_terms",
                use_container_width=True,
                type="secondary",
                icon=":material/auto_awesome:",
            ):
                if not params.video_script:
                    # Palavras-chave de vídeo precisam ser extraídas com base na cópia. Se a cópia estiver vazia, você será avisado antecipadamente e a chamada do modelo será ignorada.
                    st.toast(tr("Please Enter the Video Subject"))
                    st.warning(tr("Please Enter the Video Subject"))
                else:
                    with st.spinner(tr("Generating Video Keywords")):
                        with config.runtime_config_lock():
                            terms = llm.generate_terms(
                                params.video_subject,
                                params.video_script,
                                amount=8 if params.match_materials_to_script else 5,
                                match_script_order=params.match_materials_to_script,
                            )
                        if "Error: " in terms:
                            st.error(tr(terms))
                        else:
                            st.session_state["video_terms"] = ", ".join(terms)

            params.video_terms = st.text_area(
                tr("Video Keywords"),
                help=tr("Video Keywords Help"),
                key="video_terms",
            )


def _render_video_settings(panel, params):
    """Renderize as configurações de vídeo e retorne o material local selecionado desta vez."""
    uploaded_files = []
    with panel:
        with st.container(border=True):
            st.write(tr("Video Settings"))
            video_concat_modes = [
                (tr("Sequential"), "sequential"),
                (tr("Random"), "random"),
            ]
            video_sources = [
                (tr("Pexels"), "pexels"),
                (tr("Pixabay"), "pixabay"),
                (tr("Coverr"), "coverr"),
                (tr("Local file"), "local"),
            ]

            saved_video_source_name = config.app.get("video_source", "pexels")

            params.video_source = stable_selectbox(
                tr("Video Source"),
                options=[value for _, value in video_sources],
                default_value=saved_video_source_name,
                key="video_source_select",
                format_func=lambda value: dict(
                    (v, label) for label, v in video_sources
                )[value],
            )
            config.app["video_source"] = params.video_source

            if params.video_source == "local":
                # A verificação do tipo de arquivo do Streamlit é sensível à caixa da extensão, e ambas as formas maiúsculas e minúsculas são permitidas aqui.
                local_file_types = sorted(
                    extension.removeprefix(".")
                    for extension in LOCAL_MATERIAL_EXTENSIONS
                )
                uploaded_files = st.file_uploader(
                    tr("Upload Local Files"),
                    type=local_file_types
                    + [file_type.upper() for file_type in local_file_types],
                    accept_multiple_files=True,
                    key="local_video_materials_uploader",
                )

            # A correspondência da sequência de cópias manterá a ordem narrativa desde a geração da palavra-chave até a síntese final, portanto, quando estiver ativada
            # A emenda sequencial é a única opção que se adapta à lógica de execução real. A sincronização dos valores de controle evita que a interface ainda seja exibida
            # "Emenda aleatória", mantendo a seleção original do usuário e restaurando automaticamente após o fechamento.
            sync_script_order_concat_mode()
            selected_concat_mode = stable_selectbox(
                tr("Video Concat Mode"),
                options=[value for _, value in video_concat_modes],
                default_value=VideoConcatMode.random.value,
                key="video_concat_mode_select",
                format_func=lambda value: dict(
                    (v, label) for label, v in video_concat_modes
                )[value],
                disabled=bool(st.session_state.get("match_materials_to_script", False)),
            )
            params.video_concat_mode = VideoConcatMode(selected_concat_mode)

            params.match_materials_to_script = st.checkbox(
                tr("Match Materials to Script Order"),
                help=tr("Match Materials to Script Order Help"),
                key="match_materials_to_script",
                on_change=sync_script_order_concat_mode,
            )
            config.app["match_materials_to_script"] = params.match_materials_to_script

            # Modo de transição de vídeo
            video_transition_modes = [
                (tr("None"), VideoTransitionMode.none.value),
                (tr("Shuffle"), VideoTransitionMode.shuffle.value),
                (tr("FadeIn"), VideoTransitionMode.fade_in.value),
                (tr("FadeOut"), VideoTransitionMode.fade_out.value),
                (tr("SlideIn"), VideoTransitionMode.slide_in.value),
                (tr("SlideOut"), VideoTransitionMode.slide_out.value),
                (tr("ZoomIn"), VideoTransitionMode.zoom_in.value),
                (tr("ZoomOut"), VideoTransitionMode.zoom_out.value),
            ]
            selected_transition_mode = stable_selectbox(
                tr("Video Transition Mode"),
                options=[value for _, value in video_transition_modes],
                default_value=VideoTransitionMode.none.value,
                key="video_transition_mode_select",
                format_func=lambda value: dict(
                    (v, label) for label, v in video_transition_modes
                )[value],
            )
            params.video_transition_mode = VideoTransitionMode(selected_transition_mode)

            video_aspect_ratios = [
                (tr("Portrait"), VideoAspect.portrait.value),
                (tr("Landscape"), VideoAspect.landscape.value),
            ]
            # 99% da biblioteca Coverr tem tela horizontal 16:9. A tela vertical padrão fará com que a tela seja cercada por muitas bordas pretas.
            # Use uma chave de widget específica da fonte para que cada fonte se lembre de sua seleção de aspecto:
            #   - Mudar para coverr pela primeira vez → paisagem padrão (índice = 1)
            #   - Outras fontes seguem Portrait(index=0)
            #   - Se o usuário alterar manualmente o aspecto sob uma determinada fonte, o session_state será lembrado.
            #     A escolha do usuário será respeitada na próxima vez que ele retornar à mesma fonte e não será sobrescrita novamente à força.
            default_aspect_index = 1 if params.video_source == "coverr" else 0
            selected_aspect_ratio = stable_selectbox(
                tr("Video Ratio"),
                options=[value for _, value in video_aspect_ratios],
                default_value=video_aspect_ratios[default_aspect_index][1],
                key=f"video_aspect_for_{params.video_source}",
                format_func=lambda value: dict(
                    (v, label) for label, v in video_aspect_ratios
                )[value],
            )
            params.video_aspect = VideoAspect(selected_aspect_ratio)

            params.video_clip_duration = stable_selectbox(
                tr("Clip Duration"),
                options=[2, 3, 4, 5, 6, 7, 8, 9, 10],
                default_value=3,
                key="video_clip_duration_select",
                help=tr("Clip Duration Help"),
            )
            clip_speed_key = localized_widget_key("video_clip_speed_slider")
            # session_state pode vir de uma tarefa herdada, parâmetro de API ou estado de página herdado. Antes do controle ser criado
            # A normalização unificada não apenas mantém as escolhas legais, mas também garante que o controle deslizante sempre receba 0,5 ~ 2,0
            # Um número finito de ponto flutuante dentro do intervalo.
            st.session_state[clip_speed_key] = utils.normalize_clip_speed(
                st.session_state.get(clip_speed_key, 1.0)
            )
            params.video_clip_speed = st.slider(
                tr("Clip Speed"),
                min_value=0.5,
                max_value=2.0,
                step=0.05,
                format="%.2fx",
                key=clip_speed_key,
                help=tr("Clip Speed Help"),
            )
            params.video_count = stable_selectbox(
                tr("Number of Videos Generated Simultaneously"),
                options=[1, 2, 3, 4, 5],
                default_value=1,
                key="video_count_select",
            )

            video_codec_options = [
                (tr("Default Video Encoder"), DEFAULT_VIDEO_CODEC_OPTION),
                ("libx264 (CPU)", "libx264"),
                ("NVIDIA NVENC (h264_nvenc)", "h264_nvenc"),
                ("AMD AMF (h264_amf)", "h264_amf"),
                ("Intel QSV (h264_qsv)", "h264_qsv"),
                ("Windows MediaFoundation (h264_mf)", "h264_mf"),
                ("macOS VideoToolbox (h264_videotoolbox)", "h264_videotoolbox"),
            ]
            saved_video_codec = config.app.get(
                "video_codec", DEFAULT_VIDEO_CODEC_OPTION
            )
            saved_video_codec_values = [item[1] for item in video_codec_options]
            if saved_video_codec not in saved_video_codec_values:
                # Versões mais antigas ou configuração manual podem deixar valores inválidos. UI retorna ao "padrão" em vez de substituir o usuário
                # Corrigido um determinado codificador e o backend ainda resolverá para libx264 de acordo com a política estável.
                saved_video_codec = DEFAULT_VIDEO_CODEC_OPTION
            selected_video_codec = stable_selectbox(
                tr("Video Encoder"),
                options=saved_video_codec_values,
                default_value=saved_video_codec,
                key="video_encoder_select",
                format_func=lambda value: dict(
                    (v, label) for label, v in video_codec_options
                )[value],
                help=tr("Video Encoder Help"),
            )
            if selected_video_codec == DEFAULT_VIDEO_CODEC_OPTION:
                # O modo padrão não persiste codificadores específicos, deixando a configuração expressar “seguir os padrões do projeto”.
                config.app.pop("video_codec", None)
            else:
                config.app["video_codec"] = selected_video_codec
    return uploaded_files


def _estimate_voiceover_duration_range(
    text: str, voice_rate: float
) -> tuple[float, float] | None:
    """
    Estima localmente a duração completa da dublagem, retornando limites superiores e inferiores conservadores em segundos.

    Esta estimativa é usada apenas para ajudar os usuários a avaliar a magnitude do copywriting antes de ligar para o TTS pago e não participa da execução da tarefa.
    Chinês, japonês e coreano são estimados com base na velocidade dos caracteres, e outros idiomas que usam segmentação de palavras espaciais são estimados com base na velocidade das palavras.
    Pausas de pontuação comuns também estão incluídas. Diferentes provedores, timbres e tons causarão desvios reais, portanto a interface
    Um intervalo deve ser apresentado em vez de um resultado único pseudoexato.
    """
    normalized_text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized_text:
        return None

    script_chars = re.findall(
        r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]",
        normalized_text,
    )
    remaining_text = re.sub(
        r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]",
        " ",
        normalized_text,
    )
    words = re.findall(r"\b[\w]+(?:[-'’][\w]+)*\b", remaining_text, re.UNICODE)
    punctuation_count = len(re.findall(r"[,，.。!?！？;；:：]", normalized_text))

    # 4.2 Palavras/segundo e 2,6 palavras/segundo estão próximos da velocidade dos comentários diários; pontuação toque em 0,12 segundos para adicionar uma pequena pausa.
    # voice_rate Apenas como uma correção de estimativa. O TTS parcialmente gerado não impõe estritamente a ampliação, portanto, no final
    # permanecer ±15% intervalo para evitar que os usuários pensem erroneamente que o valor é equivalente ao resultado real no lado do servidor.
    base_seconds = len(script_chars) / 4.2 + len(words) / 2.6 + punctuation_count * 0.12
    if base_seconds <= 0:
        return None

    normalized_rate = max(float(voice_rate or 1.0), 0.1)
    estimated_seconds = base_seconds / normalized_rate
    return (
        round(max(estimated_seconds * 0.85, 1.0), 1),
        round(max(estimated_seconds * 1.15, 1.0), 1),
    )


def _get_voice_preview_sample(voice_name: str) -> str:
    """Retorna uma pequena cópia de audição adequada ao timbre atual, sem usar a cópia completa do vídeo do usuário."""
    # Os sons do ElevenLabs são selecionados com base nos caracteres vietnamitas no nome de exibição quando não possuem um campo de idioma explícito
    # Ouça a cópia e evite usar linguagem que claramente não corresponda para julgar o efeito do timbre.
    if voice.is_elevenlabs_voice(voice_name):
        parts = voice_name.split(":", 2)
        display = parts[2] if len(parts) >= 3 else ""
        vietnamese_chars = set("àáâãèéêìíòóôõùúýăđơưÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝĂĐƠƯ")
        if any(char in vietnamese_chars for char in display):
            return "Xin chào, đây là đoạn âm thanh thử nghiệm giọng nói."
    return tr("Voice Example")


def _voice_preview_fingerprint(
    *,
    preview_type: str,
    content: str,
    tts_server: str,
    voice_name: str,
    voice_rate: float,
    voice_volume: float,
    provider_signature: dict,
) -> str:
    """Gere impressões digitais do cache de audição e invalide automaticamente resultados de audição antigos após qualquer alteração nos parâmetros de dublagem."""
    payload = {
        "preview_type": preview_type,
        "content": content,
        "tts_server": tts_server,
        "voice_name": voice_name,
        "voice_rate": voice_rate,
        "voice_volume": voice_volume,
        "provider_signature": provider_signature,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _credential_signature(value: str) -> str:
    """
    Gere um resumo de credenciais que seja usado apenas para determinação de invalidação de cache.

    O resumo não é gravado nos arquivos de configuração, log ou tarefa. Após o usuário modificar a chave API, o resumo será alterado, assim
    Força um recall do serviço de dublagem atual para evitar que caches de audição antigos disponibilizem novas credenciais inválidas.
    """
    normalized_value = str(value or "")
    if not normalized_value:
        return ""
    return hashlib.sha256(normalized_value.encode("utf-8")).hexdigest()


def _get_voice_preview_provider_signature(tts_server: str) -> dict:
    """
    Retorna a configuração não confidencial do provedor que afeta os resultados de escuta.

    A chave de API participa apenas da impressão digital do cache como um resumo unidirecional e as credenciais originais não entram no cache ou nos logs. Modelo,
    Sempre que o endereço, região ou credenciais do serviço mudarem, a audição deverá ser regenerada, caso contrário a interface poderá continuar a jogar.
    O áudio na configuração antiga do provedor faz com que os usuários acreditem erroneamente que as configurações atuais entraram em vigor.
    """
    if tts_server == "azure-tts-v2":
        return {
            "speech_region": config.azure.get("speech_region", ""),
            "credential": _credential_signature(config.azure.get("speech_key", "")),
        }
    if tts_server == "siliconflow":
        return {
            "credential": _credential_signature(config.siliconflow.get("api_key", ""))
        }
    if tts_server == "gemini-tts":
        return {
            "credential": _credential_signature(config.app.get("gemini_api_key", ""))
        }
    if tts_server == "mimo-tts":
        return {"credential": _credential_signature(config.app.get("mimo_api_key", ""))}
    if tts_server == "elevenlabs":
        return {
            "model_id": config.elevenlabs.get("model_id", ""),
            "credential": _credential_signature(config.elevenlabs.get("api_key", "")),
        }
    if tts_server == "chatterbox":
        return {
            "base_url": config.chatterbox.get("base_url", ""),
            "model_id": config.chatterbox.get("model_id", ""),
            "credential": _credential_signature(config.chatterbox.get("api_key", "")),
        }
    return {}


def _synthesize_voice_preview(
    *,
    content: str,
    preview_type: str,
    selected_tts_server: str,
    voice_name: str,
    voice_rate: float,
    voice_volume: float,
) -> dict | None:
    """As audições são geradas uma vez e movidas para o cache de memória; os arquivos temporários não são persistidos entre as sessões."""
    if selected_tts_server == "chatterbox":
        _sync_chatterbox_config_from_session_state()

    temp_dir = utils.storage_dir("temp", create=True)
    audio_file = os.path.join(temp_dir, f"tmp-voice-{str(uuid4())}.mp3")
    logger.info(
        f"generating {preview_type} voice preview: "
        f"voice={voice_name}, rate={voice_rate}, volume={voice_volume}, "
        f"text_length={len(content)}"
    )
    try:
        with config.try_runtime_config_lock() as lock_acquired:
            if not lock_acquired:
                return {"busy": True}
            sub_maker = voice.tts(
                text=content,
                voice_name=voice_name,
                voice_rate=voice_rate,
                voice_file=audio_file,
                voice_volume=voice_volume,
            )
        if not sub_maker or not os.path.exists(audio_file):
            logger.error(f"{preview_type} voice preview did not produce an audio file")
            return None

        with open(audio_file, "rb") as file:
            audio_bytes = file.read()
        if not audio_bytes:
            logger.error(f"voice preview audio file is empty: {audio_file}")
            return None

        duration = voice.get_audio_duration(audio_file)
        if (
            not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration <= 0
        ):
            logger.warning(
                f"voice preview duration is unavailable: "
                f"preview_type={preview_type}, voice={voice_name}"
            )
            duration = None

        return {
            "audio_bytes": audio_bytes,
            "mime_type": _detect_audio_mime(audio_file, audio_bytes),
            "duration": duration,
            "preview_type": preview_type,
            "sub_maker": sub_maker,
        }
    finally:
        # O player do navegador usa bytes de memória e os arquivos podem ser limpos após a leitura para evitar o acúmulo de arquivos temporários durante a audição frequente.
        try:
            os.remove(audio_file)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # As falhas de limpeza não devem substituir respostas ou exceções reais do TTS, mas os caminhos e erros do sistema precisam ser preservados,
            # É conveniente solucionar problemas ambientais, como permissões e sistemas de arquivos somente leitura.
            logger.warning(
                f"failed to delete voice preview file {audio_file}: {str(exc)}"
            )


def _render_voice_preview(params, friendly_names, selected_tts_server, voice_name):
    """Renderize audições curtas de baixo custo, estimativas completas de duração de redação e pré-visualizações completas de narração sob demanda."""
    if not friendly_names:
        return

    script_content = str(params.video_script or "").strip()
    estimated_range = _estimate_voiceover_duration_range(
        script_content,
        params.voice_rate,
    )
    if estimated_range:
        st.caption(
            tr("Estimated Voiceover Duration").format(
                min=estimated_range[0],
                max=estimated_range[1],
            )
        )
    else:
        st.caption(tr("Voiceover Script Required"))

    sample_content = _get_voice_preview_sample(voice_name)
    provider_signature = _get_voice_preview_provider_signature(selected_tts_server)
    preview_columns = st.columns(2)
    short_preview_requested = preview_columns[0].button(
        tr("Play Voice"),
        key="play_voice_button",
        icon=":material/graphic_eq:",
        use_container_width=True,
    )
    full_preview_requested = preview_columns[1].button(
        tr("Generate Full Voiceover Preview"),
        key="generate_full_voiceover_preview_button",
        icon=":material/article:",
        help=tr("Full Voiceover Preview Cost Hint"),
        use_container_width=True,
        disabled=not bool(script_content),
    )

    preview_type = ""
    preview_content = ""
    if short_preview_requested:
        preview_type = "sample"
        preview_content = sample_content
    elif full_preview_requested:
        preview_type = "full"
        preview_content = script_content

    sample_fingerprint = _voice_preview_fingerprint(
        preview_type="sample",
        content=sample_content,
        tts_server=selected_tts_server,
        voice_name=voice_name,
        voice_rate=params.voice_rate,
        voice_volume=params.voice_volume,
        provider_signature=provider_signature,
    )
    full_fingerprint = (
        _voice_preview_fingerprint(
            preview_type="full",
            content=script_content,
            tts_server=selected_tts_server,
            voice_name=voice_name,
            voice_rate=params.voice_rate,
            voice_volume=params.voice_volume,
            provider_signature=provider_signature,
        )
        if script_content
        else ""
    )

    if preview_type:
        requested_fingerprint = (
            sample_fingerprint if preview_type == "sample" else full_fingerprint
        )
        cached_preview = st.session_state.get("voice_preview_audio")
        if (
            not cached_preview
            or cached_preview.get("fingerprint") != requested_fingerprint
        ):
            try:
                with st.spinner(tr("Synthesizing Voice")):
                    preview_result = _synthesize_voice_preview(
                        content=preview_content,
                        preview_type=preview_type,
                        selected_tts_server=selected_tts_server,
                        voice_name=voice_name,
                        voice_rate=params.voice_rate,
                        voice_volume=params.voice_volume,
                    )
            except Exception as exc:
                logger.exception(f"failed to generate {preview_type} voice preview")
                st.error(tr("Voice Preview Failed").format(error=str(exc)))
            else:
                if preview_result and preview_result.get("busy"):
                    st.warning(tr("Voice Preview Busy"))
                elif preview_result:
                    preview_result["fingerprint"] = requested_fingerprint
                    st.session_state["voice_preview_audio"] = preview_result
                else:
                    st.error(tr("Voice Preview No Audio"))

    cached_preview = st.session_state.get("voice_preview_audio")
    valid_fingerprints = {sample_fingerprint, full_fingerprint}
    if (
        cached_preview
        and cached_preview.get("fingerprint") in valid_fingerprints
        and cached_preview.get("audio_bytes")
    ):
        st.audio(
            cached_preview["audio_bytes"],
            format=cached_preview.get("mime_type", "audio/mp3"),
        )
        if cached_preview.get("preview_type") == "full":
            duration = cached_preview.get("duration")
            if isinstance(duration, (int, float)) and duration > 0:
                st.caption(
                    tr("Actual Voiceover Duration").format(duration=f"{duration:.1f}")
                )
            else:
                st.warning(tr("Voice Preview Duration Unavailable"))


def _get_reusable_full_voice_preview(params, voice_mode: str) -> dict | None:
    """
    Retorna o cache de audição completo que corresponde exatamente aos parâmetros de construção atuais.

    Apenas a redação completa é reutilizada para audição, e amostras de tons curtos nunca podem entrar na tarefa oficial. As impressões digitais cobrem uniformemente os direitos autorais,
    Provedor, timbre, velocidade de fala, volume e resumo de configuração não sensível; quaisquer alterações de parâmetro retornarão naturalmente para
    Processo TTS normal. A linha do tempo da legenda e a duração efetiva também são condições necessárias para evitar apenas reutilizar o áudio e depois
    O link da legenda do Edge perde o SubMaker.
    """
    if voice_mode != VOICE_MODE_TTS:
        return None

    script_content = str(params.video_script or "").strip()
    selected_tts_server = config.ui.get("tts_server", "azure-tts-v1")
    if (
        not script_content
        or not params.voice_name
        # Os vídeos formais aplicarão uniformemente o volume de dublagem durante o estágio de síntese do MoviePy; alguns provedores irão
        # O ganho de volume é gravado diretamente no estágio TTS. A escuta multiplex em volumes não padrão pode causar ganho secundário.
        # Portanto, primeiro revertemos de forma conservadora ao processo original para evitar a introdução de julgamentos especiais do Provedor para um pequeno número de cenários.
        or not math.isclose(float(params.voice_volume), 1.0)
    ):
        return None

    expected_fingerprint = _voice_preview_fingerprint(
        preview_type="full",
        content=script_content,
        tts_server=selected_tts_server,
        voice_name=params.voice_name,
        voice_rate=params.voice_rate,
        voice_volume=params.voice_volume,
        provider_signature=_get_voice_preview_provider_signature(selected_tts_server),
    )
    cached_preview = st.session_state.get("voice_preview_audio")
    if (
        not cached_preview
        or cached_preview.get("fingerprint") != expected_fingerprint
        or cached_preview.get("preview_type") != "full"
        or not cached_preview.get("audio_bytes")
        or cached_preview.get("sub_maker") is None
    ):
        return None

    duration = cached_preview.get("duration")
    if (
        not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        return None

    return {
        "audio_bytes": bytes(cached_preview["audio_bytes"]),
        "duration": float(duration),
        "sub_maker": cached_preview["sub_maker"],
        "script": script_content,
        "voice_name": params.voice_name,
        "voice_rate": float(params.voice_rate),
        "voice_volume": float(params.voice_volume),
    }


def _render_elevenlabs_api_key_input(label_key):
    """
    Renderizando o estado de entrada exclusivo da chave de API que o ElevenLabs TTS compartilha com a trilha sonora.

    Se duas teclas de widget forem usadas para TTS e trilha sonora na mesma página, o Streamlit manterá os valores antigos, respectivamente.
    As caixas de entrada pós-renderizadas também substituem a configuração compartilhada. Uma chave é usada aqui e as variáveis ​​de ambiente são processadas centralmente.
    O preenchimento, as atualizações de configuração e a invalidação do cache de som garantem que a exibição da interface e as tarefas em segundo plano sempre leiam o mesmo valor.
    """
    configured_key = str(config.elevenlabs.get("api_key", "") or "").strip()
    effective_key = configured_key or os.getenv("ELEVENLABS_API_KEY", "").strip()
    entered_key = st.text_input(
        tr(label_key),
        value=effective_key,
        type="password",
        key="elevenlabs_api_key_input",
    ).strip()

    if entered_key != effective_key:
        for cache_key in list(st.session_state.keys()):
            if str(cache_key).startswith("elevenlabs_voices_"):
                del st.session_state[cache_key]

    # Variáveis ​​de ambiente são usadas apenas para o processo atual e não são copiadas automaticamente para config.toml se não forem modificadas pelo usuário.
    # A configuração local é atualizada somente quando existe uma configuração existente ou o usuário modifica ativamente a entrada, consistente com o comportamento do Sonilo.
    if configured_key or entered_key != effective_key:
        config.elevenlabs["api_key"] = entered_key
    return entered_key


def _render_background_music_settings(params, elevenlabs_api_key_rendered=False):
    """Renderize a fonte da música de fundo e as configurações de volume e retorne o arquivo carregado para ser salvo desta vez."""
    uploaded_bgm_file = None
    st.divider()
    bgm_options = [
        (tr("No Background Music"), ""),
        (tr("Random Background Music"), "random"),
        (tr("Custom Background Music"), "custom"),
        (tr("Sonilo Background Music"), "sonilo"),
        (tr("ElevenLabs Background Music"), "elevenlabs"),
    ]
    selected_bgm_type = stable_selectbox(
        tr("Background Music Source"),
        options=[value for _, value in bgm_options],
        default_value="random",
        key="bgm_type_select",
        format_func=lambda value: dict((v, label) for label, v in bgm_options)[value],
    )
    params.bgm_type = selected_bgm_type
    if params.bgm_type == "sonilo":
        configured_key = str(config.app.get("sonilo_api_key", "") or "").strip()
        effective_key = configured_key or os.getenv("SONILO_API_KEY", "").strip()
        entered_key = st.text_input(
            tr("Sonilo API Key"),
            value=effective_key,
            type="password",
            key="sonilo_api_key_input",
        ).strip()
        # O usuário exige que a chave configurada seja preenchida diretamente na caixa de entrada de senha. Os valores de configuração têm precedência sobre as variáveis ​​de ambiente;
        # Somente escreva de volta quando o usuário realmente alterar a entrada ou usar a configuração para evitar alterar a chave na variável de ambiente.
        # Copie para config.toml sem qualquer operação.
        if configured_key or entered_key != effective_key:
            config.app["sonilo_api_key"] = entered_key
    elif params.bgm_type == "elevenlabs":
        if elevenlabs_api_key_rendered:
            # Quando a caixa de entrada compartilhada for renderizada na área TTS, um segundo widget não será mais criado para evitar dois widgets independentes.
            # Os valores session_state se sobrescrevem. O texto descritivo ajuda os usuários a localizar a configuração compartilhada acima.
            st.caption(tr("ElevenLabs API Key Help"))
        else:
            _render_elevenlabs_api_key_input("ElevenLabs Music API Key")

    params.bgm_volume = stable_selectbox(
        tr("Background Music Volume"),
        options=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        default_value=0.2,
        key="bgm_volume_select",
        format_func=lambda value: f"{int(value * 100)}%",
        disabled=not params.bgm_type,
    )
    bgm_enabled = bgm_service.should_use_bgm(
        params.bgm_type, params.bgm_volume
    )

    if params.bgm_type == "custom":
        uploaded_bgm_file = st.file_uploader(
            tr("Upload Background Music"),
            type=[
                extension.removeprefix(".")
                for extension in bgm_service.SUPPORTED_BGM_EXTENSIONS
            ],
            accept_multiple_files=False,
            key="custom_bgm_uploader",
            help=tr("Upload Background Music Help"),
            # Streamlit exibe um limite global de 200 MB no controle por padrão. Isso deve estar relacionado à camada de serviço
            # O limite rígido de 30 MB permanece consistente para evitar ser rejeitado pelo servidor somente quando a interface permitir seleção e envio.
            max_upload_size=bgm_service.MAX_BGM_UPLOAD_BYTES // (1024 * 1024),
        )
        if uploaded_bgm_file is not None and bgm_enabled:
            try:
                safe_name = bgm_service.sanitize_upload_filename(
                    uploaded_bgm_file.name
                )
                # Streamlit executará novamente a página após ajustar quaisquer controles, como volume. Use hash de conteúdo
                # Diferencie os arquivos carregados e armazene em cache os resultados completos da decodificação na sessão atual. Você não pode confiar apenas no mesmo nome,
                # O uso indevido de resultados antigos para arquivos do mesmo tamanho também evita chamar o FFmpeg repetidamente para cada nova execução.
                validation_key = (
                    safe_name,
                    uploaded_bgm_file.size,
                    hashlib.sha256(uploaded_bgm_file.getbuffer()).hexdigest(),
                )
                cached_validation = st.session_state.get("custom_bgm_validation")
                if (
                    not cached_validation
                    or cached_validation.get("key") != validation_key
                ):
                    try:
                        bgm_service.validate_bgm_upload(
                            uploaded_bgm_file.name, uploaded_bgm_file
                        )
                    except bgm_service.BgmUploadError as exc:
                        cached_validation = {
                            "key": validation_key,
                            "error": str(exc),
                            "error_type": "upload",
                        }
                        # Os resultados com falha da mesma impressão digital do arquivo serão inseridos no cache da sessão, portanto, apenas aqui
                        # Grave-o uma vez quando a verificação for realmente executada pela primeira vez para evitar a repetição de controles comuns e atualizar a tela.
                        logger.warning(
                            "WebUI background music validation rejected: "
                            f"name={safe_name}, error={str(exc)}"
                        )
                    except bgm_service.BgmServiceError as exc:
                        cached_validation = {
                            "key": validation_key,
                            "error": str(exc),
                            "error_type": "service",
                        }
                        logger.error(
                            "WebUI background music validation failed: "
                            f"name={safe_name}, error={str(exc)}"
                        )
                    else:
                        cached_validation = {
                            "key": validation_key,
                            "error": "",
                            "error_type": "",
                        }
                    st.session_state["custom_bgm_validation"] = cached_validation

                if cached_validation.get("error"):
                    if cached_validation.get("error_type") == "service":
                        raise bgm_service.BgmServiceError(
                            cached_validation["error"]
                        )
                    raise bgm_service.BgmUploadError(cached_validation["error"])
            except bgm_service.BgmUploadError:
                # Arquivos ilegais não podem herdar o nome do último upload válido, caso contrário os parâmetros da tarefa ainda poderão apontar para
                # BGM histórico. Mantenha o valor de retorno UploadedFile para que ele ainda seja finalizado quando o usuário clicar em Gerar
                # O servidor verifica a interceptação em vez de gerar silenciosamente um vídeo sem música de fundo.
                params.bgm_file = ""
                st.error(tr("Invalid Background Music"))
            except bgm_service.BgmServiceError:
                params.bgm_file = ""
                st.error(tr("Background Music Validation Failed"))
            else:
                # O player e "Pronto" serão exibidos somente após a verificação completa da decodificação. Os arquivos ainda estão apenas clicando
                # Persistindo na construção, o usuário apenas visualiza ou remove posteriormente os arquivos não polui o armazenamento/bgm.
                uploaded_mime_type = str(
                    getattr(uploaded_bgm_file, "type", "") or ""
                )
                preview_mime_type = (
                    uploaded_mime_type
                    if uploaded_mime_type.startswith("audio/")
                    else mimetypes.guess_type(safe_name)[0] or "audio/mpeg"
                )
                st.audio(uploaded_bgm_file, format=preview_mime_type)
                st.info(f"{tr('Background Music Ready')}: {safe_name}")
                params.bgm_file = safe_name

        custom_bgm_file = st.text_input(
            tr("Custom Background Music File"),
            key="custom_bgm_file_input",
            disabled=uploaded_bgm_file is not None,
        )
        if uploaded_bgm_file is None and custom_bgm_file and bgm_enabled:
            # O nome do arquivo é mapeado para armazenamento/bgm ou recurso/músicas pela camada de serviço e depois verificado.
            # A UI não aceita nenhum caminho fora dos dois diretórios da lista de permissões.
            params.bgm_file = custom_bgm_file.strip()
        elif not bgm_enabled:
            # O controle de upload continua retendo os arquivos selecionados pelo usuário, e a próxima reexecução após aumentar o volume será automaticamente
            # Verificação completa; os parâmetros da tarefa atual devem ser limpos para evitar que a tarefa de volume 0 salve ou analise o arquivo.
            params.bgm_file = ""

    if params.bgm_type == "sonilo":
        params.video_music_prompt = st.text_input(
            tr("Sonilo Music Prompt"),
            key="sonilo_bgm_prompt_input",
            max_chars=sonilo_service.MAX_PROMPT_LENGTH,
            help=tr("Sonilo Music Prompt Help"),
        ).strip()
        if params.video_count > 1:
            st.warning(tr("Sonilo Multiple Videos Warning"))
        if st.button(
            tr("Test Sonilo Connection"),
            key="test_sonilo_connection_button",
            use_container_width=True,
        ):
            try:
                sonilo_service.test_connection()
            except sonilo_service.SoniloError as exc:
                logger.warning(f"Sonilo connection test failed: {exc}")
                st.error(tr("Sonilo Connection Test Failed").format(error=str(exc)))
            else:
                st.success(tr("Sonilo Connection Test Succeeded"))
    elif params.bgm_type == "elevenlabs":
        params.video_music_prompt = st.text_input(
            tr("ElevenLabs Music Prompt"),
            key="elevenlabs_music_prompt_input",
            max_chars=elevenlabs_music_service.MAX_PROMPT_LENGTH,
            help=tr("ElevenLabs Music Prompt Help"),
        ).strip()
        if params.video_count > 1:
            st.warning(tr("ElevenLabs Multiple Videos Warning"))
        if st.button(
            tr("Test ElevenLabs Connection"),
            key="test_elevenlabs_music_connection_button",
            use_container_width=True,
        ):
            try:
                elevenlabs_music_service.test_connection()
            except elevenlabs_music_service.ElevenLabsPaidPlanRequiredError:
                st.error(tr("ElevenLabs Paid Plan Required"))
            except elevenlabs_music_service.ElevenLabsMusicError as exc:
                logger.warning(f"ElevenLabs connection test failed: {exc}")
                st.error(tr("ElevenLabs Connection Test Failed").format(error=str(exc)))
            else:
                st.success(tr("ElevenLabs Connection Test Succeeded"))
    if params.bgm_type == "sonilo" and bgm_enabled and not sonilo_service.is_enabled():
        # A camada de tarefa não gera ou mixa a trilha sonora do Sonilo no volume 0, portanto, nenhum prompt de tecla é necessário;
        # Este julgamento compartilha regras da camada de serviço com a entrada da tarefa para evitar a bifurcação entre os prompts da interface e as condições reais de execução.
        st.warning(tr("Sonilo API Key Required"))
    elif (
        params.bgm_type == "elevenlabs"
        and bgm_enabled
        and not elevenlabs_music_service.is_enabled()
    ):
        st.warning(tr("ElevenLabs API Key Required"))
    return uploaded_bgm_file


def _render_audio_settings(panel, params):
    """Renderize as configurações de áudio e retorne o áudio carregado e o modo de dublagem atual."""
    with panel:
        with st.container(border=True):
            st.write(tr("Audio Settings"))

            # O modo de dublagem é o status de primeiro nível das configurações de áudio, responsável por distinguir claramente a dublagem automática, o upload do usuário e a não dublagem.
            # Quando a configuração antiga não possui voice_mode, o sentinela sem voz de acordo com o tts_server original permanece compatível.
            saved_tts_server = config.ui.get("tts_server", "azure-tts-v1")
            saved_voice_mode = config.ui.get("voice_mode")
            if saved_voice_mode not in {
                VOICE_MODE_TTS,
                VOICE_MODE_UPLOAD,
                VOICE_MODE_NONE,
            }:
                saved_voice_mode = (
                    VOICE_MODE_NONE
                    if saved_tts_server == voice.NO_VOICE_NAME
                    else VOICE_MODE_TTS
                )
            voice_mode_options = [VOICE_MODE_TTS, VOICE_MODE_UPLOAD, VOICE_MODE_NONE]
            voice_mode_labels = {
                VOICE_MODE_TTS: tr("Automatic Voiceover"),
                VOICE_MODE_UPLOAD: tr("Upload Voiceover"),
                VOICE_MODE_NONE: tr("No Voiceover"),
            }
            voice_mode = stable_segmented_control(
                tr("Voiceover Mode"),
                options=voice_mode_options,
                default_value=saved_voice_mode,
                key="voice_mode_control",
                format_func=lambda value: voice_mode_labels[value],
                width="stretch",
            )
            config.ui["voice_mode"] = voice_mode
            tts_mode_enabled = voice_mode == VOICE_MODE_TTS

            # O menu suspenso Provedor é responsável apenas por selecionar o serviço de dublagem automática; nenhuma dublagem já é controlada pelo modo superior.
            # Ele não está mais misturado à lista como Provedor TTS para evitar duas entradas que expressem o mesmo estado.
            tts_servers = [
                ("azure-tts-v1", "Azure TTS V1"),
                ("azure-tts-v2", "Azure TTS V2"),
                ("siliconflow", "SiliconFlow TTS"),
                ("gemini-tts", "Google Gemini TTS"),
                ("mimo-tts", "Xiaomi MiMo TTS"),
                ("elevenlabs", "ElevenLabs TTS"),
                ("chatterbox", "Chatterbox TTS"),
            ]

            tts_server_values = [server_value for server_value, _ in tts_servers]
            if saved_tts_server not in tts_server_values:
                saved_tts_server = "azure-tts-v1"

            if tts_mode_enabled:
                selected_tts_server = stable_selectbox(
                    tr("Voiceover Service"),
                    options=tts_server_values,
                    default_value=saved_tts_server,
                    key="tts_server_select",
                    format_func=lambda value: dict(
                        (v, label) for v, label in tts_servers
                    )[value],
                )
            else:
                # O modo de dublagem não automática não renderiza o controle TTS, mas mantém a última seleção e pode continuar a usá-la após voltar.
                selected_tts_server = saved_tts_server

            config.ui["tts_server"] = selected_tts_server

            # A descrição do serviço segue a seleção do Provedor, primeiro informando ao usuário o que precisa ser preparado e depois inserindo o timbre e
            # Configuração de credenciais. Provedores sem descrição não renderizam blocos de dicas vazios.
            if tts_mode_enabled:
                provider_tips = get_tts_provider_tips(selected_tts_server)
                if provider_tips:
                    st.info(provider_tips)

            # Obtenha a lista de sons com base no servidor TTS selecionado
            filtered_voices = []
            saved_voice_name = config.ui.get("voice_name", "")
            elevenlabs_api_key_rendered = False

            if not tts_mode_enabled:
                # O modo de upload de áudio e sem dublagem não carrega sons remotos, reduzindo solicitações de rede sem sentido e ruído de interface.
                filtered_voices = []
            elif selected_tts_server == "siliconflow":
                # Obtenha uma lista de sons fluidos baseados em silício
                filtered_voices = voice.get_siliconflow_voices()
            elif selected_tts_server == "gemini-tts":
                # Obtenha a lista de sons do Gemini TTS
                filtered_voices = voice.get_gemini_voices()
            elif selected_tts_server == "mimo-tts":
                # Obtenha a lista de tons predefinidos para Xiaomi MiMo TTS
                filtered_voices = voice.get_mimo_voices()
            elif selected_tts_server == "elevenlabs":
                # Read from session_state first so the API key is available before
                # the Play Voice button runs (which is earlier in the script than
                # the API key text_input widget).
                saved_elevenlabs_api_key = st.session_state.get(
                    "elevenlabs_api_key_input",
                    config.elevenlabs.get("api_key", ""),
                )
                if saved_elevenlabs_api_key:
                    config.elevenlabs["api_key"] = saved_elevenlabs_api_key
                cache_key = f"elevenlabs_voices_{saved_elevenlabs_api_key}"
                if cache_key not in st.session_state:
                    st.session_state[cache_key] = voice.get_elevenlabs_voices(
                        saved_elevenlabs_api_key
                    )
                filtered_voices = st.session_state[cache_key]
            elif selected_tts_server == "chatterbox":
                # Vozes predefinidas para serviços Chatterbox auto-hospedados (da configuração de vozes [chatterbox])
                _sync_chatterbox_config_from_session_state()
                filtered_voices = voice.get_chatterbox_voices()
            else:
                # Obtenha a lista de sons do Azure
                all_voices = voice.get_all_azure_voices(filter_locals=None)

                # Filtrar sons com base no servidor TTS selecionado
                for v in all_voices:
                    if selected_tts_server == "azure-tts-v2":
                        # As versões V2 dos sons contêm "v2" em seus nomes
                        if "V2" in v:
                            filtered_voices.append(v)
                    else:
                        # A versão V1 do som não contém "v2" em seu nome
                        if "V2" not in v:
                            filtered_voices.append(v)

            def _friendly(v):
                if voice.is_no_voice(v):
                    return tr("No Voice Selected")
                if voice.is_elevenlabs_voice(v):
                    parts = v.split(":", 2)
                    return parts[2] if len(parts) >= 3 else v
                if voice.is_chatterbox_voice(v):
                    name = v.split(":", 1)[1] if ":" in v else v
                    return name.replace("-Female", "").replace("-Male", "")
                return (
                    v.replace("Female", tr("Female"))
                    .replace("Male", tr("Male"))
                    .replace("Neural", "")
                )

            friendly_names = {v: _friendly(v) for v in filtered_voices}

            saved_voice_name_index = 0

            # Verifique se o som salvo está na lista de sons filtrados atualmente
            if saved_voice_name in friendly_names:
                saved_voice_name_index = list(friendly_names.keys()).index(
                    saved_voice_name
                )
            else:
                # Caso contrário, seleciona uma voz padrão com base no idioma atual da IU
                for i, v in enumerate(filtered_voices):
                    if v.lower().startswith(st.session_state["ui_language"].lower()):
                        saved_voice_name_index = i
                        break

            # Se nenhum som correspondente for encontrado, o primeiro som será usado
            if saved_voice_name_index >= len(friendly_names) and friendly_names:
                saved_voice_name_index = 0

            # Certifique-se de que haja uma opção de som
            if tts_mode_enabled and friendly_names:
                voice_name = stable_selectbox(
                    tr("Voiceover Voice"),
                    options=list(friendly_names.keys()),
                    default_value=list(friendly_names.keys())[saved_voice_name_index],
                    key=f"speech_synthesis_select_{selected_tts_server}",
                    format_func=lambda value: friendly_names[value],
                )

                params.voice_name = voice_name
                if not voice.is_no_voice(voice_name):
                    # O espaço reservado sentinela é usado apenas para exibição desabilitada no modo não automático e não substitui o anterior do usuário.
                    # O tom real selecionado pode ser restaurado à sua configuração original após voltar para a dublagem automática.
                    config.ui["voice_name"] = voice_name
            elif tts_mode_enabled:
                # Se não houver som disponível, uma mensagem de aviso será exibida.
                st.warning(
                    tr(
                        "No voices available for the selected TTS server. Please select another server."
                    )
                )
                voice_name = ""
                params.voice_name = ""
                config.ui["voice_name"] = ""
            else:
                # O modo de dublagem não automático não exibe os controles de timbre e apenas reutiliza os valores salvos para manter uma estrutura de parâmetros estável.
                voice_name = saved_voice_name or voice.NO_VOICE_NAME
                params.voice_name = voice_name

            # Quando a versão V2 é selecionada ou o som é V2, a área de serviço e a caixa de entrada da chave API são exibidas.
            if tts_mode_enabled and (
                selected_tts_server == "azure-tts-v2"
                or (voice_name and voice.is_azure_v2_voice(voice_name))
            ):
                saved_azure_speech_region = config.azure.get("speech_region", "")
                saved_azure_speech_key = config.azure.get("speech_key", "")
                azure_speech_region = st.text_input(
                    tr("Speech Region"),
                    value=saved_azure_speech_region,
                    key="azure_speech_region_input",
                )
                azure_speech_key = st.text_input(
                    tr("Speech Key"),
                    value=saved_azure_speech_key,
                    type="password",
                    key="azure_speech_key_input",
                )
                config.azure["speech_region"] = azure_speech_region
                config.azure["speech_key"] = azure_speech_key

            if tts_mode_enabled and selected_tts_server == "gemini-tts":
                # Gemini TTS e Gemini LLM compartilham a mesma chave; fornecer acesso direto no painel de áudio,
                # Os usuários não precisam trocar primeiro de provedor LLM para concluir a configuração de voz.
                gemini_tts_api_key = st.text_input(
                    tr("Gemini API Key"),
                    value=config.app.get("gemini_api_key", ""),
                    type="password",
                    key="gemini_tts_api_key_input",
                )
                config.app["gemini_api_key"] = gemini_tts_api_key

            # Quando o fluxo baseado em silício é selecionado, a caixa de entrada da chave API e as informações de descrição são exibidas.
            if tts_mode_enabled and (
                selected_tts_server == "siliconflow"
                or (voice_name and voice.is_siliconflow_voice(voice_name))
            ):
                saved_siliconflow_api_key = config.siliconflow.get("api_key", "")

                siliconflow_api_key = st.text_input(
                    tr("SiliconFlow API Key"),
                    value=saved_siliconflow_api_key,
                    type="password",
                    key="siliconflow_api_key_input",
                )

                config.siliconflow["api_key"] = siliconflow_api_key

            # Quando o Xiaomi MiMo TTS é selecionado, a chave API do provedor MiMo LLM é reutilizada.
            # Dessa forma, se os usuários utilizarem o MiMo para gerar direitos autorais e fala ao mesmo tempo, eles só precisarão manter uma chave.
            if tts_mode_enabled and (
                selected_tts_server == "mimo-tts"
                or (voice_name and voice.is_mimo_voice(voice_name))
            ):
                saved_mimo_api_key = config.app.get("mimo_api_key", "")

                mimo_api_key = st.text_input(
                    tr("MiMo API Key"),
                    value=saved_mimo_api_key,
                    type="password",
                    key="mimo_tts_api_key_input",
                )

                config.app["mimo_api_key"] = mimo_api_key

            # ElevenLabs API key section
            if tts_mode_enabled and (
                selected_tts_server == "elevenlabs"
                or (voice_name and voice.is_elevenlabs_voice(voice_name))
            ):
                _render_elevenlabs_api_key_input(
                    "ElevenLabs API Key",
                )
                elevenlabs_api_key_rendered = True

                _elevenlabs_models = [
                    "eleven_multilingual_v2",
                    "eleven_flash_v2_5",
                    "eleven_v3",
                ]
                saved_elevenlabs_model = config.elevenlabs.get(
                    "model_id", "eleven_multilingual_v2"
                )
                if saved_elevenlabs_model not in _elevenlabs_models:
                    saved_elevenlabs_model = "eleven_multilingual_v2"
                elevenlabs_model = stable_selectbox(
                    tr("ElevenLabs Model"),
                    options=_elevenlabs_models,
                    default_value=saved_elevenlabs_model,
                    key="elevenlabs_model_select",
                )
                config.elevenlabs["model_id"] = elevenlabs_model

            # Chatterbox API settings section (self-hosted, OpenAI-compatible)
            if tts_mode_enabled and (
                selected_tts_server == "chatterbox"
                or (voice_name and voice.is_chatterbox_voice(voice_name))
            ):
                chatterbox_base_url = st.text_input(
                    tr("Chatterbox Base URL"),
                    value=config.chatterbox.get("base_url")
                    or DEFAULT_CHATTERBOX_BASE_URL,
                    key="chatterbox_base_url_input",
                    placeholder=tr("Chatterbox Base URL Placeholder"),
                )
                config.chatterbox["base_url"] = (chatterbox_base_url or "").strip()

                chatterbox_api_key = st.text_input(
                    tr("Chatterbox API Key"),
                    value=config.chatterbox.get("api_key", ""),
                    type="password",
                    key="chatterbox_api_key_input",
                )
                config.chatterbox["api_key"] = chatterbox_api_key

                chatterbox_model = st.text_input(
                    tr("Chatterbox Model"),
                    value=config.chatterbox.get("model_id") or DEFAULT_CHATTERBOX_MODEL,
                    key="chatterbox_model_input",
                )
                config.chatterbox["model_id"] = (
                    chatterbox_model or DEFAULT_CHATTERBOX_MODEL
                ).strip()

                _saved_chatterbox_voices = (
                    _parse_chatterbox_voices(config.chatterbox.get("voices"))
                    or DEFAULT_CHATTERBOX_VOICES
                )
                if isinstance(_saved_chatterbox_voices, list):
                    _saved_chatterbox_voices = ", ".join(_saved_chatterbox_voices)
                chatterbox_voices = st.text_input(
                    tr("Chatterbox Voices"),
                    value=str(_saved_chatterbox_voices or ""),
                    key="chatterbox_voices_input",
                    placeholder=tr("Chatterbox Voices Placeholder"),
                )
                config.chatterbox["voices"] = _parse_chatterbox_voices(
                    chatterbox_voices
                )

            # Os três modos apenas renderizam os controles realmente necessários para a tarefa atual. Dublagem automática com volume e velocidade de fala ajustáveis;
            # O upload de áudio requer apenas arquivo e volume; sem dublagem não exibirá mais configurações inválidas.
            params.voice_name = (
                voice.NO_VOICE_NAME if voice_mode == VOICE_MODE_NONE else voice_name
            )
            params.voice_volume = 1.0
            params.voice_rate = 1.0
            uploaded_audio_file = None

            if tts_mode_enabled:
                voice_control_cols = st.columns(2)
                with voice_control_cols[0]:
                    params.voice_volume = stable_selectbox(
                        tr("Voiceover Volume"),
                        options=[0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0, 4.0, 5.0],
                        default_value=1.0,
                        key="voice_volume_select",
                        format_func=lambda value: f"{int(value * 100)}%",
                        help=tr("Voiceover Volume Help"),
                    )

                with voice_control_cols[1]:
                    params.voice_rate = stable_selectbox(
                        tr("Voiceover Speed"),
                        options=[0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 1.8, 2.0],
                        default_value=1.0,
                        key="voice_rate_select",
                        format_func=lambda value: f"{value:.1f}×",
                        help=tr("Voiceover Speed Help"),
                    )

                # A audição deve ser realizada após os controles de volume e velocidade de fala, garantindo que a chamada utilize os valores de controle atuais.
                _render_voice_preview(
                    params,
                    friendly_names,
                    selected_tts_server,
                    voice_name,
                )
            elif voice_mode == VOICE_MODE_UPLOAD:
                custom_audio_file_types = sorted(
                    extension.removeprefix(".") for extension in CUSTOM_AUDIO_EXTENSIONS
                )
                uploaded_audio_file = st.file_uploader(
                    tr("Upload Voiceover File"),
                    type=custom_audio_file_types
                    + [file_type.upper() for file_type in custom_audio_file_types],
                    accept_multiple_files=False,
                    key="custom_audio_file_uploader",
                    help=tr("Upload Voiceover File Help"),
                )
                params.voice_volume = stable_selectbox(
                    tr("Voiceover Volume"),
                    options=[0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0, 4.0, 5.0],
                    default_value=1.0,
                    key="voice_volume_select",
                    format_func=lambda value: f"{int(value * 100)}%",
                    help=tr("Voiceover Volume Help"),
                )
                if uploaded_audio_file:
                    st.audio(uploaded_audio_file, format="audio/mp3")
                    st.info(
                        tr(
                            "Custom audio will be used directly. TTS synthesis will be skipped for this task."
                        )
                    )
            uploaded_bgm_file = _render_background_music_settings(
                params,
                elevenlabs_api_key_rendered=elevenlabs_api_key_rendered,
            )
    return uploaded_audio_file, uploaded_bgm_file, voice_mode


def _render_subtitle_settings(panel, params):
    """Renderize as configurações de legenda e atualize os parâmetros de geração."""
    with panel:
        with st.container(border=True):
            st.write(tr("Subtitle Settings"))
            st.session_state.setdefault(
                "subtitle_enabled_checkbox",
                DEFAULT_SUBTITLE_SETTINGS["subtitle_enabled"],
            )
            params.subtitle_enabled = st.checkbox(
                tr("Enable Subtitles"),
                key="subtitle_enabled_checkbox",
            )
            subtitle_settings_disabled = not params.subtitle_enabled
            font_names = get_all_fonts()
            saved_font_name = config.ui.get(
                "font_name", DEFAULT_SUBTITLE_SETTINGS["font_name"]
            )
            saved_font_name_index = 0
            if saved_font_name in font_names:
                saved_font_name_index = font_names.index(saved_font_name)
            params.font_name = stable_selectbox(
                tr("Font"),
                options=font_names,
                default_value=font_names[saved_font_name_index] if font_names else "",
                key="font_name_select",
                disabled=subtitle_settings_disabled,
            )
            config.ui["font_name"] = params.font_name

            subtitle_positions = [
                (tr("Top"), "top"),
                (tr("Center"), "center"),
                (tr("Bottom"), "bottom"),
                (tr("Custom"), "custom"),
            ]
            saved_subtitle_position = config.ui.get(
                "subtitle_position", DEFAULT_SUBTITLE_SETTINGS["subtitle_position"]
            )
            saved_position_index = 2
            for i, (_, pos_value) in enumerate(subtitle_positions):
                if pos_value == saved_subtitle_position:
                    saved_position_index = i
                    break
            selected_subtitle_position = stable_selectbox(
                tr("Position"),
                options=[value for _, value in subtitle_positions],
                default_value=subtitle_positions[saved_position_index][1],
                key="subtitle_position_select",
                format_func=lambda value: dict(
                    (v, label) for label, v in subtitle_positions
                )[value],
                disabled=subtitle_settings_disabled,
            )
            params.subtitle_position = selected_subtitle_position
            config.ui["subtitle_position"] = params.subtitle_position

            if params.subtitle_position == "custom":
                saved_custom_position = config.ui.get(
                    "custom_position", DEFAULT_SUBTITLE_SETTINGS["custom_position"]
                )
                st.session_state.setdefault(
                    "custom_position_input", str(saved_custom_position)
                )
                custom_position = st.text_input(
                    tr("Custom Position (% from top)"),
                    key="custom_position_input",
                    disabled=subtitle_settings_disabled,
                )
                try:
                    params.custom_position = float(custom_position)
                    if params.custom_position < 0 or params.custom_position > 100:
                        st.error(tr("Please enter a value between 0 and 100"))
                    else:
                        config.ui["custom_position"] = params.custom_position
                except ValueError:
                    st.error(tr("Please enter a valid number"))

            # As etiquetas coloridas para idiomas não chineses são geralmente mais longas do que para o chinês. Deixe a largura apropriada para o seletor de cores,
            # Evite embrulhar as etiquetas e ainda deixe espaço suficiente para o controle deslizante de tamanho da fonte se movimentar.
            font_cols = st.columns([0.42, 0.58])
            with font_cols[0]:
                saved_text_fore_color = config.ui.get(
                    "text_fore_color", DEFAULT_SUBTITLE_SETTINGS["text_fore_color"]
                )
                st.session_state.setdefault("font_color_picker", saved_text_fore_color)
                params.text_fore_color = st.color_picker(
                    tr("Font Color"),
                    key="font_color_picker",
                    disabled=subtitle_settings_disabled,
                )
                config.ui["text_fore_color"] = params.text_fore_color

            with font_cols[1]:
                saved_font_size = config.ui.get(
                    "font_size", DEFAULT_SUBTITLE_SETTINGS["font_size"]
                )
                st.session_state.setdefault("font_size_slider", saved_font_size)
                params.font_size = st.slider(
                    tr("Font Size"),
                    30,
                    100,
                    key="font_size_slider",
                    disabled=subtitle_settings_disabled,
                )
                config.ui["font_size"] = params.font_size

            stroke_cols = st.columns([0.42, 0.58])
            with stroke_cols[0]:
                st.session_state.setdefault(
                    "stroke_color_picker", DEFAULT_SUBTITLE_SETTINGS["stroke_color"]
                )
                params.stroke_color = st.color_picker(
                    tr("Stroke Color"),
                    key="stroke_color_picker",
                    disabled=subtitle_settings_disabled,
                )
            with stroke_cols[1]:
                st.session_state.setdefault(
                    "stroke_width_slider", DEFAULT_SUBTITLE_SETTINGS["stroke_width"]
                )
                params.stroke_width = st.slider(
                    tr("Stroke Width"),
                    0.0,
                    10.0,
                    key="stroke_width_slider",
                    disabled=subtitle_settings_disabled,
                )

            # O nome localizado do switch de fundo geralmente é mais longo que o rótulo colorido, permitindo assim que o switch ocupe um pouco mais de espaço.
            subtitle_bg_cols = st.columns([0.55, 0.45])
            saved_subtitle_background_enabled = config.ui.get(
                "subtitle_background_enabled",
                DEFAULT_SUBTITLE_SETTINGS["subtitle_background_enabled"],
            )
            st.session_state.setdefault(
                "subtitle_background_enabled_checkbox",
                saved_subtitle_background_enabled,
            )
            with subtitle_bg_cols[0]:
                subtitle_background_enabled = st.checkbox(
                    tr("Enable Subtitle Background"),
                    key="subtitle_background_enabled_checkbox",
                    disabled=subtitle_settings_disabled,
                )
            config.ui["subtitle_background_enabled"] = subtitle_background_enabled

            # A cor de fundo e o estilo dos cantos arredondados estão subordinados à opção de fundo da legenda. Os controles filhos sempre permanecem na página,
            # Quando a chave pai é desligada, ela é desabilitada uniformemente para evitar saltos de layout causados ​​pelo desaparecimento de um controle enquanto outro controle é desabilitado.
            # Os valores de cores ainda são salvos na configuração da UI e a seleção anterior do usuário pode ser restaurada após reativar o plano de fundo;
            # O parâmetro passado para o serviço de geração é definido como False para garantir que o estado desligado não renderize realmente o plano de fundo.
            saved_subtitle_background_color = config.ui.get(
                "subtitle_background_color",
                DEFAULT_SUBTITLE_SETTINGS["subtitle_background_color"],
            )
            st.session_state.setdefault(
                "subtitle_background_color_picker",
                saved_subtitle_background_color,
            )
            with subtitle_bg_cols[1]:
                selected_subtitle_background_color = st.color_picker(
                    tr("Subtitle Background Color"),
                    key="subtitle_background_color_picker",
                    disabled=subtitle_settings_disabled
                    or not subtitle_background_enabled,
                )
            config.ui["subtitle_background_color"] = selected_subtitle_background_color
            params.text_background_color = (
                selected_subtitle_background_color
                if subtitle_background_enabled
                else False
            )

            saved_rounded_subtitle_background = config.ui.get(
                "rounded_subtitle_background",
                DEFAULT_SUBTITLE_SETTINGS["rounded_subtitle_background"],
            )
            # Quando o fundo está desativado, o fundo arredondado não tem fundo renderizável. Desative o controle aqui, mas mantenha a configuração original.
            # Na próxima vez que o usuário reativar o plano de fundo da legenda, ele poderá continuar a usar a preferência de canto arredondado salva anteriormente.
            rounded_background_disabled = (
                subtitle_settings_disabled or not subtitle_background_enabled
            )
            st.session_state.setdefault(
                "rounded_subtitle_background_checkbox",
                saved_rounded_subtitle_background,
            )
            selected_rounded_subtitle_background = st.checkbox(
                tr("Rounded Subtitle Background"),
                help=tr("Rounded Subtitle Background Help"),
                disabled=rounded_background_disabled,
                key="rounded_subtitle_background_checkbox",
            )
            params.rounded_subtitle_background = (
                selected_rounded_subtitle_background
                if subtitle_background_enabled
                else False
            )
            if not subtitle_settings_disabled and subtitle_background_enabled:
                config.ui["rounded_subtitle_background"] = (
                    selected_rounded_subtitle_background
                )

            if video.subtitle_colors_are_indistinguishable(params):
                # A mesma configuração de cores ainda é uma escolha legal do usuário, portanto só é solicitada na área de configuração de legendas.
                # Não impede a geração. Os usuários podem decidir se querem continuar com base nas necessidades visuais reais.
                st.warning(tr("Subtitle Colors Are Indistinguishable"))

            subtitle_preview_text = params.video_script or params.video_subject
            selected_font_path = os.path.join(font_dir, params.font_name)
            if (
                params.subtitle_enabled
                and subtitle_preview_text
                and not video.subtitle_font_supports_text(
                    selected_font_path, subtitle_preview_text
                )
            ):
                st.warning(tr("Subtitle Font Does Not Support Text"))

            if st.button(
                tr("Restore Default Subtitle Settings"),
                key="restore_default_subtitle_settings",
                icon=":material/restart_alt:",
                on_click=reset_subtitle_settings,
                use_container_width=True,
            ):
                st.toast(tr("Default Subtitle Settings Restored"))


def _render_generation_controls(
    params, uploaded_files, uploaded_audio_file, uploaded_bgm_file, voice_mode
):
    """
    Verifique as dependências geradas, envie tarefas e renderize logs e resultados de fragmentação.

    Retorne a esta página para verificar se a nova tarefa foi enviada com sucesso. A configuração foi salva antes do envio e o chamador de acordo
    Ignore o salvamento repetido no final da página para evitar longas tarefas em segundo plano que prendem bloqueios de configuração e bloqueiam o Streamlit principal
    roteiro. O script principal deve terminar a tempo para que o fragmento agendado possa atualizar continuamente o progresso e o log de tarefas.
    """
    restore_upload_requirements = st.session_state.get(
        "task_restore_upload_requirements", {}
    )
    has_local_materials = bool(
        uploaded_files or st.session_state.get("local_video_materials", [])
    )
    has_custom_audio = bool(uploaded_audio_file)
    unmet_restore_requirements = _get_unmet_restore_upload_requirements(
        restore_upload_requirements,
        video_source=params.video_source,
        voice_name=params.voice_name or "",
        has_local_materials=has_local_materials,
        has_custom_audio=has_custom_audio,
        voice_mode=voice_mode,
    )
    if "local_materials" in unmet_restore_requirements:
        st.warning(tr("Task Restore Local Materials Warning"))
    if "custom_audio" in unmet_restore_requirements:
        st.warning(tr("Task Restore Custom Audio Warning"))
    if restore_upload_requirements and not unmet_restore_requirements:
        # O usuário reenviou o arquivo ou alterou ativamente a fonte/tom do material. Neste momento, a dependência de upload de tarefas históricas
        # Isso foi resolvido de forma clara e a marca foi apagada para evitar que compilações normais subsequentes continuem exibindo o prompt antigo.
        st.session_state.pop("task_restore_upload_requirements", None)

    start_button = st.button(
        tr("Generate Video"),
        use_container_width=True,
        type="primary",
        key="generate_video_button",
        on_click=_prepare_generation_task,
    )
    render_onboarding_tour()
    if start_button:
        config.save_config()
        task_id = st.session_state.get("pending_generation_task_id") or str(uuid4())
        _add_active_generation_task(
            task_id,
            subject=params.video_subject or params.video_script or task_id,
        )
        if not params.video_subject and not params.video_script:
            _remove_active_generation_task(task_id)
            st.error(tr("Video Script and Subject Cannot Both Be Empty"))
            st.stop()

        if params.video_source not in ["pexels", "pixabay", "coverr", "local"]:
            _remove_active_generation_task(task_id)
            st.error(tr("Please Select a Valid Video Source"))
            st.stop()

        if params.video_source == "pexels" and not config.app.get(
            "pexels_api_keys", ""
        ):
            _remove_active_generation_task(task_id)
            st.error(tr("Please Enter the Pexels API Key"))
            st.stop()

        if params.video_source == "pixabay" and not config.app.get(
            "pixabay_api_keys", ""
        ):
            _remove_active_generation_task(task_id)
            st.error(tr("Please Enter the Pixabay API Key"))
            st.stop()

        if params.video_source == "coverr" and not config.app.get(
            "coverr_api_keys", ""
        ):
            _remove_active_generation_task(task_id)
            st.error(tr("Please Enter the Coverr API Key"))
            st.stop()

        if (
            params.bgm_type == "sonilo"
            and bgm_service.should_use_bgm(params.bgm_type, params.bgm_volume)
            and not sonilo_service.is_enabled()
        ):
            _remove_active_generation_task(task_id)
            st.error(tr("Sonilo API Key Required"))
            st.stop()

        if (
            params.bgm_type == "elevenlabs"
            and bgm_service.should_use_bgm(params.bgm_type, params.bgm_volume)
            and not elevenlabs_music_service.is_enabled()
        ):
            _remove_active_generation_task(task_id)
            st.error(tr("ElevenLabs API Key Required"))
            st.stop()

        if params.video_source == "local" and not has_local_materials:
            # Continuar a execução quando o material local estiver vazio gerará primeiro TTS/legendas e, finalmente, falhará no estágio de pré-processamento do material.
            # A interceptação antes do início da tarefa pode evitar chamadas de API e arquivos intermediários sem sentido.
            _remove_active_generation_task(task_id)
            st.error(tr("Please Upload Local Materials First"))
            st.stop()

        if voice_mode == VOICE_MODE_UPLOAD and not uploaded_audio_file:
            # O upload de áudio é o método de dublagem explicitamente selecionado pelo usuário, e o TTS não pode ser retornado silenciosamente quando o arquivo está faltando.
            # Intercepte antes do início da tarefa para evitar a produção de filmes inconsistentes com a seleção do usuário.
            _remove_active_generation_task(task_id)
            st.error(tr("Please Upload Voiceover File First"))
            st.stop()

        if "custom_audio" in unmet_restore_requirements:
            # O áudio personalizado histórico não pode ser preenchido automaticamente. Quando o usuário não fez upload novamente e não alterou ativamente o timbre,
            # O fallback silencioso para TTS deve ser evitado, caso contrário os resultados regenerados serão inconsistentes com a voz de tarefa original.
            _remove_active_generation_task(task_id)
            st.error(tr("Task Restore Custom Audio Warning"))
            st.stop()

        if uploaded_bgm_file and bgm_service.should_use_bgm(
            params.bgm_type, params.bgm_volume
        ):
            try:
                saved_bgm_name = bgm_service.save_bgm_upload(
                    uploaded_bgm_file.name, uploaded_bgm_file
                )
            except bgm_service.BgmUploadError as exc:
                _remove_active_generation_task(task_id)
                logger.warning(f"WebUI background music upload rejected: {str(exc)}")
                st.error(tr("Invalid Background Music"))
                st.stop()
            except bgm_service.BgmServiceError as exc:
                _remove_active_generation_task(task_id)
                logger.error(f"WebUI background music upload failed: {str(exc)}")
                st.error(tr("Background Music Validation Failed"))
                st.stop()
            # Após salvar com sucesso, apenas o nome do arquivo será gravado nos parâmetros da tarefa. O serviço de vídeo estará em duas listas de permissões BGM
            # Analise novamente o diretório para evitar persistir ou exibir o caminho absoluto do servidor para o usuário.
            params.bgm_file = saved_bgm_name
        elif uploaded_bgm_file:
            # 0 O serviço de vídeo não usará nenhuma música de fundo quando o volume for alterado, portanto, os arquivos enviados que foram visualizados não serão mais
            # Persista no armazenamento. Quando o usuário aumentar o volume posteriormente, ele poderá clicar diretamente em Gerar novamente para concluir o salvamento.
            params.bgm_file = ""

        if uploaded_audio_file:
            task_dir = utils.task_dir(task_id)
            try:
                custom_audio_path = _build_uploaded_file_path(
                    uploaded_audio_file,
                    task_dir,
                    CUSTOM_AUDIO_EXTENSIONS,
                    "custom-audio",
                )
            except ValueError:
                _remove_active_generation_task(task_id)
                st.error(tr("Unsupported Upload File Type"))
                st.stop()
            with open(custom_audio_path, "wb") as f:
                f.write(uploaded_audio_file.getbuffer())
            params.custom_audio_file = custom_audio_path

        if uploaded_files:
            local_videos_dir = utils.storage_dir("local_videos", create=True)
            # Cada vez que você fizer um novo upload, o material selecionado desta vez será usado como padrão para evitar a adição repetida de materiais antigos.
            params.video_materials = []
            persisted_local_materials = []
            for file in uploaded_files:
                try:
                    file_path = _build_uploaded_file_path(
                        file,
                        local_videos_dir,
                        LOCAL_MATERIAL_EXTENSIONS,
                        "material",
                    )
                except ValueError:
                    _remove_active_generation_task(task_id)
                    st.error(tr("Unsupported Upload File Type"))
                    st.stop()
                with open(file_path, "wb") as f:
                    f.write(file.getbuffer())
                    m = MaterialInfo()
                    m.provider = "local"
                    m.url = file_path
                    params.video_materials.append(m)
                    persisted_local_materials.append(
                        {
                            "provider": m.provider,
                            "url": m.url,
                            "duration": m.duration,
                        }
                    )
            # Escreva o material de vídeo que foi carregado e salvo localmente na sessão para reutilização direta quando somente a cópia for modificada posteriormente.
            st.session_state["local_video_materials"] = persisted_local_materials
        elif (
            params.video_source == "local" and st.session_state["local_video_materials"]
        ):
            # Quando o usuário não reenvia o arquivo, a lista de materiais locais que foi salva pela última vez no disco é reutilizada.
            params.video_materials = []
            for material in st.session_state["local_video_materials"]:
                m = MaterialInfo()
                m.provider = material.get("provider", "local")
                m.url = material.get("url", "")
                m.duration = material.get("duration", 0)
                if m.url:
                    params.video_materials.append(m)

        reusable_voice_preview = _get_reusable_full_voice_preview(
            params,
            voice_mode,
        )
        if reusable_voice_preview:
            # O cache de audição existe apenas para a sessão atual do Streamlit. Grave o áudio no diretório de tarefas de destino antes de enviar.
            # O thread de segundo plano lê apenas os próprios arquivos da tarefa; mesmo que a página seja executada novamente, o navegador seja fechado ou
            # Quando os usuários experimentam outros timbres, isso não afetará as tarefas de geração que já foram enfileiradas.
            preview_audio_file = os.path.join(
                utils.task_dir(task_id),
                "audio.mp3",
            )
            with open(preview_audio_file, "wb") as file:
                file.write(reusable_voice_preview.pop("audio_bytes"))
            reusable_voice_preview["audio_file"] = preview_audio_file
            logger.info(
                f"reuse full voice preview for task: "
                f"task_id={task_id}, duration={reusable_voice_preview['duration']:.2f}s"
            )

        try:
            st.toast(tr("Generating Video"))
            logger.info(tr("Start Generating Video"))
            logger.info(utils.to_json(params))
            webui_task.submit_generation(
                task_id=task_id,
                params=params,
                capture_logs=not config.ui.get("hide_log", False),
                voice_preview=reusable_voice_preview,
            )
        except Exception:
            _remove_active_generation_task(task_id)
            st.error(tr("Video Generation Failed"))
            st.stop()

        st.session_state["current_generation_task_id"] = task_id
        logger.info(f"WebUI generation task submitted: task_id={task_id}")

    _render_current_generation_task()
    return start_button


def _render_application():
    """Renderize a barra superior, a janela pop-up, o formulário gerado e os resultados da tarefa em uma ordem fixa."""
    _render_top_bar()

    if st.session_state.get("settings_dialog_open", False):
        _render_settings_dialog()

    restore_applied = _apply_pending_task_restore()
    restore_candidate_id = st.session_state.get("task_restore_candidate_id")
    if restore_candidate_id:
        _render_task_restore_dialog(restore_candidate_id)
    restore_succeeded = st.session_state.pop("task_restore_succeeded", False)
    if restore_applied or restore_succeeded:
        st.success(tr("Task Configuration Loaded"))

    with st.container(key="main_settings_grid"):
        panel = st.columns(4)
    left_panel = panel[0]
    middle_panel = panel[1]
    audio_panel = panel[2]
    right_panel = panel[3]

    params = VideoParams(video_subject="")
    params.match_materials_to_script = bool(
        st.session_state.get("match_materials_to_script", False)
    )
    _render_script_settings(left_panel, params)

    uploaded_files = _render_video_settings(middle_panel, params)
    uploaded_audio_file, uploaded_bgm_file, voice_mode = _render_audio_settings(
        audio_panel, params
    )

    _render_subtitle_settings(right_panel, params)

    generation_submitted = _render_generation_controls(
        params,
        uploaded_files,
        uploaded_audio_file,
        uploaded_bgm_file,
        voice_mode,
    )

    # A ramificação de construção salvou a configuração antes de iniciar o thread em segundo plano. Não há lucro em poupar novamente aqui, e é possível
    # Competir com tarefas longas mantendo runtime_config_lock, fazendo com que o script Streamlit atual bloqueie o tempo todo
    # Até que o vídeo seja concluído, o fragmento de log não poderá ser executado. As interações comuns da página ainda são salvas de maneira uniforme.
    if not generation_submitted:
        config.save_config()


_render_application()
