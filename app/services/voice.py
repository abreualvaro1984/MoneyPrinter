import asyncio
import base64
import io
import inspect
import json
import math
import os
import queue
import re
import subprocess
import threading
import time
import unicodedata
from datetime import datetime
from typing import Union
from xml.sax.saxutils import escape, unescape

import edge_tts
import requests
from edge_tts import SubMaker
from loguru import logger
from moviepy.video.tools import subtitles
from moviepy.audio.io.AudioFileClip import AudioFileClip
from openai import OpenAI

from app.config import config
from app.utils import utils

_DEFAULT_EDGE_TTS_TIMEOUT_SECONDS = 30.0
_MIMO_DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
_MIMO_DEFAULT_TTS_MODEL = "mimo-v2.5-tts"
NO_VOICE_NAME = "no-voice"
# `none` é o sinalizador de não dublagem usado no PR #981. Este valor é compatível com este valor no curto prazo para evitar
# Os usuários da API que chamaram manualmente esta ramificação serão inválidos imediatamente após a atualização; WebUI e novo código serão usados ​​uniformemente
# `sem voz` mais explícito.
_NO_VOICE_ALIASES = {NO_VOICE_NAME, "none"}


def _configure_pydub_ffmpeg(audio_segment_cls):
    configured_ffmpeg = utils.get_ffmpeg_binary()
    if configured_ffmpeg:
        audio_segment_cls.converter = configured_ffmpeg


def mktimestamp(time_unit: float) -> str:
    """Converta as unidades de tempo de 100 nanossegundos usadas por edge_tts em carimbos de data e hora de legenda.

    edge_tts 7.x não exporta mais `mktimestamp` na versão antiga, mas sim o link da legenda antiga no projeto
    Esta função de formatação também é necessária para ser compatível com Azure v2, Gemini, SiliconFlow, etc.
    Linha do tempo de legenda construída manualmente, portanto, uma implementação equivalente é incorporada aqui."""
    hour = math.floor(time_unit / 10**7 / 3600)
    minute = math.floor((time_unit / 10**7 / 60) % 60)
    seconds = (time_unit / 10**7) % 60
    return f"{hour:02d}:{minute:02d}:{seconds:06.3f}"


def get_siliconflow_voices() -> list[str]:
    """Obtenha uma lista de sons fluidos baseados em silício

    Retorna:
        Lista de vozes, no formato ["siliconflow:FunAudioLLM/CosyVoice2-0.5B:alex", ...]"""
    # Lista fluida de sons e gêneros correspondentes baseada em silício (para exibição)
    voices_with_gender = [
        ("FunAudioLLM/CosyVoice2-0.5B", "alex", "Male"),
        ("FunAudioLLM/CosyVoice2-0.5B", "anna", "Female"),
        ("FunAudioLLM/CosyVoice2-0.5B", "bella", "Female"),
        ("FunAudioLLM/CosyVoice2-0.5B", "benjamin", "Male"),
        ("FunAudioLLM/CosyVoice2-0.5B", "charles", "Male"),
        ("FunAudioLLM/CosyVoice2-0.5B", "claire", "Female"),
        ("FunAudioLLM/CosyVoice2-0.5B", "david", "Male"),
        ("FunAudioLLM/CosyVoice2-0.5B", "diana", "Female"),
    ]

    # Adicionar Siliconflow: prefixo e formato como nome de exibição
    return [
        f"siliconflow:{model}:{voice}-{gender}"
        for model, voice, gender in voices_with_gender
    ]


def get_gemini_voices() -> list[str]:
    """Obtenha a lista de sons do Gemini TTS
    
    Retorna:
        Lista de sons no formato ["gemini:Zephyr-Female", "gemini:Puck-Male", ...]"""
    # Lista de vozes suportadas pelo Gemini TTS
    voices_with_gender = [
        ("Zephyr", "Female"),
        ("Puck", "Male"), 
        ("Charon", "Male"),
        ("Kore", "Female"),
        ("Fenrir", "Male"),
        ("Aoede", "Female"),
        ("Thalia", "Female"),
        ("Sage", "Male"),
        ("Echo", "Female"),
        ("Harmony", "Female"),
        ("Lux", "Female"),
        ("Nova", "Female"),
        ("Vale", "Male"),
        ("Orion", "Male"),
        ("Atlas", "Male"),
    ]
    
    # Adicione gemini: prefixo e formato como nome de exibição
    return [
        f"gemini:{voice}-{gender}"
        for voice, gender in voices_with_gender
    ]


def get_mimo_voices() -> list[str]:
    """Obtenha a lista de tons predefinidos para Xiaomi MiMo V2.5 TTS.

    Atualmente, apenas o modo de tom predefinido `mimo-v2.5-tts` na documentação oficial está conectado. design de som
    `mimo-v2.5-tts-voicedesign` e clone de som `mimo-v2.5-tts-voiceclone`
    São necessários formulários de entrada adicionais e processos de upload de materiais. Não os misture em caixas suspensas TTS comuns para evitar
    Os usuários acreditam erroneamente que a seleção de uma identificação de voz completará todos os recursos avançados."""
    voices_with_gender = [
        ("mimo_default", "Female"),
        ("冰糖", "Female"),
        ("茉莉", "Female"),
        ("苏打", "Male"),
        ("白桦", "Male"),
        ("Mia", "Female"),
        ("Chloe", "Female"),
        ("Milo", "Male"),
        ("Dean", "Male"),
    ]

    return [f"mimo:{voice}-{gender}" for voice, gender in voices_with_gender]


def get_elevenlabs_voices(api_key: str) -> list[str]:
    if not api_key:
        return []
    try:
        url = "https://api.elevenlabs.io/v2/voices"
        params = {"is_favorite": "true", "page_size": 100}
        headers = {"xi-api-key": api_key}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.warning(
                f"ElevenLabs voices fetch failed with status {response.status_code}: {response.text}"
            )
            return []
        data = response.json()
        voices = data.get("voices", [])
        return [
            f"elevenlabs:{v['voice_id']}:{v['name']}"
            for v in voices
            if v.get("voice_id") and v.get("name") and v.get("status") != "disabled"
        ]
    except Exception as e:
        logger.warning(f"ElevenLabs voices fetch failed: {str(e)}")
        return []


def get_chatterbox_voices() -> list[str]:
    """Return the configured Chatterbox voices.

    Chatterbox is self-hosted, so there is no global voice catalog. Operators
    list the voice names exposed by their server via ``[chatterbox] voices``
    (a TOML array, or a comma-separated string). Each entry is normalised to
    the ``chatterbox:<name>`` format used by the TTS dispatcher.
    """
    voices = config.chatterbox.get("voices", []) or []
    if isinstance(voices, str):
        voices = [v.strip() for v in voices.split(",") if v.strip()]
    result = []
    for v in voices:
        v = str(v).strip()
        if not v:
            continue
        result.append(v if v.startswith("chatterbox:") else f"chatterbox:{v}")
    if not result:
        # keep the dropdown usable even before any voice is configured
        result = ["chatterbox:default-Female"]
    return result


_AZURE_VOICES_DATA_FILE = os.path.join(
    os.path.dirname(__file__), "data", "azure_voices.json"
)
_azure_voices_cache = None


def _load_azure_voices() -> list[dict]:
    global _azure_voices_cache
    if _azure_voices_cache is None:
        with open(_AZURE_VOICES_DATA_FILE, "r", encoding="utf-8") as f:
            _azure_voices_cache = json.load(f)
    return _azure_voices_cache


def get_all_azure_voices(filter_locals=None) -> list[str]:
    voices = []
    for item in _load_azure_voices():
        name = item["name"]
        gender = item["gender"]
        # Aplicar filtros
        if filter_locals and any(
            name.lower().startswith(fl.lower()) for fl in filter_locals
        ):
            voices.append(f"{name}-{gender}")
        elif not filter_locals:
            voices.append(f"{name}-{gender}")

    voices.sort()
    return voices


def parse_voice_name(name: str):
    # zh-CN-XiaoyiNeural-Female
    # zh-CN-YunxiNeural-Male
    # zh-CN-XiaoxiaoMultilingualNeural-V2-Female
    name = name.replace("-Female", "").replace("-Male", "").strip()
    return name


def is_azure_v2_voice(voice_name: str):
    voice_name = parse_voice_name(voice_name)
    if voice_name.endswith("-V2"):
        return voice_name.replace("-V2", "").strip()
    return ""


def is_siliconflow_voice(voice_name: str):
    """Verifique se é o som do fluxo baseado em silício"""
    return voice_name.startswith("siliconflow:")


def is_gemini_voice(voice_name: str):
    """Verifique se é o som do Gemini TTS"""
    return voice_name.startswith("gemini:")


def is_mimo_voice(voice_name: str):
    """Verifique se é o som do Xiaomi MiMo TTS"""
    return voice_name.startswith("mimo:")


def is_elevenlabs_voice(voice_name: str) -> bool:
    return (voice_name or "").startswith("elevenlabs:")


def is_chatterbox_voice(voice_name: str) -> bool:
    return (voice_name or "").startswith("chatterbox:")


def is_no_voice(voice_name: str | None) -> bool:
    """Determine se o usuário selecionou explicitamente o modo "sem dublagem".

    Aqui nós deliberadamente não tratamos strings vazias como nenhuma dublagem: é mais provável que uma voz vazia seja uma configuração danificada ou uma versão antiga.
    O estado da WebUI foi perdido ou os parâmetros da interface estão ausentes. Apenas sentinelas explícitas entram no ramo silencioso,
    Isso evita que erros reais sejam disfarçados como compilações normais."""
    return str(voice_name or "").strip().lower() in _NO_VOICE_ALIASES


def estimate_no_voice_duration(text: str) -> float:
    """Estime uma duração estável da linha do tempo do vídeo para o modo não dublado.

    Nenhuma dublagem ainda requer um espaço reservado de áudio para cortar a filmagem existente, a linha do tempo das legendas e a composição final.
    Mantenha a estratégia de estimativa o mais simples possível:
    1. Os caracteres chineses e outros caracteres CJK são estimados em cerca de 4,2 palavras/segundo;
    2. Inglês/Números são estimados em aproximadamente 2,7 palavras/segundo;
    3. O texto em outros idiomas é estimado em aproximadamente 4,0 caracteres/segundo, abrangendo russo, árabe,
       Kana japonês, coreano e outros textos não-ASCII;
    4. Adicione uma pequena pausa a cada segmento de frase para que a troca de legendas não seja muito apertada;
    5. Pelo menos 3 segundos para evitar scripts extremamente curtos que gerem 0 segundos de áudio."""
    normalized_text = (text or "").strip()
    if not normalized_text:
        return 3.0

    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", normalized_text))
    words = len(re.findall(r"[A-Za-z0-9]+", normalized_text))
    ascii_word_chars = sum(len(word) for word in re.findall(r"[A-Za-z0-9]+", normalized_text))
    other_text_chars = 0
    for char in normalized_text:
        # As categorias Unicode começam com L para representar letras em vários idiomas e N para representar números. Já sozinho antes
        # Palavras CJK e ASCII são contadas, e apenas o texto restante é contado aqui para evitar contagens repetidas de inglês.
        category = unicodedata.category(char)
        if category.startswith(("L", "N")):
            other_text_chars += 1
    other_text_chars = max(other_text_chars - cjk_chars - ascii_word_chars, 0)
    sentence_count = max(len(utils.split_string_by_punctuations(normalized_text)), 1)

    cjk_duration = cjk_chars / 4.2
    word_duration = words / 2.7
    other_text_duration = other_text_chars / 4.0
    pause_duration = max(sentence_count - 1, 0) * 0.35
    return max(3.0, cjk_duration + word_duration + other_text_duration + pause_duration)


def generate_silent_audio(duration_seconds: float, output_file: str) -> bool:
    """Gera áudio silencioso MP3 como espaço reservado na linha do tempo para o modo "sem dublagem".

    Use o anullsrc do FFmpeg para gerar silêncio diretamente, o que requer menos etapas do que construir primeiro um WAV temporário e depois transcodificá-lo.
    arquivo. Retorna False em caso de falha, permitindo que a camada superior processe e registre logs de acordo com o caminho normal de falha do TTS."""
    ensure_file_path_exists(output_file)
    duration_seconds = max(float(duration_seconds or 0), 0.1)
    ffmpeg_binary = utils.get_ffmpeg_binary()
    command = [
        ffmpeg_binary,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-t",
        f"{duration_seconds:.3f}",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "4",
        output_file,
    ]

    logger.info(
        f"generating silent audio for no-voice mode, duration: {duration_seconds:.2f}s"
    )
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.error(
            "failed to generate silent audio: "
            f"{(result.stderr or result.stdout or '').strip()}"
        )
        return False
    if not os.path.exists(output_file) or os.path.getsize(output_file) <= 0:
        logger.error(
            "silent audio output file is missing or empty, "
            f"file: {output_file}, duration: {duration_seconds:.2f}s"
        )
        return False
    return True


def tts(
    text: str,
    voice_name: str,
    voice_rate: float,
    voice_file: str,
    voice_volume: float = 1.0,
) -> Union[SubMaker, None]:
    if is_no_voice(voice_name):
        duration_seconds = estimate_no_voice_duration(text)
        if not generate_silent_audio(duration_seconds, voice_file):
            return None

        sub_maker = ensure_legacy_submaker_fields(SubMaker())
        return populate_legacy_submaker_with_full_text(
            sub_maker=sub_maker,
            text=text,
            audio_duration_seconds=duration_seconds,
        )

    if is_azure_v2_voice(voice_name):
        return azure_tts_v2(
            text,
            voice_name,
            voice_file,
            voice_rate=voice_rate,
        )
    elif is_siliconflow_voice(voice_name):
        # Extraia modelo e voz de voice_name
        # Formato: Siliconflow:modelo:voz-gênero
        parts = voice_name.split(":")
        if len(parts) >= 3:
            model = parts[1]
            # Remova o sufixo de gênero, como "alex-Male" -> "alex"
            voice_with_gender = parts[2]
            voice = voice_with_gender.split("-")[0]
            # Construa os parâmetros de voz completos no formato "model:voice"
            full_voice = f"{model}:{voice}"
            return siliconflow_tts(
                text, model, full_voice, voice_rate, voice_file, voice_volume
            )
        else:
            logger.error(f"Invalid siliconflow voice name format: {voice_name}")
            return None
    elif is_gemini_voice(voice_name):
        # Extraia o nome da voz de voice_name
        # Formato: gemini:voz-gênero
        parts = voice_name.split(":")
        if len(parts) >= 2:
            # Remova o sufixo de gênero, por ex. "Zéfiro-Fêmea" -> "Zéfiro"
            voice_with_gender = parts[1]
            voice = voice_with_gender.split("-")[0]
            return gemini_tts(text, voice, voice_rate, voice_file, voice_volume)
        else:
            logger.error(f"Invalid gemini voice name format: {voice_name}")
            return None
    elif is_mimo_voice(voice_name):
        # Extraia o nome da voz de voice_name
        # Formato: mimo:voz-Gênero; se o chamador executou parse_voice_name,
        # então pode ser mimo:voz. Ambos os formatos são compatíveis.
        parts = voice_name.split(":")
        if len(parts) >= 2:
            voice_with_gender = parts[1]
            voice = voice_with_gender.split("-")[0]
            return mimo_tts(text, voice, voice_rate, voice_file, voice_volume)
        else:
            logger.error(f"Invalid mimo voice name format: {voice_name}")
            return None
    elif is_elevenlabs_voice(voice_name):
        # Formato: onzelabs:{voice_id}:{nome}
        parts = voice_name.split(":")
        if len(parts) >= 2:
            voice_id = parts[1]
            return elevenlabs_tts(text, voice_id, voice_file, voice_rate, voice_volume)
        else:
            logger.error(f"Invalid elevenlabs voice name format: {voice_name}")
            return None
    elif is_chatterbox_voice(voice_name):
        # Formato: chatterbox:<voz>, a voz pode ter o sufixo -Female/-Male para exibição
        parts = voice_name.split(":", 1)
        if len(parts) >= 2 and parts[1].strip():
            chatterbox_voice = parts[1].strip()
            if chatterbox_voice.endswith(("-Female", "-Male")):
                chatterbox_voice = chatterbox_voice.rsplit("-", 1)[0]
            return chatterbox_tts(
                text, chatterbox_voice, voice_file, voice_rate, voice_volume
            )
        else:
            logger.error(f"Invalid chatterbox voice name format: {voice_name}")
            return None
    return azure_tts_v1(text, voice_name, voice_rate, voice_file)


def convert_rate_to_percent(rate: float) -> str:
    # edge-tts requires a sign-prefixed percentage (e.g. "+0%", "-20%").
    # Rounding can yield 0 for rates near but not equal to 1.0 (e.g. 1.004,
    # 0.997); those must still be returned as "+0%", not the unsigned "0%"
    # which edge-tts rejects with ValueError: Invalid rate '0%'.
    # Chamadas de API ou em lote podem passar em 0, 0,0, Nenhum ou um valor nulo não conversível; esses valores não representam
    # A velocidade de fala legal, calculada diretamente, será de -100% ou uma exceção será lançada. Aqui, voltamos à velocidade normal de fala.
    # Evite gerar áudio extremamente lento ou fazer com que o processo TTS falhe nas entradas de limite.
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        rate = 1.0
    if rate <= 0:
        rate = 1.0
    percent = round((rate - 1.0) * 100)
    if percent >= 0:
        return f"+{percent}%"
    return f"{percent}%"


def ensure_file_path_exists(file_path: str) -> None:
    """Certifique-se de que o diretório onde o arquivo de saída está localizado exista.

    Aqui está uma camada separada de detalhes, porque o edge_tts 7.x antes de realmente iniciar uma solicitação de rede,
    O arquivo de áudio de destino será aberto primeiro; se o diretório não existir, um erro será relatado diretamente devido ao caminho do arquivo local.
    mascarando assim os verdadeiros resultados comportamentais do TTS."""
    dir_path = os.path.dirname(file_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)


def ensure_legacy_submaker_fields(sub_maker: SubMaker) -> SubMaker:
    """Preencha o campo de compatibilidade para chamadores que ainda utilizam a antiga estrutura de legendas no projeto.

    `SubMaker` do edge_tts 7.x expõe principalmente `cues/get_srt()`, mas no projeto Azure v2,
    Os caminhos Gemini e SiliconFlow ainda lerão e escreverão `subs/offset` diretamente. Complete aqui,
    Evite que esses caminhos não-edge sejam danificados após atualizar edge_tts."""
    if not hasattr(sub_maker, "subs"):
        sub_maker.subs = []
    if not hasattr(sub_maker, "offset"):
        sub_maker.offset = []
    return sub_maker


def populate_legacy_submaker_with_full_text(
    sub_maker: SubMaker, text: str, audio_duration_seconds: float
) -> SubMaker:
    """Preenche a estrutura de legendas `subs/offset` herdada do histórico do projeto com o texto inteiro.

    Antecedentes:
    1. `SubMaker` do edge_tts 7.x não fornece mais `create_sub()` na versão antiga;
    2. Caminhos sem borda, como Gemini e SiliconFlow no projeto, ainda precisam retornar um
       Objeto com `subs/offset` para posterior cálculo unificado da duração do áudio e geração de legendas;
    3. Para serviços TTS que não conseguem obter limites palavra por palavra, eles precisam pelo menos segmentar as frases em vários segmentos de acordo com o script.
       Desta forma, a lógica de agregação subsequente de `subtitle_provider=edge` pode continuar a funcionar em vez de
       Use o Whisper porque o texto inteiro não pode corresponder aos segmentos do script linha por linha.

    Argumentos:
        sub_maker: Objeto de legenda que precisa escrever campos compatíveis
        texto: texto do roteiro original
        audio_duration_seconds: duração total do áudio, em segundos

    Retorna:
        Objeto SubMaker preenchido com dados de legenda compatíveis"""
    sub_maker = ensure_legacy_submaker_fields(sub_maker)

    # Limpe os valores antigos para evitar a sobreposição de dados sujos quando o chamador reutiliza objetos.
    sub_maker.subs = []
    sub_maker.offset = []

    normalized_text = (text or "").strip()
    if not normalized_text:
        return sub_maker

    audio_duration_100ns = max(int(audio_duration_seconds * 10000000), 1)

    # Quando caminhos como Gemini/SiliconFlow não conseguem obter limites palavra por palavra, tente ainda usar o projeto
    # A estratégia original de “quebrar frases de acordo com pontuação + alocar duração de acordo com o número de caracteres”. Isto permitirá
    # create_subtitle() corresponde aos segmentos do script e evita reverter para o Whisper novamente.
    sentences = utils.split_string_by_punctuations(normalized_text)
    if not sentences:
        sentences = [normalized_text]

    total_chars = sum(len(sentence) for sentence in sentences)
    if total_chars <= 0:
        sub_maker.subs.append(normalized_text)
        sub_maker.offset.append((0, audio_duration_100ns))
        return sub_maker

    current_offset = 0
    for index, sentence in enumerate(sentences):
        cleaned_sentence = sentence.strip()
        if not cleaned_sentence:
            continue

        # A duração das frases anteriores é alocada proporcionalmente ao número de caracteres, e a última frase consome a duração restante.
        # Evite arredondamentos de números inteiros, fazendo com que a duração total seja perdida ou que o tempo de término da legenda seja menor que o do áudio.
        if index == len(sentences) - 1:
            sentence_end = audio_duration_100ns
        else:
            sentence_chars = len(cleaned_sentence)
            sentence_duration = max(
                int(audio_duration_100ns * (sentence_chars / total_chars)),
                1,
            )
            sentence_end = min(current_offset + sentence_duration, audio_duration_100ns)

        sub_maker.subs.append(cleaned_sentence)
        sub_maker.offset.append((current_offset, sentence_end))
        current_offset = sentence_end

    return sub_maker


def create_edge_tts_communicate(
    text: str, voice_name: str, rate_str: str
) -> edge_tts.Communicate:
    """Constrói um objeto Communicate com base na versão atualmente instalada do edge_tts.

    Antecedentes:
    1. O código da linha principal foi atualizado para edge_tts 7.x e usa o parâmetro `boundary` para obter eventos de limite mais precisos;
    2. No entanto, se o pacote portátil do Windows não for atualizado, o ambiente local ainda poderá estar preso na versão antiga do edge_tts;
    3. A versão antiga de `Communicate.__init__()` não aceita `boundary` e irá lançá-lo diretamente
       `argumento de palavra-chave inesperado 'limite'`, fazendo com que todo o link TTS falhe.

    Portanto, aqui primeiro detectamos os parâmetros suportados pela versão atual com base na assinatura do construtor e, em seguida, decidimos se devemos transmiti-los.
    `boundary` torna o mesmo código compatível com versões antigas e novas de dependências."""
    communicate_kwargs = {"rate": rate_str}
    communicate_signature = inspect.signature(edge_tts.Communicate)

    if "boundary" in communicate_signature.parameters:
        communicate_kwargs["boundary"] = "WordBoundary"

    return edge_tts.Communicate(text, voice_name, **communicate_kwargs)


def get_edge_tts_timeout_seconds() -> Union[float, None]:
    """Obtém o tempo limite para uma única solicitação de streaming no Azure TTS V1.

    Antecedentes:
    O TTS do consumidor de borda funciona em cenários como falha de rede, limitação de corrente no servidor, incompatibilidade de idioma de voz e texto, etc.
    Ele pode ficar preso dentro de `stream_sync()` por um longo tempo, e o log só permanece em `start`. Aqui está um
    O tempo limite padrão evita que as tarefas WebUI não recebam feedback por um longo período.

    Como usar:
    - O padrão é 30 segundos, cobrindo o tempo de espera do primeiro pacote de scripts de vídeo curtos comuns;
    - Se o usuário estiver em uma rede lenta ou ambiente proxy, pode ser definido em `config.toml`
      `edge_tts_timeout = 60`;
    - Defina como 0 ou um número negativo para desativar explicitamente os tempos limite, preservando a compatibilidade total com versões anteriores."""
    raw_timeout = config.app.get(
        "edge_tts_timeout", _DEFAULT_EDGE_TTS_TIMEOUT_SECONDS
    )
    try:
        timeout_seconds = float(raw_timeout)
    except (TypeError, ValueError):
        logger.warning(
            "invalid edge_tts_timeout: "
            f"{raw_timeout}, fallback to {_DEFAULT_EDGE_TTS_TIMEOUT_SECONDS}s"
        )
        timeout_seconds = _DEFAULT_EDGE_TTS_TIMEOUT_SECONDS

    if timeout_seconds <= 0:
        return None

    return timeout_seconds


def _stream_edge_tts_sync_with_timeout(
    communicate, on_chunk, timeout_seconds: float
) -> None:
    """Consumir o fluxo síncrono do edge_tts 7.x com tempo limite total.

    Motivo da implementação:
    O próprio `stream_sync()` é um iterador de bloqueio e o thread principal não pode se recuperar a tempo quando a camada de rede está travada.
    Aqui, a iteração de bloqueio é colocada no thread daemon e o thread principal obtém o pedaço por meio da Fila.
    Lance TimeoutError diretamente após atingir o período de tempo limite, permitindo que a nova tentativa externa e o log de erros continuem funcionando.

    Nota:
    O thread daemon é usado apenas para proteção de cobertura e é gerado com até três tentativas do Azure TTS V1.
    Um pequeno número de fios residuais; eles serão reciclados automaticamente quando o processo terminar. Em comparação com as tarefas da WebUI que ficam travadas permanentemente, isso é
    Modos de falha mais controláveis."""
    stream_queue = queue.Queue()
    done_marker = object()

    def _produce_chunks():
        try:
            for chunk in communicate.stream_sync():
                stream_queue.put(("chunk", chunk))
            stream_queue.put(("done", done_marker))
        except Exception as e:
            stream_queue.put(("error", e))

    thread = threading.Thread(target=_produce_chunks, daemon=True)
    thread.start()

    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise TimeoutError(
                f"edge_tts stream timed out after {timeout_seconds:g}s"
            )

        try:
            item_type, payload = stream_queue.get(
                timeout=min(0.5, remaining_seconds)
            )
        except queue.Empty:
            continue

        if item_type == "chunk":
            on_chunk(payload)
        elif item_type == "error":
            raise payload
        elif item_type == "done":
            return


def stream_edge_tts_chunks(
    communicate, on_chunk, timeout_seconds: Union[float, None] = None
) -> None:
    """Consumir o fluxo síncrono e o fluxo assíncrono legado de edge_tts de maneira unificada.

    edge_tts 7.x fornece `stream_sync()`, que pode ser iterado diretamente na função de sincronização;
    Versões mais antigas geralmente tinham apenas `stream()` assíncrono. Para que `azure_tts_v1()`
    Ele ainda pode continuar funcionando em cenários onde dependências antigas permanecem e uma camada de compatibilidade de streaming é unificada aqui.

    Argumentos:
        comunicar: instância edge_tts.Communicate
        on_chunk: retorno de chamada executado toda vez que um pedaço de evento é obtido
        timeout_seconds: Tempo limite total para uma única solicitação de streaming; o tempo limite não está habilitado quando Nenhum."""
    if hasattr(communicate, "stream_sync"):
        if timeout_seconds:
            _stream_edge_tts_sync_with_timeout(
                communicate, on_chunk, timeout_seconds
            )
            return

        for chunk in communicate.stream_sync():
            on_chunk(chunk)
        return

    if not hasattr(communicate, "stream"):
        raise AttributeError("edge_tts communicate object has no stream method")

    async def _consume_async_stream():
        async for chunk in communicate.stream():
            on_chunk(chunk)

    # Aqui criamos explicitamente um loop de eventos independente em vez de reutilizar o contexto externo. O objetivo é evitar
    # Na pilha de chamadas de sincronização, encontrei o problema de "o thread atual não possui um loop de eventos" ou o problema de reutilização de loops entre threads.
    loop = asyncio.new_event_loop()
    try:
        if timeout_seconds:
            loop.run_until_complete(
                asyncio.wait_for(_consume_async_stream(), timeout=timeout_seconds)
            )
        else:
            loop.run_until_complete(_consume_async_stream())
    finally:
        loop.close()


def azure_tts_v1(
    text: str, voice_name: str, voice_rate: float, voice_file: str
) -> Union[SubMaker, None]:
    voice_name = parse_voice_name(voice_name)
    text = text.strip()
    rate_str = convert_rate_to_percent(voice_rate)
    for i in range(3):
        try:
            logger.info(f"start, voice name: {voice_name}, try: {i + 1}")

            # Isso é compatível com edge_tts 7.xe com as dependências antigas que podem permanecer na versão antiga do pacote portátil:
            # 1. A nova versão suporta `boundary` + `stream_sync()`
            # 2. A versão antiga não suporta `boundary` e geralmente expõe apenas `stream()` assíncrono
            ensure_file_path_exists(voice_file)
            communicate = create_edge_tts_communicate(text, voice_name, rate_str)
            sub_maker = edge_tts.SubMaker()
            timeout_seconds = get_edge_tts_timeout_seconds()

            with open(voice_file, "wb") as file:
                def _handle_chunk(chunk):
                    chunk_type = chunk["type"]
                    if chunk_type == "audio":
                        file.write(chunk["data"])
                    elif chunk_type in ["WordBoundary", "SentenceBoundary"]:
                        # Seja um stream síncrono da versão 7.x ou um stream assíncrono legado, desde que a estrutura do evento
                        # Se ainda houver informações de limite nele, elas serão alimentadas uniformemente no SubMaker para garantir links de legendas subsequentes.
                        # Ainda siga a lógica existente do projeto.
                        sub_maker.feed(chunk)

                stream_edge_tts_chunks(
                    communicate, _handle_chunk, timeout_seconds=timeout_seconds
                )

            if not sub_maker.get_srt():
                logger.warning("failed, sub_maker.get_srt() is empty")
                continue

            logger.info(f"completed, output file: {voice_file}")
            return sub_maker
        except Exception as e:
            logger.error(f"failed, error: {str(e)}")
            # Se a gravação do streaming TTS expirar ou a rede estiver anormal antes do primeiro pacote, os arquivos de áudio de 0 bytes serão deixados.
            # Esses arquivos não são reproduzíveis nem podem enganar a solução de problemas subsequente, portanto, apenas os arquivos vazios são apagados após a falha;
            # Caso parte dos dados tenha sido gravada, o arquivo local é retido para facilitar a análise do conteúdo retornado pelo servidor.
            if os.path.exists(voice_file) and os.path.getsize(voice_file) == 0:
                try:
                    os.remove(voice_file)
                except Exception as remove_error:
                    logger.warning(
                        "failed to remove empty tts file: "
                        f"{voice_file}, error: {str(remove_error)}"
                    )
    return None


def siliconflow_tts(
    text: str,
    model: str,
    voice: str,
    voice_rate: float,
    voice_file: str,
    voice_volume: float = 1.0,
) -> Union[SubMaker, None]:
    """Gere fala usando Silicon Fluid API

    Argumentos:
        texto: texto a ser convertido em fala
        modelo: nome do modelo, como "FunAudioLLM/CosyVoice2-0.5B"
        voz: nome da voz, como "FunAudioLLM/CosyVoice2-0.5B:alex"
        voice_rate: velocidade da voz, alcance [0,25, 4,0]
        voice_file: caminho do arquivo de áudio de saída
        voice_volume: volume de voz, faixa [0,6, 5,0], precisa ser convertido para faixa de ganho de fluxo de silício [-10, 10]

    Retorna:
        Objeto SubMaker ou Nenhum"""
    text = text.strip()
    api_key = config.siliconflow.get("api_key", "")

    if not api_key:
        logger.error("SiliconFlow API key is not set")
        return None

    # Converta voice_volume para obter alcance para fluxo de silício
    # O voice_volume padrão é 1,0 e o ganho correspondente é 0
    gain = voice_volume - 1.0
    # Certifique-se de que o ganho esteja na faixa [-10, 10]
    gain = max(-10, min(10, gain))

    url = "https://api.siliconflow.cn/v1/audio/speech"

    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "mp3",
        "sample_rate": 32000,
        "stream": False,
        "speed": voice_rate,
        "gain": gain,
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    for i in range(3):  # 尝试3次
        try:
            logger.info(
                f"start siliconflow tts, model: {model}, voice: {voice}, try: {i + 1}"
            )

            response = requests.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                # Salvar arquivo de áudio
                with open(voice_file, "wb") as f:
                    f.write(response.content)

                # A estrutura de legenda original do projeto ainda é usada aqui, portanto os campos antigos precisam ser preenchidos.
                sub_maker = ensure_legacy_submaker_fields(SubMaker())

                # Obtenha a duração real do arquivo de áudio
                try:
                    # Tente usar o moviepy para obter a duração do áudio
                    from moviepy import AudioFileClip

                    audio_clip = AudioFileClip(voice_file)
                    audio_duration = audio_clip.duration
                    audio_clip.close()

                    # Converta a duração do áudio em unidades de 100 nanossegundos (compatível com edge_tts)
                    audio_duration_100ns = int(audio_duration * 10000000)

                    # Use segmentação de texto para criar legendas mais precisas
                    # Divida o texto em frases com base na pontuação
                    sentences = utils.split_string_by_punctuations(text)

                    if sentences:
                        # Calcule o comprimento aproximado de cada frase (proporcional ao número de caracteres)
                        total_chars = sum(len(s) for s in sentences)
                        char_duration = (
                            audio_duration_100ns / total_chars if total_chars > 0 else 0
                        )

                        current_offset = 0
                        for sentence in sentences:
                            if not sentence.strip():
                                continue

                            # Calcule a duração da frase atual
                            sentence_chars = len(sentence)
                            sentence_duration = int(sentence_chars * char_duration)

                            # Adicionar ao SubMaker
                            sub_maker.subs.append(sentence)
                            sub_maker.offset.append(
                                (current_offset, current_offset + sentence_duration)
                            )

                            # Atualizar deslocamento
                            current_offset += sentence_duration
                    else:
                        # Se a divisão não for possível, use o texto inteiro como um subtítulo
                        sub_maker.subs = [text]
                        sub_maker.offset = [(0, audio_duration_100ns)]

                except Exception as e:
                    logger.warning(f"Failed to create accurate subtitles: {str(e)}")
                    # Fallback para legendas simples
                    sub_maker.subs = [text]
                    # Use a duração real do arquivo de áudio, se não estiver disponível, considere 10 segundos
                    sub_maker.offset = [
                        (
                            0,
                            audio_duration_100ns
                            if "audio_duration_100ns" in locals()
                            else 10000000,
                        )
                    ]

                logger.success(f"siliconflow tts succeeded: {voice_file}")
                logger.debug(
                    "siliconflow subtitle timeline generated, "
                    f"subs: {len(sub_maker.subs)}, offsets: {len(sub_maker.offset)}"
                )
                return sub_maker
            else:
                logger.error(
                    f"siliconflow tts failed with status code {response.status_code}: {response.text}"
                )
        except Exception as e:
            logger.error(f"siliconflow tts failed: {str(e)}")

    return None


def _build_azure_v2_ssml(text: str, voice_name: str, voice_rate: float) -> str:
    """Construa SSML para uso com o Azure Speech V2 e normalize com segurança os parâmetros de velocidade de fala."""
    try:
        normalized_rate = float(voice_rate)
    except (TypeError, ValueError):
        normalized_rate = 1.0
    normalized_rate = max(0.25, min(4.0, normalized_rate))

    voice_locale_parts = voice_name.split("-", 2)
    voice_locale = (
        "-".join(voice_locale_parts[:2])
        if len(voice_locale_parts) >= 2
        else "en-US"
    )
    escaped_text = escape(text)
    escaped_voice_name = escape(voice_name, {'"': "&quot;"})
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{voice_locale}">'
        f'<voice name="{escaped_voice_name}">'
        f'<prosody rate="{normalized_rate:g}">{escaped_text}</prosody>'
        "</voice></speak>"
    )


def azure_tts_v2(
    text: str,
    voice_name: str,
    voice_file: str,
    voice_rate: float = 1.0,
) -> Union[SubMaker, None]:
    voice_name = is_azure_v2_voice(voice_name)
    if not voice_name:
        logger.error(f"invalid voice name: {voice_name}")
        raise ValueError(f"invalid voice name: {voice_name}")
    text = text.strip()
    ssml = _build_azure_v2_ssml(text, voice_name, voice_rate)

    def _format_duration_to_offset(duration) -> int:
        if isinstance(duration, str):
            time_obj = datetime.strptime(duration, "%H:%M:%S.%f")
            milliseconds = (
                (time_obj.hour * 3600000)
                + (time_obj.minute * 60000)
                + (time_obj.second * 1000)
                + (time_obj.microsecond // 1000)
            )
            return milliseconds * 10000

        if isinstance(duration, int):
            return duration

        return 0

    for i in range(3):
        try:
            logger.info(
                f"start, voice name: {voice_name}, rate: {voice_rate}, try: {i + 1}"
            )

            import azure.cognitiveservices.speech as speechsdk

            sub_maker = ensure_legacy_submaker_fields(SubMaker())

            def speech_synthesizer_word_boundary_cb(evt: speechsdk.SessionEventArgs):
                # print('WordBoundary event:')
                # print('\tBoundaryType: {}'.format(evt.boundary_type))
                # print('\tAudioOffset: {}ms'.format((evt.audio_offset + 5000)))
                # print('\tDuration: {}'.format(evt.duration))
                # print('\tText: {}'.format(evt.text))
                # print('\tTextOffset: {}'.format(evt.text_offset))
                # print('\tWordLength: {}'.format(evt.word_length))

                duration = _format_duration_to_offset(str(evt.duration))
                offset = _format_duration_to_offset(evt.audio_offset)
                sub_maker.subs.append(evt.text)
                sub_maker.offset.append((offset, offset + duration))

            # Creates an instance of a speech config with specified subscription key and service region.
            speech_key = config.azure.get("speech_key", "")
            service_region = config.azure.get("speech_region", "")
            if not speech_key or not service_region:
                logger.error("Azure speech key or region is not set")
                return None

            audio_config = speechsdk.audio.AudioOutputConfig(
                filename=voice_file, use_default_speaker=True
            )
            speech_config = speechsdk.SpeechConfig(
                subscription=speech_key, region=service_region
            )
            speech_config.speech_synthesis_voice_name = voice_name
            # speech_config.set_property(property_id=speechsdk.PropertyId.SpeechServiceResponse_RequestSentenceBoundary,
            #                            value='true')
            speech_config.set_property(
                property_id=speechsdk.PropertyId.SpeechServiceResponse_RequestWordBoundary,
                value="true",
            )

            speech_config.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Audio48Khz192KBitRateMonoMp3
            )
            speech_synthesizer = speechsdk.SpeechSynthesizer(
                audio_config=audio_config, speech_config=speech_config
            )
            speech_synthesizer.synthesis_word_boundary.connect(
                speech_synthesizer_word_boundary_cb
            )

            # speak_text_async() não suporta o parâmetro de velocidade de fala. Depois de usar a prosódia SSML, audição e
            # A geração formal ajustará a velocidade da fala de acordo com o voice_rate transmitido pela WebUI/API.
            result = speech_synthesizer.speak_ssml_async(ssml).get()
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                logger.success(f"azure v2 speech synthesis succeeded: {voice_file}")
                return sub_maker
            elif result.reason == speechsdk.ResultReason.Canceled:
                cancellation_details = result.cancellation_details
                logger.error(
                    f"azure v2 speech synthesis canceled: {cancellation_details.reason}"
                )
                if cancellation_details.reason == speechsdk.CancellationReason.Error:
                    logger.error(
                        f"azure v2 speech synthesis error: {cancellation_details.error_details}"
                    )
            logger.info(f"completed, output file: {voice_file}")
        except Exception as e:
            logger.error(f"failed, error: {str(e)}")
    return None


def gemini_tts(
    text: str,
    voice_name: str,
    voice_rate: float,
    voice_file: str,
    voice_volume: float = 1.0,
) -> Union[SubMaker, None]:
    """Gere fala usando o Google Gemini TTS
    
    Argumentos:
        texto: o texto a ser convertido
        voice_name: nome da voz, como "Zephyr", "Puck", etc.
        voice_rate: Taxa de voz (não usada atualmente)
        voice_file: caminho do arquivo de áudio de saída
        voice_volume: volume do áudio (atualmente não usado)
        
    Retorna:
        Objeto SubMaker ou Nenhum"""
    import base64
    import io
    from pydub import AudioSegment
    from google import genai
    from google.genai import types
    _configure_pydub_ffmpeg(AudioSegment)
    
    try:
        api_key = config.app.get("gemini_api_key", "")
        if not api_key:
            logger.error("Gemini API key is not set")
            return None

        logger.info(f"start, voice name: {voice_name}, try: 1")

        generation_config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        )

        # google-genai usa o cliente unificado para chamar modelos de texto e TTS. O gerenciador de contexto garante
        # Libere a conexão HTTP após a conclusão da solicitação, mantendo a transcodificação PCM original e a lógica da linha do tempo das legendas.
        with genai.Client(api_key=api_key) as client:
            response = client.models.generate_content(
                model="gemini-2.5-flash-preview-tts",
                contents=text,
                config=generation_config,
            )

        # Verifique a resposta
        if not response.candidates or not response.candidates[0].content:
            logger.error("No audio content received from Gemini TTS")
            return None
            
        # Obtenha dados de áudio
        audio_data = None
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'inline_data') and part.inline_data:
                audio_data = part.inline_data.data
                break
                
        if not audio_data:
            logger.error("No audio data found in response")
            return None
            
        # Os dados de áudio já são bytes brutos e não requerem decodificação base64
        if isinstance(audio_data, str):
            # Se for uma string, é necessária a decodificação base64
            audio_bytes = base64.b64decode(audio_data)
        else:
            # Se já for um byte, use-o diretamente
            audio_bytes = audio_data
        
        # Experimente diferentes formatos de áudio - Gemini pode retornar formatos diferentes
        audio_segment = None
        
        # Gemini retorna o formato Linear PCM, analisado de acordo com os parâmetros do documento
        try:
            audio_segment = AudioSegment.from_file(
                io.BytesIO(audio_bytes), 
                format="raw",
                frame_rate=24000,  # Gemini TTS默认采样率
                channels=1,        # 单声道
                sample_width=2     # 16-bit
            )
        except Exception as e:
            logger.error(f"Failed to load PCM audio: {e}")
            return None
        
        # APIs, CLIs ou testes podem direcionar diretamente diretórios aninhados que ainda não existem como locais de saída. aqui em
        # Crie o diretório pai antes de realmente gravar o arquivo para evitar que uma solicitação Gemini bem-sucedida termine com
        # Perder resultados quando o caminho local não existe também faz com que este provedor se comporte de forma consistente com outras implementações de TTS.
        ensure_file_path_exists(voice_file)

        # pydub retorna o objeto de arquivo de saída aberto. Se não for fechado ativamente durante a geração do lote, o descritor de arquivo
        # Ele continua a se acumular e aumenta a chance de falha subsequente ao substituir ou excluir arquivos de áudio no Windows.
        exported_audio = audio_segment.export(voice_file, format="mp3")
        exported_audio.close()
        
        logger.info(f"completed, output file: {voice_file}")
        
        # Gêmeos não consegue obter eventos de limite palavra por palavra como edge_tts, então voltamos para
        # A estrutura original de compatibilidade `subs/offset` do projeto garante pelo menos legendas e duração subsequentes.
        # O link de computação pode continuar funcionando.
        sub_maker = ensure_legacy_submaker_fields(SubMaker())
        audio_duration = len(audio_segment) / 1000.0  # 转换为秒
        return populate_legacy_submaker_with_full_text(
            sub_maker=sub_maker,
            text=text,
            audio_duration_seconds=audio_duration,
        )
        
    except ImportError as e:
        logger.error(f"Missing required package for Gemini TTS: {str(e)}. Please install: pip install pydub")
        return None
    except Exception as e:
        logger.error(f"Gemini TTS failed, error: {str(e)}")
        return None


def mimo_tts(
    text: str,
    voice_name: str,
    voice_rate: float,
    voice_file: str,
    voice_volume: float = 1.0,
) -> Union[SubMaker, None]:
    """Geração de fala usando Xiaomi MiMo V2.5 TTS.

    A interface oficial é compatível com OpenAI Chat Completions, mas o TTS tem duas diferenças principais:
    1. O texto a ser sintetizado deverá ser colocado na mensagem do `assistente`;
    2. O áudio é retornado como uma string base64 em `message.audio.data`.

    Atualmente, o MiMo não retorna uma linha do tempo palavra por palavra, portanto, o legado existente do projeto é reutilizado aqui.
    Solução do SubMaker: gerar uma linha do tempo de legenda com base na duração final do áudio e nos segmentos de texto do script."""
    from pydub import AudioSegment

    text = (text or "").strip()
    if not text:
        logger.error("MiMo TTS text is empty")
        return None

    api_key = config.app.get("mimo_api_key", "")
    if not api_key:
        logger.error("MiMo API key is not set")
        return None

    base_url = config.app.get("mimo_base_url", "") or _MIMO_DEFAULT_BASE_URL
    model_name = config.app.get("mimo_tts_model_name", "") or _MIMO_DEFAULT_TTS_MODEL
    style_prompt = config.app.get(
        "mimo_tts_style_prompt",
        "请用自然、清晰、适合短视频旁白的语气朗读。",
    )

    _configure_pydub_ffmpeg(AudioSegment)

    for i in range(3):
        try:
            logger.info(
                f"start mimo tts, model: {model_name}, voice: {voice_name}, try: {i + 1}"
            )
            ensure_file_path_exists(voice_file)

            client = OpenAI(api_key=api_key, base_url=base_url)
            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "user", "content": style_prompt},
                    {"role": "assistant", "content": text},
                ],
                audio={
                    "format": "wav",
                    "voice": voice_name,
                },
            )

            if not completion or not getattr(completion, "choices", None):
                raise ValueError("MiMo TTS returned empty response")

            message = completion.choices[0].message
            audio = getattr(message, "audio", None)
            audio_data = None
            if isinstance(audio, dict):
                audio_data = audio.get("data")
            elif audio is not None:
                audio_data = getattr(audio, "data", None)

            if not audio_data:
                raise ValueError("MiMo TTS returned empty audio data")

            audio_bytes = base64.b64decode(audio_data)
            audio_segment = AudioSegment.from_file(io.BytesIO(audio_bytes), format="wav")

            output_format = utils.parse_extension(voice_file) or "mp3"
            if output_format == "wav":
                with open(voice_file, "wb") as f:
                    f.write(audio_bytes)
            else:
                audio_segment.export(voice_file, format=output_format)

            audio_duration = len(audio_segment) / 1000.0
            sub_maker = ensure_legacy_submaker_fields(SubMaker())
            logger.success(f"mimo tts succeeded: {voice_file}")
            logger.debug(
                "mimo subtitle timeline generated, "
                f"duration: {audio_duration:.3f}s, output_format: {output_format}"
            )
            return populate_legacy_submaker_with_full_text(
                sub_maker=sub_maker,
                text=text,
                audio_duration_seconds=audio_duration,
            )
        except Exception as e:
            logger.error(f"mimo tts failed: {str(e)}")

    return None


def elevenlabs_tts(
    text: str,
    voice_id: str,
    voice_file: str,
    voice_rate: float = 1.0,
    voice_volume: float = 1.0,
    model_id: str = "",
) -> Union[SubMaker, None]:
    text = (text or "").strip()
    if not text:
        logger.error("ElevenLabs TTS text is empty")
        return None

    api_key = config.elevenlabs.get("api_key", "")
    if not api_key:
        logger.error("ElevenLabs API key is not set")
        return None

    if not model_id:
        model_id = config.elevenlabs.get("model_id", "eleven_multilingual_v2")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }

    # Errors where retrying will never help (auth/access/validation failures).
    _NON_RETRYABLE_CODES = {401, 403, 422}
    _NON_RETRYABLE_STATUSES = {"voice_disabled", "voice_access_denied", "unauthorized"}

    for i in range(3):
        try:
            logger.info(f"start elevenlabs tts, voice_id: {voice_id}, try: {i + 1}")
            ensure_file_path_exists(voice_file)

            response = requests.post(url, json=payload, headers=headers, timeout=60)
            if response.status_code != 200:
                error_status = ""
                try:
                    detail = response.json().get("detail", {})
                    if isinstance(detail, dict):
                        error_status = detail.get("status", "")
                except Exception:
                    pass

                if response.status_code in _NON_RETRYABLE_CODES or error_status in _NON_RETRYABLE_STATUSES:
                    logger.error(
                        f"ElevenLabs TTS failed (non-retryable) — voice_id: {voice_id}, "
                        f"status: {response.status_code}, error: {error_status or response.text[:200]}. "
                        "Please select a different ElevenLabs voice."
                    )
                    return None

                logger.error(
                    f"elevenlabs tts failed with status {response.status_code}: {response.text[:200]}"
                )
                continue

            with open(voice_file, "wb") as f:
                f.write(response.content)

            audio_clip = AudioFileClip(voice_file)
            audio_duration = audio_clip.duration
            audio_clip.close()

            sub_maker = ensure_legacy_submaker_fields(SubMaker())
            logger.success(f"elevenlabs tts succeeded: {voice_file}")
            return populate_legacy_submaker_with_full_text(
                sub_maker=sub_maker,
                text=text,
                audio_duration_seconds=audio_duration,
            )
        except Exception as e:
            logger.error(f"elevenlabs tts failed: {str(e)}")

    return None


def chatterbox_tts(
    text: str,
    voice: str,
    voice_file: str,
    voice_rate: float = 1.0,
    voice_volume: float = 1.0,
    model_id: str = "",
) -> Union[SubMaker, None]:
    """Generate speech with a self-hosted Chatterbox TTS server.

    Chatterbox (Resemble AI, MIT) is an open-source, locally hosted TTS model
    with zero-shot voice cloning — a self-hostable alternative to ElevenLabs.
    This talks to an OpenAI-compatible ``/audio/speech`` endpoint, so it works
    with the common community servers (e.g. devnen/Chatterbox-TTS-Server,
    travisvn/chatterbox-tts-api). Configure ``[chatterbox] base_url`` (and an
    optional ``api_key``).

    Like ElevenLabs, Chatterbox does not return word-level timestamps, so the
    subtitle path falls back to the full-text SubMaker. For tighter subtitle
    sync set ``subtitle_provider = "whisper"``.
    """
    text = (text or "").strip()
    if not text:
        logger.error("Chatterbox TTS text is empty")
        return None

    base_url = (config.chatterbox.get("base_url", "") or "").strip().rstrip("/")
    if not base_url:
        logger.error(
            "Chatterbox base_url is not set, please configure [chatterbox] base_url in config.toml"
        )
        return None

    api_key = config.chatterbox.get("api_key", "")
    if not model_id:
        model_id = config.chatterbox.get("model_id", "chatterbox") or "chatterbox"

    url = f"{base_url}/audio/speech"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model_id,
        "input": text,
        "voice": voice,
        "response_format": "mp3",
        # OpenAI speech API accepts speed 0.25-4.0; MoneyPrinterTurbo's rate is a
        # 1.0-centred multiplier, so it maps directly (clamped to the valid range).
        "speed": max(0.25, min(4.0, float(voice_rate or 1.0))),
    }
    # voice_volume is accepted for parity with the other TTS providers but is
    # intentionally not sent: the OpenAI /audio/speech contract has no volume
    # field, so Chatterbox servers ignore it. Adjust loudness via voice_rate
    # (speed) or in post-processing instead.

    for i in range(3):
        try:
            logger.info(f"start chatterbox tts, voice: {voice}, try: {i + 1}")
            ensure_file_path_exists(voice_file)

            response = requests.post(url, json=payload, headers=headers, timeout=120)
            if response.status_code != 200:
                logger.error(
                    f"chatterbox tts failed with status {response.status_code}: {response.text[:200]}"
                )
                continue

            with open(voice_file, "wb") as f:
                f.write(response.content)

            audio_clip = AudioFileClip(voice_file)
            audio_duration = audio_clip.duration
            audio_clip.close()

            sub_maker = ensure_legacy_submaker_fields(SubMaker())
            logger.success(f"chatterbox tts succeeded: {voice_file}")
            return populate_legacy_submaker_with_full_text(
                sub_maker=sub_maker,
                text=text,
                audio_duration_seconds=audio_duration,
            )
        except Exception as e:
            logger.error(f"chatterbox tts failed: {str(e)}")

    return None


def _format_text(text: str) -> str:
    """Limpe o texto do script antes do alinhamento das legendas.

    Isso não pode ser tratado apenas durante a fase de geração do LLM, pois o usuário também pode colar o script manualmente ou via
    A API passa diretamente o texto contendo a marcação Markdown. TTS geralmente não lê `---`,
    Linhas delimitadoras como `___` e `***` não serão lidas em voz alta. Marcas de ênfase como `_` não serão lidas em voz alta; se as legendas
    O alinhamento ainda retém esses caracteres, `create_subtitle()` aguardará por uma sugestão que não existe,
    Em última análise, isso resultou na falta de arquivos de legenda e em uma linha do tempo com todos os 0s preenchidos durante a correção de fallback do Whisper."""
    text = text.replace("[", " ")
    text = text.replace("]", " ")
    text = text.replace("(", " ")
    text = text.replace(")", " ")
    text = text.replace("{", " ")
    text = text.replace("}", " ")
    return utils.normalize_script_for_subtitle_matching(text)


def _build_subtitle_formatter():
    """Retorna a função unificada de formatação de linha SRT.

    Isso é separado em uma pequena ferramenta para fazer o caminho das dicas do edge_tts 7.x
    Ele compartilha o mesmo formato de disco de legenda com o caminho legado `subs/offset` original do projeto.
    Evite diferenças sutis de formato entre os dois conjuntos de lógica."""

    def formatter(idx: int, start_time: float, end_time: float, sub_text: str) -> str:
        start_t = mktimestamp(start_time).replace(".", ",")
        end_t = mktimestamp(end_time).replace(".", ",")
        return f"{idx}\n{start_t} --> {end_t}\n{sub_text}\n"

    return formatter


# Diacríticos árabes e alongadores Tatweel podem aparecer no texto de retorno do edge-tts,
# Esses caracteres não afetam a semântica, mas podem causar falhas nas correspondências exatas entre o texto do script e as sequências de sinalização de legenda.
_ARABIC_DIACRITICS = re.compile("[\u0610-\u061A\u064B-\u065F\u0670\u0640\u06D6-\u06ED]")


def _normalize_arabic(text: str) -> str:
    """Unifique variantes comuns de letras árabes e melhore a taxa de tolerância a erros de correspondência de dicas de legenda e linhas de script.

    edge-tts para árabe pode retornar formas de letras diferentes da escrita original, por exemplo, أ/إ/آ
    Normalize para ا ou use diacríticos. Isso é usado apenas na última camada de bolsos correspondentes.
    Não altere o texto da legenda original para evitar afetar o conteúdo final da exibição."""
    text = _ARABIC_DIACRITICS.sub("", text)
    for src, dst in (
        ("أإآٱ", "ا"),
        ("ىئ", "ي"),
        ("ة", "ه"),
        ("ؤ", "و"),
    ):
        for ch in src:
            text = text.replace(ch, dst)
    return text


def _match_script_line(script_lines: list[str], current_text: str, sub_index: int) -> str:
    """Tente combinar o texto da legenda atualmente acumulado com um segmento padrão no script.

    A ideia original do projeto de “dividir o roteiro de acordo com os pontos de pontuação e depois compará-lo seção por seção” é reutilizada aqui:
    1. Dê prioridade à correspondência exata;
    2. Faça a correspondência novamente para remover caracteres de pontuação e formato Markdown `_`;
    3. Finalmente, faça uma correspondência normalizada das formas dos caracteres árabes.

    Isso é compatível com:
    - Pontos de pontuação que podem estar faltando ou divididos separadamente nos retornos TTS;
    - Em cenários chineses, os limites das palavras e o texto do script não correspondem completamente um a um."""
    if len(script_lines) <= sub_index:
        return ""

    target_line = script_lines[sub_index]
    if current_text == target_line:
        return target_line.strip()

    current_text_normalized = re.sub(r"[_\W]+", "", current_text)
    target_line_normalized = re.sub(r"[_\W]+", "", target_line)
    if current_text_normalized == target_line_normalized:
        return target_line.strip()

    # Última camada de tolerância árabe: letras, diacríticos ou Tatweel retornados por edge-tts
    # Pode ser diferente do script. As comparações normalizadas só são executadas depois que a correspondência regular falha, o texto não árabe não é afetado.
    current_ar = re.sub(r"[_\W]+", "", _normalize_arabic(current_text))
    target_ar = re.sub(r"[_\W]+", "", _normalize_arabic(target_line))
    if current_ar and current_ar == target_ar:
        return target_line.strip()

    return ""


def _write_subtitle_items(sub_items: list[str], subtitle_file: str) -> bool:
    """Grave os segmentos de legenda agregados no arquivo SRT e execute uma verificação básica de legibilidade.

    Valor de retorno:
    - `True`: O arquivo de legenda foi baixado com sucesso e pode ser analisado pelo moviepy;
    - `False`: Falha na gravação ou análise do arquivo de legenda."""
    try:
        ensure_file_path_exists(subtitle_file)
        with open(subtitle_file, "w", encoding="utf-8") as file:
            file.write("\n".join(sub_items) + "\n")

        sbs = subtitles.file_to_subtitles(subtitle_file, encoding="utf-8")
        duration = max([tb for ((ta, tb), txt) in sbs]) if sbs else 0
        logger.info(
            f"completed, subtitle file created: {subtitle_file}, duration: {duration}"
        )
        return True
    except Exception as e:
        logger.error(f"failed, error: {str(e)}")
        if os.path.exists(subtitle_file):
            os.remove(subtitle_file)
        return False


def _build_subtitle_items_from_edge_cues(
    sub_maker: SubMaker, script_lines: list[str]
) -> list[str]:
    """Agregue as `sugestões` refinadas do edge_tts 7.x em fragmentos SRT com script.

    Antecedentes:
    O `SubMaker.get_srt()` do edge_tts 7.x prefere uma linha do tempo palavra por palavra/frase por frase.
    Não há problema em destacar o inglês palavra por palavra, mas se as legendas curtas do vídeo em chinês forem copiadas diretamente, elas aparecerão
    “Dinheiro / é / uma / social / ferramenta” Esta é uma experiência de leitura ruim.

    Estratégia de implementação:
    1. Consumir o `conteúdo` em dicas, uma por uma;
    2. Acumular num texto candidato;
    3. Quando o texto candidato corresponde ao segmento alvo atual no roteiro, ele converge para um segmento completo de legenda;
    4. Use o horário de início da primeira sugestão e o horário de término da última sugestão para garantir que a linha do tempo seja contínua."""
    formatter = _build_subtitle_formatter()
    sub_items = []
    sub_index = 0
    current_text = ""
    current_start_time = None

    for cue in sub_maker.cues:
        cue_text = unescape(cue.content)
        if current_start_time is None:
            current_start_time = int(cue.start.total_seconds() * 10000000)

        current_end_time = int(cue.end.total_seconds() * 10000000)
        current_text += cue_text

        matched_text = _match_script_line(script_lines, current_text, sub_index)
        if not matched_text:
            continue

        sub_index += 1
        sub_items.append(
            formatter(
                idx=sub_index,
                start_time=current_start_time,
                end_time=current_end_time,
                sub_text=matched_text,
            )
        )
        current_text = ""
        current_start_time = None

    if current_text.strip():
        logger.warning(
            f"edge cues still have unmatched text after aggregation: {current_text}"
        )

    return sub_items


def _build_subtitle_items_from_legacy_submaker(
    sub_maker: SubMaker, script_lines: list[str]
) -> list[str]:
    """Agregue a estrutura `subs/offset` original do projeto em fragmentos SRT com script.

    Esta parte mantém a ideia central original, mas é dividida em funções independentes para facilitar a integração com edge_tts 7.x
    A lógica de agregação de dicas compartilha o mesmo processo de correspondência e posicionamento de frases."""
    formatter = _build_subtitle_formatter()
    start_time = -1.0
    sub_items = []
    sub_index = 0
    sub_line = ""

    legacy_offsets = getattr(sub_maker, "offset", [])
    legacy_subs = getattr(sub_maker, "subs", [])
    for _, (offset, sub) in enumerate(zip(legacy_offsets, legacy_subs)):
        current_start_time, current_end_time = offset
        if start_time < 0:
            start_time = current_start_time

        sub_line += unescape(sub)
        matched_text = _match_script_line(script_lines, sub_line, sub_index)
        if not matched_text:
            continue

        sub_index += 1
        sub_items.append(
            formatter(
                idx=sub_index,
                start_time=start_time,
                end_time=current_end_time,
                sub_text=matched_text,
            )
        )
        start_time = -1.0
        sub_line = ""

    if sub_line.strip():
        logger.warning(
            f"legacy subtitle items still have unmatched text after aggregation: {sub_line}"
        )

    return sub_items


def create_subtitle(sub_maker: SubMaker, text: str, subtitle_file: str):
    """Otimize arquivos de legenda
    1. Divida o arquivo de legenda em várias linhas de acordo com os sinais de pontuação
    2. Combine o texto no arquivo de legenda linha por linha
    3. Gere novos arquivos de legenda"""
    text = _format_text(text)
    script_lines = utils.split_string_by_punctuations(text)
    try:
        if hasattr(sub_maker, "cues") and sub_maker.cues:
            sub_items = _build_subtitle_items_from_edge_cues(sub_maker, script_lines)
        else:
            sub_items = _build_subtitle_items_from_legacy_submaker(
                sub_maker, script_lines
            )

        if len(sub_items) != len(script_lines):
            logger.warning(
                f"failed, sub_items len: {len(sub_items)}, script_lines len: {len(script_lines)}"
            )
            return

        _write_subtitle_items(sub_items, subtitle_file)
    except Exception as e:
        logger.error(f"failed, error: {str(e)}")


def _get_audio_duration_from_submaker(sub_maker: SubMaker):
    """Obtenha a duração do áudio"""
    # É dada prioridade à compatibilidade com a estrutura de sugestões do edge_tts 7.x;
    # Se for uma estrutura antiga preenchida manualmente por outro TTS do projeto, continue lendo o deslocamento.
    if hasattr(sub_maker, "cues") and sub_maker.cues:
        return sub_maker.cues[-1].end.total_seconds()

    legacy_offsets = getattr(sub_maker, "offset", [])
    if not legacy_offsets:
        return 0.0
    return legacy_offsets[-1][1] / 10000000

def _get_audio_duration_from_file(audio_file: str) -> float:
    """Obtenha a duração do arquivo de áudio (suporta formatos decodificáveis ​​ffmpeg, como mp3/m4a/wav/aac)"""
    if not os.path.exists(audio_file):
        logger.error(f"audio file does not exist: {audio_file}")
        return 0.0

    try:
        # Use moviepy (ffmpeg) to read the duration of any supported audio format
        with AudioFileClip(audio_file) as audio:
            return audio.duration  # Duration in seconds
    except Exception as e:
        logger.error(f"Failed to get audio duration from file: {str(e)}")
        return 0.0

def get_audio_duration(target: Union[str, SubMaker]) -> float:
    """Obtenha a duração do áudio
    Se for um objeto SubMaker, obtenha a duração do SubMaker
    Se for um caminho de arquivo de áudio, obtenha a duração do arquivo de áudio (suporta mp3/m4a/wav e outros formatos)"""
    if isinstance(target, SubMaker):
        return _get_audio_duration_from_submaker(target)
    elif isinstance(target, str):
        return _get_audio_duration_from_file(target)
    else:
        logger.error(f"Invalid target type: {type(target)}")
        return 0.0

if __name__ == "__main__":
    voice_name = "zh-CN-XiaoxiaoMultilingualNeural-V2-Female"
    voice_name = parse_voice_name(voice_name)
    voice_name = is_azure_v2_voice(voice_name)
    print(voice_name)

    voices = get_all_azure_voices()
    print(len(voices))

    async def _do():
        temp_dir = utils.storage_dir("temp")

        voice_names = [
            "zh-CN-XiaoxiaoMultilingualNeural",
            # fêmea
            "zh-CN-XiaoxiaoNeural",
            "zh-CN-XiaoyiNeural",
            # macho
            "zh-CN-YunyangNeural",
            "zh-CN-YunxiNeural",
        ]
        text = """"Silent Night Thoughts" é um antigo poema de cinco caracteres escrito por Li Bai, um poeta da Dinastia Tang. Este poema retrata o poeta vendo a lua brilhante em frente à janela em uma noite tranquila, e não consegue deixar de pensar em sua cidade natal e em seus parentes distantes, expressando seu profundo desejo por sua cidade natal e seus parentes. O conteúdo de todo o poema é: "Há um luar forte na frente da cama e suspeito que seja gelo no chão. Olho para a lua brilhante e abaixo a cabeça para sentir falta da minha cidade natal." Neste curto poema de quatro versos, o poeta expressa habilmente a solidão e a tristeza das pessoas que deixaram sua cidade natal por meio das imagens da “lua brilhante” e do “pensamento na cidade natal”. A primeira frase, “O luar brilhante antes da cama” define o cenário e evoca o devaneio do poeta através do luar brilhante; “Suspeita-se que haja geada no chão” aumenta a sensação de frio da noite e aprofunda a solidão do poeta; “Olhando para a lua brilhante” e “Olhando para a cidade natal” são a sublimação de emoções, mostrando a profunda saudade do poeta e a saudade de casa. Este poema é conciso, vivo e emocionalmente sincero. É uma peça muito famosa da poesia clássica chinesa e é profundamente amada e respeitada pelas gerações futuras."""

        text = """
        What is the meaning of life? This question has puzzled philosophers, scientists, and thinkers of all kinds for centuries. Throughout history, various cultures and individuals have come up with their interpretations and beliefs around the purpose of life. Some say it's to seek happiness and self-fulfillment, while others believe it's about contributing to the welfare of others and making a positive impact in the world. Despite the myriad of perspectives, one thing remains clear: the meaning of life is a deeply personal concept that varies from one person to another. It's an existential inquiry that encourages us to reflect on our values, desires, and the essence of our existence.
        """

        text = """Espera-se que haja atividades frequentes de ar frio em Shenzhen nos próximos três dias, e continuará nublado com chuva fraca nos próximos dois dias. Por favor, traga capa de chuva quando sair;
               Continuará nublado com chuva fraca nos dias 10 e 11, e a diferença diária de temperatura é pequena. A temperatura está entre 13-17 ℃ e é fresca;
               O tempo melhorou brevemente no dia 12, com manhãs e noites frescas;"""

        text = "[Opening scene: A sunny day in a suburban neighborhood. A young boy named Alex, around 8 years old, is playing in his front yard with his loyal dog, Buddy.]\n\n[Camera zooms in on Alex as he throws a ball for Buddy to fetch. Buddy excitedly runs after it and brings it back to Alex.]\n\nAlex: Good boy, Buddy! You're the best dog ever!\n\n[Buddy barks happily and wags his tail.]\n\n[As Alex and Buddy continue playing, a series of potential dangers loom nearby, such as a stray dog approaching, a ball rolling towards the street, and a suspicious-looking stranger walking by.]\n\nAlex: Uh oh, Buddy, look out!\n\n[Buddy senses the danger and immediately springs into action. He barks loudly at the stray dog, scaring it away. Then, he rushes to retrieve the ball before it reaches the street and gently nudges it back towards Alex. Finally, he stands protectively between Alex and the stranger, growling softly to warn them away.]\n\nAlex: Wow, Buddy, you're like my superhero!\n\n[Just as Alex and Buddy are about to head inside, they hear a loud crash from a nearby construction site. They rush over to investigate and find a pile of rubble blocking the path of a kitten trapped underneath.]\n\nAlex: Oh no, Buddy, we have to help!\n\n[Buddy barks in agreement and together they work to carefully move the rubble aside, allowing the kitten to escape unharmed. The kitten gratefully nuzzles against Buddy, who responds with a friendly lick.]\n\nAlex: We did it, Buddy! We saved the day again!\n\n[As Alex and Buddy walk home together, the sun begins to set, casting a warm glow over the neighborhood.]\n\nAlex: Thanks for always being there to watch over me, Buddy. You're not just my dog, you're my best friend.\n\n[Buddy barks happily and nuzzles against Alex as they disappear into the sunset, ready to face whatever adventures tomorrow may bring.]\n\n[End scene.]"

        text = "大家好，我是乔哥，一个想帮你把信用卡全部还清的家伙！\n今天我们要聊的是信用卡的取现功能。\n你是不是也曾经因为一时的资金紧张，而拿着信用卡到ATM机取现？如果是，那你得好好看看这个视频了。\n现在都2024年了，我以为现在不会再有人用信用卡取现功能了。前几天一个粉丝发来一张图片，取现1万。\n信用卡取现有三个弊端。\n一，信用卡取现功能代价可不小。会先收取一个取现手续费，比如这个粉丝，取现1万，按2.5%收取手续费，收取了250元。\n二，信用卡正常消费有最长56天的免息期，但取现不享受免息期。从取现那一天开始，每天按照万5收取利息，这个粉丝用了11天，收取了55元利息。\n三，频繁的取现行为，银行会认为你资金紧张，会被标记为高风险用户，影响你的综合评分和额度。\n那么，如果你资金紧张了，该怎么办呢？\n乔哥给你支一招，用破思机摩擦信用卡，只需要少量的手续费，而且还可以享受最长56天的免息期。\n最后，如果你对玩卡感兴趣，可以找乔哥领取一本《卡神秘籍》，用卡过程中遇到任何疑惑，也欢迎找乔哥交流。\n别忘了，关注乔哥，回复用卡技巧，免费领取《2024用卡技巧》，让我们一起成为用卡高手！"

        text = """Visão geral rápida dos resultados do ano completo de 2023
O lucro operacional acumulado da empresa durante todo o ano foi de 147,694 bilhões de yuans, um aumento anual de 19,01%, e o lucro líquido atribuível à controladora foi de 74,734 bilhões de yuans, um aumento anual de 19,16%. O EPS atingiu 59,49 yuans. Somente no quarto trimestre, o lucro operacional foi de 44,425 bilhões de yuans, um aumento anual de 20,26% e um aumento mensal de 31,86%; o lucro líquido atribuível às empresas-mãe foi de 21,858 bilhões de yuans, um aumento anual de 19,33% e um aumento mensal de 29,37%. esta fase
O desempenho não só destaca a dinâmica de crescimento e rentabilidade da empresa, mas também reflecte que a empresa manteve uma boa dinâmica de desenvolvimento num ambiente de mercado ferozmente competitivo.
Visão geral rápida dos resultados do quarto trimestre de 2023
No quarto trimestre, o resultado operacional contribuiu com o principal ponto de crescimento; aumentaram as elevadas despesas com vendas, o que pressionou a rentabilidade; os impostos aumentaram 27% em relação ao ano anterior, perturbando o desempenho da margem de lucro líquido.
Interpretação de desempenho
Em termos de lucros, o lucro líquido atribuível à empresa-mãe da Kweichow Moutai aumentou 19% em 2023, dos quais o resultado operacional contribuiu com 18%, os custos operacionais contribuíram com 1% e as despesas administrativas contribuíram com 1,4%. (Nota: Taxa de crescimento do lucro líquido atribuível à controladora = taxa de crescimento do lucro operacional + contribuição de cada sujeito, exibe os quatro principais assuntos que contribuem/arrastam, e o valor da contribuição/taxa de crescimento do lucro líquido deve ser> 15%)"""
        text = "静夜思是唐代诗人李白创作的一首五言古诗。这首诗描绘了诗人在寂静的夜晚，看到窗前的明月，不禁想起远方的家乡和亲人"

        text = _format_text(text)
        lines = utils.split_string_by_punctuations(text)
        print(lines)

        for voice_name in voice_names:
            voice_file = f"{temp_dir}/tts-{voice_name}.mp3"
            subtitle_file = f"{temp_dir}/tts.mp3.srt"
            sub_maker = azure_tts_v2(
                text=text, voice_name=voice_name, voice_file=voice_file
            )
            create_subtitle(sub_maker=sub_maker, text=text, subtitle_file=subtitle_file)
            audio_duration = get_audio_duration(sub_maker)
            print(f"voice: {voice_name}, audio duration: {audio_duration}s")

    loop = asyncio.get_event_loop_policy().get_event_loop()
    try:
        loop.run_until_complete(_do())
    finally:
        loop.close()
