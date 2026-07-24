import itertools
import io
import os
import random
import gc
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import ExitStack, redirect_stdout
from functools import lru_cache
from typing import List
from loguru import logger
import numpy as np
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    afx,
)
from moviepy.video.tools.subtitles import SubtitlesClip
from PIL import Image, ImageDraw, ImageFont

from app.config import config
from app.models import const
from app.models.schema import (
    MaterialInfo,
    VideoAspect,
    VideoConcatMode,
    VideoParams,
    VideoTransitionMode,
)
from app.services import bgm as bgm_service
from app.services.utils import video_effects
from app.utils import file_security, utils

class SubClippedVideoClip:
    def __init__(
        self,
        file_path,
        start_time=None,
        end_time=None,
        width=None,
        height=None,
        duration=None,
        source_file_path=None,
    ):
        self.file_path = file_path
        self.start_time = start_time
        self.end_time = end_time
        self.width = width
        self.height = height
        self.source_file_path = source_file_path or file_path
        if duration is None:
            self.duration = end_time - start_time
        else:
            self.duration = duration

    def __str__(self):
        return f"SubClippedVideoClip(file_path={self.file_path}, start_time={self.start_time}, end_time={self.end_time}, duration={self.duration}, width={self.width}, height={self.height})"


audio_codec = "aac"
# A combinação ffmpeg/AAC no Docker é mais propensa a flutuações na qualidade de áudio na configuração padrão.
# Aqui, a taxa de bits de áudio é explicitamente aumentada para evitar distorções óbvias causadas pelo valor padrão ser muito baixo durante o estágio de produção.
audio_bitrate = "192k"
fps = 30
# Quando o FFmpeg emenda/transcodifica na taxa de quadros, a duração final pode ser dezenas de milissegundos menor que a duração teórica lida pelo MoviePy.
# Aqui, deixa-se uma pequena margem de segurança para o material de vídeo para evitar uma tela preta ou tela preta no final do áudio devido ao arredondamento de quadros.
# Gagueira ou nenhuma imagem no último parágrafo da narração.
_VIDEO_DURATION_SAFETY_MARGIN = 0.1
_MIN_MATERIAL_DIMENSION = 480
# Aplicativos de mensagens e alguns codificadores arredondarão o tamanho da tela. Por exemplo, o WhatsApp irá arredondar para baixo as 9h16
# O material é compactado para 478x850, que é dois pixels a menos que 480. Pressionar diretamente o cartão rígido 480 fará com que todos esses materiais sejam
# Descartado e finalmente falhou em geral com "nenhum material válido encontrado". Deixe uma pequena tolerância aqui,
# Ele pode não apenas passar o material que está ligeiramente abaixo do limite devido ao arredondamento, mas também bloquear o material real de baixa definição.
_MIN_DIMENSION_TOLERANCE = 10
_DEFAULT_VIDEO_CODEC = "libx264"
_SUPPORTED_VIDEO_CODECS = (
    "libx264",
    "h264_nvenc",
    "h264_amf",
    "h264_qsv",
    "h264_mf",
    "h264_videotoolbox",
)
_runtime_disabled_video_codecs = set()


def _get_required_video_duration(audio_duration: float) -> float:
    """Retorna a duração desejada da emenda do material de vídeo.

    Cenário de utilização: Ao compor um vídeo, a duração do material precisa cobrir o áudio da narração. Basta fazer "igual a"
    Quando se trata da duração do áudio, o FFmpeg pode tornar o vídeo final um pouco mais curto devido ao arredondamento da taxa de quadros, por isso adiciona um
    Margem leve. A função é independente, o que facilita o teste e o ajuste subsequente do tamanho da margem com base no feedback real."""
    return max(0.0, float(audio_duration) + _VIDEO_DURATION_SAFETY_MARGIN)


def is_material_resolution_acceptable(width: int, height: int) -> bool:
    """Determine se a resolução do material é suficiente para composição.

    O mínimo nominal é 480x480, mas `_MIN_DIMENSION_TOLERANCE` pixels abaixo disso são permitidos,
    Dimensões arredondadas para codificadores/aplicativos de mensagens compatíveis (por exemplo, 478x850 para WhatsApp)."""
    min_dimension = _MIN_MATERIAL_DIMENSION - _MIN_DIMENSION_TOLERANCE
    return width >= min_dimension and height >= min_dimension


def _prioritize_unique_source_clips(
    subclipped_items: List[SubClippedVideoClip],
    concat_mode: VideoConcatMode,
) -> List[SubClippedVideoClip]:
    """Priorize que cada material de origem apareça apenas uma vez para reduzir a probabilidade do mesmo material aparecer repetidamente no filme final.

    Os materiais online muitas vezes encontram a situação de “um vídeo longo sendo cortado em vários clipes curtos”. A velha lógica é
    No modo aleatório, todos os clipes curtos são embaralhados diretamente, resultando em várias fatias do mesmo vídeo de origem.
    Distribuído no início e no meio, os usuários perceberão o material como repetido. Esta função ajusta apenas a ordem dos fragmentos:
    Reproduza primeiro o clipe mais longo de cada arquivo de origem e use os clipes restantes como backup; quando a duração total do material é insuficiente,
    Os segmentos subsequentes ainda podem completar a duração do áudio para evitar danos à taxa de sucesso da geração de vídeo. Priorize o mais longo
    O objetivo do recorte é evitar a seleção aleatória de clipes curtos fragmentados no final do vídeo, resultando em reutilização prematura mesmo que haja material suficiente."""
    if not subclipped_items:
        return []

    concat_mode_value = getattr(concat_mode, "value", concat_mode)
    if concat_mode_value != VideoConcatMode.random.value:
        return subclipped_items

    grouped_items: dict[str, list[SubClippedVideoClip]] = {}
    for item in subclipped_items:
        grouped_items.setdefault(item.source_file_path, []).append(item)

    primary_items = []
    overflow_items = []
    for items in grouped_items.values():
        primary_item = max(items, key=lambda item: item.duration)
        primary_items.append(primary_item)
        overflow_items.extend(item for item in items if item is not primary_item)

    random.shuffle(primary_items)
    random.shuffle(overflow_items)
    logger.info(
        "prioritized unique video materials, "
        f"sources: {len(grouped_items)}, "
        f"primary clips: {len(primary_items)}, "
        f"fallback clips: {len(overflow_items)}"
    )
    return primary_items + overflow_items


def get_ffmpeg_binary():
    """Compatível com chamadores que historicamente leem caminhos FFmpeg diretamente do serviço de vídeo.

    A lógica de análise real foi extraída para `app.utils.utils.get_ffmpeg_binary()`, vídeo, voz
    O mesmo conjunto de prioridades deve ser reutilizado com novas ligações subsequentes; embalagem fina é mantida aqui para evitar scripts externos ou
    Testes antigos geravam AttributeError ao importar `app.services.video.get_ffmpeg_binary` diretamente."""
    return utils.get_ffmpeg_binary()


def _get_configured_video_codec() -> str:
    """Leia o codificador de vídeo configurado pelo usuário.

    Esta configuração é para usuários avançados que tentam habilitar hardware como NVENC/AMF/QSV/VideoToolbox
    Codificação. Apenas uma lista de permissões fixa é deliberadamente permitida aqui para evitar que os usuários preencham erros após abrir qualquer parâmetro do FFmpeg.
    Os parâmetros fazem com que o formato de saída fique incontrolável e até mesmo fazem com que a tarefa de geração falhe nos estágios subsequentes."""
    configured_codec = str(
        config.app.get("video_codec", _DEFAULT_VIDEO_CODEC) or _DEFAULT_VIDEO_CODEC
    ).strip()
    if configured_codec not in _SUPPORTED_VIDEO_CODECS:
        logger.warning(
            f"unsupported video codec configured: {configured_codec}, "
            f"fallback to {_DEFAULT_VIDEO_CODEC}"
        )
        return _DEFAULT_VIDEO_CODEC
    return configured_codec


@lru_cache(maxsize=16)
def _ffmpeg_encoder_exists(ffmpeg_binary: str, codec: str) -> bool:
    """Verifique se o FFmpeg atual declara suporte para o codificador especificado.

    Isso só pode provar que o FFmpeg inclui este codificador durante a compilação, mas não pode provar o hardware e driver da máquina atual.
    Deve estar disponível. Portanto, ele ainda retornará para libx264 quando a codificação real falhar."""
    try:
        result = subprocess.run(
            [ffmpeg_binary, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "failed to inspect ffmpeg encoders, "
            f"fallback to {_DEFAULT_VIDEO_CODEC}: {str(exc)}"
        )
        return False

    if result.returncode != 0:
        logger.warning(
            "failed to inspect ffmpeg encoders, "
            f"fallback to {_DEFAULT_VIDEO_CODEC}: {(result.stderr or result.stdout or '').strip()}"
        )
        return False
    return codec in result.stdout


def _get_effective_video_codec(preferred_codec: str | None = None) -> str:
    """Retorna o codificador de vídeo real usado desta vez.

    Quando o usuário seleciona um codificador de hardware, primeiro execute a detecção da lista de codificadores FFmpeg; se esse processo já
    Se a codificação real falhar, ela será revertida diretamente para evitar falhas repetidas em cada segmento de uma tarefa."""
    selected_codec = preferred_codec or _get_configured_video_codec()
    if selected_codec == _DEFAULT_VIDEO_CODEC:
        return _DEFAULT_VIDEO_CODEC

    if selected_codec in _runtime_disabled_video_codecs:
        logger.warning(
            f"video codec {selected_codec} was disabled after a runtime failure, "
            f"fallback to {_DEFAULT_VIDEO_CODEC}"
        )
        return _DEFAULT_VIDEO_CODEC

    ffmpeg_binary = utils.get_ffmpeg_binary()
    if not _ffmpeg_encoder_exists(ffmpeg_binary, selected_codec):
        logger.warning(
            f"ffmpeg encoder {selected_codec} is not available, "
            f"fallback to {_DEFAULT_VIDEO_CODEC}"
        )
        return _DEFAULT_VIDEO_CODEC

    return selected_codec


def _disable_runtime_video_codec(codec: str, reason: str):
    if codec == _DEFAULT_VIDEO_CODEC:
        return
    _runtime_disabled_video_codecs.add(codec)
    logger.warning(
        f"video codec {codec} failed, fallback to {_DEFAULT_VIDEO_CODEC}. "
        f"reason: {reason}"
    )


def _get_temp_audio_dir(output_dir: str) -> str:
    """
    Return the directory to use for MoviePy's temporary audio file.

    On Windows, Windows Defender can lock files written to the task output
    directory while scanning them, causing MoviePy to fail with a
    PermissionError (WinError 32) on the TEMP_MPY_wvf_snd temp file and
    leaving the final MP4 at 0 bytes.  Using the system temp directory
    sidesteps the scan without changing behaviour on other platforms.

    On Linux/macOS/Docker the output directory is returned unchanged so
    existing behaviour is preserved.
    """
    if sys.platform == "win32":
        return tempfile.gettempdir()
    return output_dir


def _fallback_write_videofile(clip, output_file: str, failed_codec: str, reason: str, **kwargs):
    """Depois que a codificação do hardware falhar, tente novamente com libx264. O codificador de hardware será desativado somente se a nova tentativa for bem-sucedida.

    A razão pela qual o FFmpeg falha no Windows é mais complicada: pode ser que a placa gráfica/driver não suporte ou pode ser a saída
    Problemas gerais de IO, como ocupação de arquivos, permissões de diretório e interceptação de software antivírus. Quando apenas libx264 pode escrever com sucesso,
    Só então podemos determinar que a falha original provavelmente veio do próprio codificador de hardware para evitar danos acidentais nas tarefas subsequentes."""
    clip.write_videofile(output_file, codec=_DEFAULT_VIDEO_CODEC, **kwargs)
    _disable_runtime_video_codec(failed_codec, reason)
    return _DEFAULT_VIDEO_CODEC


def _write_videofile_with_codec_fallback(clip, output_file: str, codec: str, **kwargs):
    """Grave o vídeo usando o codificador especificado e tente novamente automaticamente com libx264 se falhar.

    A disponibilidade de um codificador de hardware depende não apenas do FFmpeg, mas também da placa gráfica, do driver e do ambiente de execução atual.
    A tarefa de geração não pode falhar como um todo porque o codificador avançado não está disponível, portanto o fallback é tratado centralmente aqui."""
    effective_codec = _get_effective_video_codec(codec)
    try:
        clip.write_videofile(output_file, codec=effective_codec, **kwargs)
        return effective_codec
    except Exception as exc:
        if effective_codec == _DEFAULT_VIDEO_CODEC:
            raise
        return _fallback_write_videofile(
            clip,
            output_file,
            failed_codec=effective_codec,
            reason=str(exc),
            **kwargs,
        )


def _escape_ffmpeg_concat_path(file_path: str) -> str:
    # concat demuxer usa aspas simples para quebrar o caminho, e as aspas simples no caminho precisam ser escapadas primeiro.
    return file_path.replace("'", "'\\''")


def _format_ffmpeg_concat_path(file_path: str) -> str:
    """Gere caminhos na lista de arquivos do demuxer concat.

    A documentação oficial do FFmpeg exige que caracteres especiais e espaços na lista concat precisem ser escapados; Janelas
    Barras invertidas em caminhos absolutos também são facilmente analisadas como caracteres de escape. Aqui ele é convertido uniformemente em forma de barra.
    Deixe `C:\\Users\\...` se tornar `C:/Users/...` e, em seguida, processe aspas simples, compatíveis com macOS/Linux."""
    absolute_path = os.path.abspath(file_path)
    return _escape_ffmpeg_concat_path(absolute_path.replace("\\", "/"))


def concat_video_clips_with_ffmpeg(
    clip_files: List[str],
    output_file: str,
    threads: int,
    output_dir: str,
    max_duration: float | None = None,
):
    concat_list_file = os.path.join(output_dir, "ffmpeg-concat-list.txt")
    with open(concat_list_file, "w", encoding="utf-8") as fp:
        for clip_file in clip_files:
            fp.write(f"file '{_format_ffmpeg_concat_path(clip_file)}'\n")

    def build_command(codec: str) -> list[str]:
        command = [
            utils.get_ffmpeg_binary(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_list_file,
            "-c:v",
            codec,
            "-threads",
            str(threads or 2),
            "-pix_fmt",
            "yuv420p",
        ]
        if max_duration is not None and max_duration > 0:
            command.extend(["-t", f"{max_duration:.3f}"])
        command.append(output_file)
        return command

    def run_concat(codec: str):
        command = build_command(codec)
        # Use ffmpeg para concatenar e codificar apenas uma vez para evitar recodificações repetidas ao mesclar segmento por segmento do MoviePy.
        # Isto reduz o risco de degradação da qualidade da imagem e mudança de cor.
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            error_message = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(error_message or "ffmpeg concat failed")
        return codec

    try:
        effective_codec = _get_effective_video_codec()
        try:
            return run_concat(effective_codec)
        except Exception as exc:
            if effective_codec == _DEFAULT_VIDEO_CODEC:
                raise
            result_codec = run_concat(_DEFAULT_VIDEO_CODEC)
            _disable_runtime_video_codec(effective_codec, str(exc))
            return result_codec
    finally:
        delete_files(concat_list_file)


def _sanitize_image_file(image_path: str) -> str:
    # Embora algumas imagens locais possam ser abertas pelo Pillow, elas serão danificadas devido a metadados EXIF/eXIf corrompidos.
    # ImageClip lança uma exceção diretamente durante a fase de análise. Aqui, exporte novamente uma "imagem limpa" e remova os metadados incorretos.
    image_root, _ = os.path.splitext(image_path)
    sanitized_path = f"{image_root}.sanitized.png"

    with Image.open(image_path) as image:
        image.load()
        # Exporte para PNG uniformemente para evitar que diferentes caminhos de metadados de JPEG/PNG continuem trazendo blocos defeituosos.
        cleaned_image = Image.new(image.mode, image.size)
        cleaned_image.putdata(list(image.getdata()))
        cleaned_image.save(sanitized_path)

    return sanitized_path


def _open_image_clip_with_fallback(image_path: str):
    # É dada prioridade à abertura direta da imagem original; se falhar devido a metadados danificados, tente gerar uma cópia sem metadados.
    try:
        return ImageClip(image_path), image_path
    except Exception as exc:
        logger.warning(
            f"failed to open image directly, trying sanitized copy: {image_path}, error: {str(exc)}"
        )
        sanitized_path = _sanitize_image_file(image_path)
        return ImageClip(sanitized_path), sanitized_path


def _open_video_clip_quietly(video_path: str, audio: bool = False) -> VideoFileClip:
    """Abra arquivos de vídeo silenciosamente para evitar que o MoviePy 2.1.x imprima informações do teste ffmpeg diretamente no stdout.

    Antecedentes:
    A versão dependente atual de `FFMPEG_VideoReader` contém internamente `print(self.infos)` e
    `print (ffmpeg command)`, será gerado ao ler o vídeo do meio sem trilha de áudio
    `audio_found: Falso`. Isto são apenas metadados de material de entrada, não significa que o filme final não terá áudio.
    Mas isso enganará o WebUI/usuário final, fazendo-o pensar que a compilação falhou.

    Implementação:
    1. Redirecione o stdout apenas na pequena janela que abre o VideoFileClip;
    2. Padrão `audio=False`, pois o som original do material não precisa ser preservado na etapa de material de vídeo do projeto.
       O áudio final será montado uniformemente no estágio `generate_video()`;
    3. Se a biblioteca dependente gerar conteúdo, faça downgrade dela para o log de depuração para facilitar a solução de problemas, se necessário."""
    captured_stdout = io.StringIO()
    with redirect_stdout(captured_stdout):
        clip = VideoFileClip(video_path, audio=audio)

    moviepy_stdout = captured_stdout.getvalue().strip()
    if moviepy_stdout:
        logger.debug(
            "suppressed MoviePy video reader stdout for "
            f"{video_path}, chars: {len(moviepy_stdout)}"
        )

    return clip


def close_clip(clip):
    if clip is None:
        return
        
    try:
        # close main resources
        if hasattr(clip, 'reader') and clip.reader is not None:
            clip.reader.close()
            
        # close audio resources
        if hasattr(clip, 'audio') and clip.audio is not None:
            if hasattr(clip.audio, 'reader') and clip.audio.reader is not None:
                clip.audio.reader.close()
            del clip.audio
            
        # close mask resources
        if hasattr(clip, 'mask') and clip.mask is not None:
            if hasattr(clip.mask, 'reader') and clip.mask.reader is not None:
                clip.mask.reader.close()
            del clip.mask
            
        # handle child clips in composite clips
        if hasattr(clip, 'clips') and clip.clips:
            for child_clip in clip.clips:
                if child_clip is not clip:  # avoid possible circular references
                    close_clip(child_clip)
            
        # clear clip list
        if hasattr(clip, 'clips'):
            clip.clips = []
            
    except Exception as e:
        logger.error(f"failed to close clip: {str(e)}")
    
    del clip
    gc.collect()

def delete_files(files: List[str] | str):
    if isinstance(files, str):
        files = [files]

    # Ao fazer um loop no vídeo, o mesmo caminho de clipe temporário aparece várias vezes na lista de emendas do FFmpeg.
    # As duplicatas devem ser retidas durante a emenda, mas a limpeza só pode ser excluída uma vez; aqui, as duplicatas são removidas na ordem original, para que todas
    # O chamador obtém um comportamento idempotente e evita a saída contínua de FileNotFoundError após a primeira exclusão ser bem-sucedida.
    unique_files = dict.fromkeys(file for file in files if file)
    for file in unique_files:
        try:
            os.remove(file)
        except FileNotFoundError:
            # As ações de limpeza permitem arquivos que não existem mais, como o caminho com falha do FFmpeg ou a limpeza simultânea.
            # Reciclar arquivos; este não é um problema relacionado ao usuário e não deve poluir o log de construção.
            continue
        except OSError as e:
            # Permissões, sistema de arquivos somente leitura ou exceções de disco deixarão arquivos temporários reais, continue avisando
            # É conveniente localizar problemas ambientais com base em caminhos específicos e erros de sistema.
            logger.warning(f"failed to delete temporary file {file}: {str(e)}")


def get_bgm_file(bgm_type: str = "random", bgm_file: str = ""):
    if not bgm_type:
        return ""

    if bgm_file:
        try:
            resolved_bgm_file = bgm_service.resolve_bgm_file(bgm_file)
        except ValueError as exc:
            # O bgm_file na solicitação da API vem da entrada do usuário e só pode ser analisado no BGM do usuário ou no integrado
            # Diretório de músicas, evitando que o MoviePy leia quaisquer arquivos do servidor, como configurações e chaves.
            logger.warning(
                f"reject unsafe bgm file: {bgm_file}, error: {str(exc)}"
            )
            return ""
        return resolved_bgm_file

    if bgm_type == "random":
        files = bgm_service.list_bgm_files()
        # Quando o diretório da música de fundo estiver vazio, ele retornará diretamente para "no BGM" para evitar random.choice([]) lançando exceções.
        if not files:
            logger.warning("no background music files found")
            return ""
        return random.choice(files)

    return ""


def combine_videos(
    combined_video_path: str,
    video_paths: List[str],
    audio_file: str,
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    video_transition_mode: VideoTransitionMode = None,
    max_clip_duration: int = 5,
    threads: int = 2,
    clip_speed: float = 1.0,
) -> str:
    audio_clip = AudioFileClip(audio_file)
    try:
        # Aqui você só precisa ler a duração do áudio da narração para determinar a duração da emenda do vídeo do material; não será usado novamente mais tarde.
        # clipe de áudio. Feche imediatamente após a conclusão da leitura para evitar saída antecipada ou vazamento anormal de caminhos de identificadores de arquivo.
        audio_duration = audio_clip.duration
    finally:
        close_clip(audio_clip)
    logger.info(f"audio duration: {audio_duration} seconds")
    logger.info(f"maximum clip duration: {max_clip_duration} seconds")
    required_video_duration = _get_required_video_duration(audio_duration)
    logger.info(
        f"required video duration: {required_video_duration:.2f} seconds "
        f"(audio duration + {_VIDEO_DURATION_SAFETY_MARGIN:.2f}s safety margin)"
    )

    # Compatível com a situação em que o modo de transição não é passado ao chamar a API diretamente, para evitar travamentos ao acessar posteriormente .value.
    transition_value = getattr(video_transition_mode, "value", video_transition_mode)
    normalized_clip_speed = utils.normalize_clip_speed(clip_speed)
    if normalized_clip_speed != 1.0:
        # Registrar o valor efetivo final apenas uma vez é conveniente para localizar o problema de normalização de parâmetros fora dos limites da API.
        # Também evita a saída repetida dos mesmos logs em hot paths por fragmento.
        logger.info(f"clip playback speed: {normalized_clip_speed:.2f}x")
    # max_clip_duration restringe o tempo final de reprodução do filme finalizado, não o tempo de leitura do vídeo de origem.
    # O MoviePy reproduz 1,5 segundos da filmagem original a uma velocidade de 0,5x e obtém um clipe de 3 segundos, reproduzido a uma velocidade de 2x
    # Uma filmagem de origem de 6 segundos também resultará em um clipe de 3 segundos. Portanto, a duração da fonte deve ser deduzida de acordo com a velocidade antes do fatiamento; se
    # Ele ainda lê por 3 segundos antes de desacelerar e cortar, mas o próximo segmento começa no terceiro segundo do vídeo de origem e o meio é ignorado.
    # 1,5 segundos de filmagem. Este cálculo também garante que os cronogramas de origem em diferentes velocidades sejam contínuos e não sobrepostos.
    source_clip_duration = max_clip_duration * normalized_clip_speed
    output_dir = os.path.dirname(combined_video_path)

    aspect = VideoAspect(video_aspect)
    video_width, video_height = aspect.to_resolution()

    processed_clips = []
    subclipped_items = []
    video_duration = 0
    for video_path in video_paths:
        clip = _open_video_clip_quietly(video_path)
        clip_duration = clip.duration
        clip_w, clip_h = clip.size
        close_clip(clip)
        
        start_time = 0

        while start_time < clip_duration:
            end_time = min(start_time + source_clip_duration, clip_duration)

            # Mantenha todos os segmentos válidos.
            # Isso não perderá o material de que "o vídeo inteiro é menor que max_clip_duration".
            # Ele não engolirá o pequeno pedaço de conteúdo deixado no final de um vídeo longo.
            if end_time > start_time:
                subclipped_items.append(
                    SubClippedVideoClip(
                        file_path=video_path,
                        start_time=start_time,
                        end_time=end_time,
                        width=clip_w,
                        height=clip_h,
                        source_file_path=video_path,
                    )
                )

            start_time = end_time
            if video_concat_mode.value == VideoConcatMode.sequential.value:
                break

    subclipped_items = _prioritize_unique_source_clips(
        subclipped_items=subclipped_items,
        concat_mode=video_concat_mode,
    )
        
    logger.debug(f"total subclipped items: {len(subclipped_items)}")
    
    # Add downloaded clips over and over until the duration of the audio (max_duration) has been reached
    for i, subclipped_item in enumerate(subclipped_items):
        if video_duration >= required_video_duration:
            break
        
        logger.debug(
            f"processing clip {i+1}: {subclipped_item.width}x{subclipped_item.height}, "
            f"source: {os.path.basename(subclipped_item.source_file_path)}, "
            f"current duration: {video_duration:.2f}s, "
            f"remaining: {required_video_duration - video_duration:.2f}s"
        )
        
        try:
            clip = _open_video_clip_quietly(subclipped_item.file_path).subclipped(
                subclipped_item.start_time, subclipped_item.end_time
            )
            # A velocidade de reprodução é uma propriedade do próprio material e deve ser aplicada antes da transição. Dessa forma, Fade/Slide espera um segundo para fazer a transição.
            # Não seguirá a velocidade do material em 0,5 segundos ou 2 segundos; o corte de duração máxima subsequente continuará como
            # Uma margem segura para erros de ponto flutuante ou duração anormal do material para garantir que o clipe final não exceda o limite de configuração.
            if normalized_clip_speed != 1.0:
                clip = clip.with_speed_scaled(normalized_clip_speed)
            clip_duration = clip.duration
            # Not all videos are same size, so we need to resize them
            clip_w, clip_h = clip.size
            if clip_w != video_width or clip_h != video_height:
                clip_ratio = clip.w / clip.h
                video_ratio = video_width / video_height
                logger.debug(f"resizing clip, source: {clip_w}x{clip_h}, ratio: {clip_ratio:.2f}, target: {video_width}x{video_height}, ratio: {video_ratio:.2f}")
                
                if clip_ratio == video_ratio:
                    clip = clip.resized(new_size=(video_width, video_height))
                else:
                    if clip_ratio > video_ratio:
                        scale_factor = video_width / clip_w
                    else:
                        scale_factor = video_height / clip_h

                    new_width = int(clip_w * scale_factor)
                    new_height = int(clip_h * scale_factor)

                    background = ColorClip(size=(video_width, video_height), color=(0, 0, 0)).with_duration(clip_duration)
                    clip_resized = clip.resized(new_size=(new_width, new_height)).with_position("center")
                    clip = CompositeVideoClip([background, clip_resized])
                    
            shuffle_side = random.choice(["left", "right", "top", "bottom"])
            if transition_value in (None, VideoTransitionMode.none.value):
                clip = clip
            elif transition_value == VideoTransitionMode.fade_in.value:
                clip = video_effects.fadein_transition(clip, 1)
            elif transition_value == VideoTransitionMode.fade_out.value:
                clip = video_effects.fadeout_transition(clip, 1)
            elif transition_value == VideoTransitionMode.slide_in.value:
                clip = video_effects.slidein_transition(clip, 1, shuffle_side)
            elif transition_value == VideoTransitionMode.slide_out.value:
                clip = video_effects.slideout_transition(clip, 1, shuffle_side)
            elif transition_value == VideoTransitionMode.zoom_in.value:
                clip = video_effects.zoomin_transition(clip, 1)
            elif transition_value == VideoTransitionMode.zoom_out.value:
                clip = video_effects.zoomout_transition(clip, 1)
            elif transition_value == VideoTransitionMode.shuffle.value:
                transition_funcs = [
                    lambda c: video_effects.fadein_transition(c, 1),
                    lambda c: video_effects.fadeout_transition(c, 1),
                    lambda c: video_effects.slidein_transition(c, 1, shuffle_side),
                    lambda c: video_effects.slideout_transition(c, 1, shuffle_side),
                    lambda c: video_effects.zoomin_transition(c, 1),
                    lambda c: video_effects.zoomout_transition(c, 1),
                ]
                shuffle_transition = random.choice(transition_funcs)
                clip = shuffle_transition(clip)

            if clip.duration > max_clip_duration:
                clip = clip.subclipped(0, max_clip_duration)
                
            # wirte clip to temp file
            clip_file = f"{output_dir}/temp-clip-{i+1}.mp4"
            _write_videofile_with_codec_fallback(
                clip,
                clip_file,
                codec=_get_configured_video_codec(),
                logger=None,
                fps=fps,
            )

            # Store clip duration before closing
            clip_duration_saved = clip.duration
            close_clip(clip)

            processed_clips.append(
                SubClippedVideoClip(
                    file_path=clip_file,
                    duration=clip_duration_saved,
                    width=clip_w,
                    height=clip_h,
                    source_file_path=subclipped_item.source_file_path,
                )
            )
            video_duration += clip_duration_saved
            
        except Exception as e:
            logger.error(f"failed to process clip: {str(e)}")
    
    # loop processed clips until the video duration covers the audio duration and the small safety margin.
    if video_duration < required_video_duration:
        logger.warning(
            f"video duration ({video_duration:.2f}s) is shorter than required duration "
            f"({required_video_duration:.2f}s), looping clips to match audio length."
        )
        base_clips = processed_clips.copy()
        for clip in itertools.cycle(base_clips):
            if video_duration >= required_video_duration:
                break
            processed_clips.append(clip)
            video_duration += clip.duration
        logger.info(
            f"video duration: {video_duration:.2f}s, audio duration: {audio_duration:.2f}s, "
            f"required duration: {required_video_duration:.2f}s, "
            f"looped {len(processed_clips)-len(base_clips)} clips"
        )
     
    # merge video clips progressively, avoid loading all videos at once to avoid memory overflow
    logger.info("starting clip merging process")
    if not processed_clips:
        logger.warning("no clips available for merging")
        return combined_video_path
    
    clip_files = [clip.file_path for clip in processed_clips]
    logger.info(f"concatenating {len(clip_files)} clips with ffmpeg")
    concat_video_clips_with_ffmpeg(
        clip_files=clip_files,
        output_file=combined_video_path,
        threads=threads,
        output_dir=output_dir,
        max_duration=audio_duration,
    )
    
    # clean temp files
    delete_files(clip_files)
            
    logger.info("video combining completed")
    return combined_video_path


def wrap_text(text, max_width, font="Arial", fontsize=60):
    # O ajuste da legenda deve ser concluído antes de criar o TextClip, caso contrário, o MoviePy pressionará apenas o texto original
    # Calcule a área de renderização. Aqui, o PIL é usado para medir a largura de acordo com a fonte atual e o tamanho da fonte, garantindo que cada linha seja a mais larga possível
    # Controle-o dentro da largura disponível do vídeo para evitar que tamanhos de fonte grandes ou frases longas em chinês transbordem diretamente a tela.
    font = ImageFont.truetype(font, fontsize)
    max_width = int(max_width)

    def get_text_size(inner_text):
        inner_text = inner_text.strip()
        if not inner_text:
            return 0, fontsize
        left, top, right, bottom = font.getbbox(inner_text)
        return right - left, bottom - top

    width, height = get_text_size(text)
    if width <= max_width:
        return text, height

    def split_long_token(token):
        # Quando um token em si é muito largo (comum em frases longas sem espaços em chinês ou em palavras longas em inglês),
        # Degenera em divisão em nível de personagem. O ponto chave é: quando for detectado que um candidato é muito amplo, envie primeiro o anterior
        # Current ainda é válido e coloca o caractere atual na próxima linha. Ele não pode colocar o caractere superlargo de volta na linha anterior.
        lines = []
        current = ""
        for char in token:
            candidate = f"{current}{char}"
            candidate_width, _ = get_text_size(candidate)
            if candidate_width <= max_width or not current:
                current = candidate
                continue
            lines.append(current)
            current = char
        if current:
            lines.append(current)
        return lines

    lines = []
    current = ""
    words = text.split(" ")
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        candidate_width, _ = get_text_size(candidate)
        if candidate_width <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)

        word_width, _ = get_text_size(word)
        if word_width <= max_width:
            current = word
        else:
            lines.extend(split_long_token(word))
            current = ""

    if current:
        lines.append(current)

    line_start_punctuation = "，。！？；：、,.!?;:)]}）】》」』”’"
    for index in range(1, len(lines)):
        # Quando uma frase longa em chinês é dividida por caracteres, o último ponto final, vírgula e outras pontuações de fechamento podem ser separados
        # Colocá-lo na próxima linha faz com que o fundo da legenda fique anormalmente elevado, visualmente como um pequeno ponto caindo no texto principal.
        # abaixo. Aqui, sem redesenhar o algoritmo de nova linha, a última palavra da linha anterior é
        # Mova-o para a frente da linha de pontuação e deixe a pontuação seguir a exibição do texto. É compatível com pontuação fechada comum em chinês e inglês.
        if not lines[index] or lines[index][0] not in line_start_punctuation:
            continue
        if len(lines[index - 1]) <= 1:
            continue

        candidate = f"{lines[index - 1][-1]}{lines[index]}"
        candidate_width, _ = get_text_size(candidate)
        if candidate_width <= max_width:
            lines[index] = candidate
            lines[index - 1] = lines[index - 1][:-1]

    result = "\n".join(line.strip() for line in lines if line.strip()).strip()
    height = len(lines) * height
    return result, height


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    # A cor de fundo da legenda vem dos parâmetros API/WebUI e pode estar vazia ou em formato irregular. Aqui só aceitamos
    # No formato #RRGGBB, os valores ilegais voltam para preto para evitar exceções lançadas durante a fase de renderização do PIL e interromper a tarefa.
    if isinstance(color, str) and color.startswith("#") and len(color) == 7:
        try:
            return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
        except ValueError:
            pass
    return (0, 0, 0)


def _rounded_subtitle_background_clip(
    width: int,
    height: int,
    color: str,
    alpha: int = 140,
    radius: int = 16,
) -> ImageClip:
    # O novo plano de fundo da legenda só é usado quando o usuário o ativa explicitamente: desenhe uma placa de base arredondada e semitransparente a partir de uma imagem RGBA,
    # Em seguida, entregue-o ao MoviePy como um ImageClip transparente para participar da síntese. Desta forma, o caminho padrão permanece completamente inalterado.
    # Ao mesmo tempo, você pode experimentar visuais de legendas mais suaves a um custo baixo.
    rgb = _hex_to_rgb(color)
    safe_alpha = max(0, min(255, int(alpha)))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [0, 0, max(0, width - 1), max(0, height - 1)],
        radius=max(0, int(radius)),
        fill=(rgb[0], rgb[1], rgb[2], safe_alpha),
    )
    return ImageClip(np.array(img), transparent=True)


def _get_visible_center_position(
    text_clip: TextClip,
    container_width: int,
    container_height: int,
) -> tuple[int, int]:
    """Coloque o TextClip no centro do contêiner de fundo de acordo com os pixels visíveis reais do texto.

    O TextClip do MoviePy cria uma tela transparente com base na altura e linha de base da linha da fonte. muitas fontes
    Pode-se observar que o glifo não está no centro geométrico desta tela, diretamente `with_position("center")`
    Toda a tela transparente será centralizada, fazendo com que as legendas pareçam mais altas ou mais baixas. Leia aqui TextClip
    A máscara transparente apenas calcula o deslocamento com base no bbox que realmente possui pixels, para que o usuário possa ver o texto
    Visualmente centralizado no fundo da legenda."""
    x = int(round((container_width - text_clip.w) / 2))
    y = int(round((container_height - text_clip.h) / 2))

    try:
        if text_clip.mask is None:
            return x, y

        mask_frame = text_clip.mask.get_frame(0)
        ys, _ = np.where(mask_frame > 0.01)
        if len(ys) == 0:
            return x, y

        visible_top = int(ys.min())
        visible_bottom = int(ys.max())
        visible_height = visible_bottom - visible_top + 1
        y = int(round((container_height - visible_height) / 2 - visible_top))
    except Exception as exc:
        logger.debug(f"failed to center subtitle text by visible mask: {str(exc)}")

    return x, y


def subtitle_colors_are_indistinguishable(params: VideoParams) -> bool:
    """Determine se o texto e o fundo da legenda são da mesma cor e lembre os usuários de que talvez eles não consigam ver as legendas com clareza."""
    if not params.subtitle_enabled or not params.text_background_color:
        return False

    def normalize_color(value):
        if isinstance(value, bool):
            return "#000000" if value else ""
        return str(value or "").strip().lower()

    text_color = normalize_color(params.text_fore_color)
    background_color = normalize_color(params.text_background_color)
    return bool(text_color and text_color == background_color)


@lru_cache(maxsize=64)
def _subtitle_font_supports_sample(font_path: str, sample: str) -> bool:
    """Verifica se a fonte contém os glifos necessários para o texto de amostra e armazena em cache os resultados da verificação duplicada."""
    try:
        font = ImageFont.truetype(font_path, 30)
        missing_mask = font.getmask("\U0010ffff")
        missing_signature = (
            missing_mask.size,
            missing_mask.getbbox(),
            bytes(missing_mask),
        )
        for char in sample:
            char_mask = font.getmask(char)
            char_signature = (
                char_mask.size,
                char_mask.getbbox(),
                bytes(char_mask),
            )
            if char_mask.getbbox() is None or char_signature == missing_signature:
                return False
        return True
    except Exception as e:
        # A falha na detecção de fontes não deve impedir os usuários de construir; mantenha registros para solucionar problemas de compatibilidade do ambiente.
        logger.warning(f"failed to inspect subtitle font glyphs: {font_path}, {e}")
        return True


def subtitle_font_supports_text(font_path: str, text: str) -> bool:
    """Verifica se a fonte consegue desenhar as letras e números do texto, ignorando espaços em branco e pontuação."""
    sample = "".join(
        dict.fromkeys(
            char
            for char in str(text or "")
            if unicodedata.category(char)[0] in {"L", "N"}
        )
    )[:64]
    if not sample:
        return True
    return _subtitle_font_supports_sample(font_path, sample)


def generate_video(
    video_path: str,
    audio_path: str,
    subtitle_path: str,
    output_file: str,
    params: VideoParams,
    bgm_file_override: str | None = None,
) -> bool:
    """Sintetize o vídeo final e retorne se o processamento da música de fundo foi bem-sucedido.

    O valor de retorno descreve apenas o status de processamento da música de fundo: True é retornado quando a música de fundo não é solicitada ou mixada com êxito; solicitado
    BGM, mas retorna False se o carregamento, os efeitos ou a mixagem falharem. Mesmo se o BGM falhar, ele continuará a produzir apenas
    O vídeo narrado permite que a camada de orquestração de tarefas decida se deseja mostrar ao usuário um aviso de degradação."""
    aspect = VideoAspect(params.video_aspect)
    video_width, video_height = aspect.to_resolution()

    logger.info(f"generating video: {video_width} x {video_height}")
    logger.info(f"  ① video: {video_path}")
    logger.info(f"  ② audio: {audio_path}")
    logger.info(f"  ③ subtitle: {subtitle_path}")
    logger.info(f"  ④ output: {output_file}")

    # https://github.com/harry0703/MoneyPrinterTurbo/issues/217
    # PermissionError: [WinError 32] The process cannot access the file because it is being used by another process: 'final-1.mp4.tempTEMP_MPY_wvf_snd.mp3'
    # write into the same directory as the output file
    output_dir = os.path.dirname(output_file)

    font_path = ""
    if params.subtitle_enabled:
        if not params.font_name:
            params.font_name = "STHeitiMedium.ttc"
        font_path = os.path.join(utils.font_dir(), params.font_name)
        if os.name == "nt":
            font_path = font_path.replace("\\", "/")

        logger.info(f"  ⑤ font: {font_path}")

    def resolve_subtitle_background_color():
        # Compatível com parâmetros históricos: `text_background_color` na API pode ser um valor booleano,
        # Também pode ser uma sequência de cores real. Normalize uniformemente aqui para evitar a conversão de Verdadeiro/Falso
        # Resultados inesperados de renderização ocorrem após passá-lo diretamente para o TextClip.
        if isinstance(params.text_background_color, bool):
            return "#000000" if params.text_background_color else None
        return params.text_background_color

    def create_text_clip(subtitle_item):
        params.font_size = int(params.font_size)
        params.stroke_width = int(params.stroke_width)
        phrase = subtitle_item[1]
        max_width = video_width * 0.9
        bg_color = resolve_subtitle_background_color()
        rounded_bg_enabled = bool(
            getattr(params, "rounded_subtitle_background", False) and bg_color
        )
        has_subtitle_background = bool(bg_color)
        # O fundo arredondado é gerado de acordo com a largura real do texto, e os espaços esquerdo e direito devem ser mais restritos; o antigo fundo retangular ainda é mantido
        # Margens de segurança maiores para evitar que legendas longas sejam cortadas ou cortadas em configurações históricas.
        padding_ratio = 0.4 if rounded_bg_enabled else 0.6
        pad_x = int(params.font_size * padding_ratio) if has_subtitle_background else 0
        # Os fundos das legendas precisam deixar preenchimento claro nos lados esquerdo e direito do texto. Primeiro subtraia da largura disponível
        # preenchimento e, em seguida, envolva a linha para evitar um inglês longo ou um tamanho de fonte grande que preencha apenas 90% da largura do vídeo.
        # O texto é colado na borda da caixa de fundo e parece cortado. Fundo retangular comum e fundo de canto arredondado
        # Esta lógica é seguida; legendas sem fundo mantêm a largura máxima original.
        text_max_width = max(1, int(max_width) - 2 * pad_x)
        wrapped_txt, txt_height = wrap_text(
            phrase,
            max_width=text_max_width,
            font=font_path,
            fontsize=params.font_size,
        )
        interline = int(params.font_size * 0.25)
        line_count = wrapped_txt.count("\n") + 1
        vertical_padding = int(params.font_size * 0.35)
        text_clip_margin_y = max(
            int(params.font_size * 0.3), int(params.stroke_width * 2)
        )
        # MoviePy reduzirá automaticamente a altura da caixa de texto em `method=label`. Ao encontrar legendas com várias linhas,
        # Ao usar traços ou cores de fundo, é fácil cortar a metade inferior da última linha. Transmitido explicitamente aqui
        # Uma altura mais conservadora, levando em consideração o espaçamento entre linhas e espaços em branco extras na parte superior e inferior, para garantir legendas
        # Tanto o quadro de fundo quanto o próprio texto podem ser totalmente renderizados.
        clip_h = int(txt_height + vertical_padding + (interline * line_count))

        if rounded_bg_enabled:
            # O fundo arredondado precisa se ajustar à largura do texto, em vez de ocupar 90% da largura do vídeo. Use-o aqui primeiro
            # O PIL mede a linha de texto mais longa e adiciona preenchimento horizontal para evitar preenchimento excessivamente largo em legendas curtas.
            try:
                font = ImageFont.truetype(font_path, params.font_size)
                text_w = max(
                    int(font.getbbox(line)[2] - font.getbbox(line)[0])
                    for line in wrapped_txt.split("\n")
                )
            except Exception as exc:
                logger.warning(
                    f"failed to measure subtitle text width, fallback to max width: {str(exc)}"
                )
                text_w = int(max_width)

            box_w = max(1, min(int(max_width), text_w + 2 * pad_x))
            radius = max(8, int(params.font_size * 0.4))
            text_clip = TextClip(
                text=wrapped_txt,
                font=font_path,
                font_size=params.font_size,
                color=params.text_fore_color,
                bg_color=None,
                stroke_color=params.stroke_color,
                stroke_width=params.stroke_width,
                interline=interline,
                size=(box_w, None),
                text_align="center",
                margin=(0, text_clip_margin_y),
            )
            clip_h = max(clip_h, text_clip.h)
            bg_clip = _rounded_subtitle_background_clip(
                width=box_w,
                height=clip_h,
                color=bg_color,
                alpha=140,
                radius=radius,
            )
            text_position = _get_visible_center_position(text_clip, box_w, clip_h)
            _clip = CompositeVideoClip(
                [bg_clip, text_clip.with_position(text_position)],
                size=(box_w, clip_h),
            )
        elif bg_color:
            size = (
                int(max_width),
                clip_h,
            )
            text_clip = TextClip(
                text=wrapped_txt,
                font=font_path,
                font_size=params.font_size,
                color=params.text_fore_color,
                bg_color=None,
                stroke_color=params.stroke_color,
                stroke_width=params.stroke_width,
                interline=interline,
                size=(int(max_width), None),
                text_align="center",
                margin=(0, text_clip_margin_y),
            )
            size = (size[0], max(size[1], text_clip.h))
            bg_clip = _rounded_subtitle_background_clip(
                width=size[0],
                height=size[1],
                color=bg_color,
                alpha=255,
                radius=0,
            )
            text_position = _get_visible_center_position(text_clip, size[0], size[1])
            _clip = CompositeVideoClip(
                [bg_clip, text_clip.with_position(text_position)],
                size=size,
            )
        else:
            size = (
                int(max_width),
                clip_h,
            )
            _clip = TextClip(
                text=wrapped_txt,
                font=font_path,
                font_size=params.font_size,
                color=params.text_fore_color,
                bg_color=None,
                stroke_color=params.stroke_color,
                stroke_width=params.stroke_width,
                interline=interline,
                size=size,
                text_align="center",
            )
        duration = subtitle_item[0][1] - subtitle_item[0][0]
        _clip = _clip.with_start(subtitle_item[0][0])
        _clip = _clip.with_end(subtitle_item[0][1])
        _clip = _clip.with_duration(duration)
        if params.subtitle_position == "bottom":
            _clip = _clip.with_position(("center", video_height * 0.95 - _clip.h))
        elif params.subtitle_position == "top":
            _clip = _clip.with_position(("center", video_height * 0.05))
        elif params.subtitle_position == "custom":
            # Ensure the subtitle is fully within the screen bounds
            margin = 10  # Additional margin, in pixels
            max_y = video_height - _clip.h - margin
            min_y = margin
            custom_y = (video_height - _clip.h) * (params.custom_position / 100)
            custom_y = max(
                min_y, min(custom_y, max_y)
            )  # Constrain the y value within the valid range
            _clip = _clip.with_position(("center", custom_y))
        else:  # center
            _clip = _clip.with_position(("center", "center"))
        return _clip

    # CompositeAudioClip.close() do MoviePy não fecha o AudioFileClip filho. Usado aqui
    # ExitStack contém explicitamente todos os leitores de arquivos brutos, garantindo sucesso, exceções de legendas, falhas de remix e
    # Caminhos como falha na gravação de vídeo podem liberar o subprocesso FFmpeg, especialmente para evitar que os arquivos do Windows sejam ocupados.
    with ExitStack() as clip_stack:
        source_video_clip = clip_stack.enter_context(
            _open_video_clip_quietly(video_path)
        )
        voice_source_clip = clip_stack.enter_context(AudioFileClip(audio_path))
        video_clip = source_video_clip
        audio_clip = voice_source_clip.with_effects(
            [afx.MultiplyVolume(params.voice_volume)]
        )

        def make_textclip(text):
            return TextClip(
                text=text,
                font=font_path,
                font_size=params.font_size,
            )

        if subtitle_path and os.path.exists(subtitle_path):
            sub = clip_stack.enter_context(
                SubtitlesClip(
                    subtitles=subtitle_path,
                    encoding="utf-8",
                    make_textclip=make_textclip,
                )
            )
            text_clips = []
            for item in sub.subtitles:
                clip = create_text_clip(subtitle_item=item)
                text_clips.append(clip)
            video_clip = CompositeVideoClip([video_clip, *text_clips])
            clip_stack.callback(video_clip.close)

        bgm_enabled = bgm_service.should_use_bgm(
            params.bgm_type, params.bgm_volume
        )
        if not bgm_enabled and params.bgm_type:
            # Todas as fontes BGM compartilham esta regra de curto-circuito. Não é possível analisar aleatoriamente ou quando o volume não é maior que 0
            # Arquivos personalizados também não podem carregar arquivos retornados por provedores para evitar IO e remixagem sem sentido.
            logger.info(
                f"skipping background music because volume is not positive: "
                f"type={params.bgm_type}, volume={params.bgm_volume}"
            )

        # A trilha sonora do provedor pode ser transferida diretamente para o arquivo correspondente da camada de orquestração de tarefas. Nenhum significa usar aleatório/personalizado
        # Análise de BGM, uma string vazia desabilita explicitamente esta BGM; mas qualquer fonte deve passar primeiro pelas regras gerais de volume.
        bgm_file = ""
        if bgm_enabled:
            bgm_file = (
                bgm_file_override
                if bgm_file_override is not None
                else get_bgm_file(
                    bgm_type=params.bgm_type,
                    bgm_file=params.bgm_file,
                )
            )
        bgm_mix_succeeded = True
        if bgm_file:
            try:
                bgm_effects = [
                    afx.MultiplyVolume(params.bgm_volume),
                    afx.AudioFadeOut(3),
                ]
                # A música aleatória/personalizada analisada no serviço pode ser mais curta que o filme final e precisa ser repetida; a camada de tarefa
                # O arquivo transmitido via substituição indica que o provedor concluiu a adaptação da duração. Aqui está a base
                # A origem do arquivo determina se o ciclo deve ser feito, para evitar a modificação da lista de permissões de nomes sempre que um provedor for adicionado no futuro.
                if bgm_file_override is None:
                    bgm_effects.append(afx.AudioLoop(duration=video_clip.duration))
                bgm_source_clip = clip_stack.enter_context(AudioFileClip(bgm_file))
                bgm_clip = bgm_source_clip.with_effects(bgm_effects)
                audio_clip = CompositeAudioClip([audio_clip, bgm_clip])
            except Exception:
                bgm_mix_succeeded = False
                # Grave a pilha completa e o contexto estável para distinguir facilmente entre decodificação de arquivos, efeitos MoviePy e
                # CompositeAudioClip falhou; o conteúdo do arquivo e a chave da API não serão inseridos no log.
                logger.exception(
                    f"failed to mix background music: type={params.bgm_type}, "
                    f"file={bgm_file}"
                )

        final_video_clip = video_clip.with_audio(audio_clip)
        clip_stack.callback(final_video_clip.close)
        # Use explicitamente a taxa de amostragem do áudio de entrada; se não puder ser obtido, volte para o padrão de 44100 Hz do MoviePy.
        # Isso pode reduzir as flutuações na qualidade do som causadas pela reamostragem em diferentes ambientes, especialmente no Docker.
        output_audio_fps = int(getattr(audio_clip, "fps", 0) or 44100)
        _write_videofile_with_codec_fallback(
            final_video_clip,
            output_file=output_file,
            codec=_get_configured_video_codec(),
            audio_codec=audio_codec,
            audio_fps=output_audio_fps,
            audio_bitrate=audio_bitrate,
            temp_audiofile_path=_get_temp_audio_dir(output_dir),
            threads=params.n_threads or 2,
            logger=None,
            fps=fps,
        )
        return bgm_mix_succeeded


def preprocess_video(materials: List[MaterialInfo], clip_duration=4):
    # WebUI pode passar uma lista de materiais vazia em alguns cenários de geração secundária. Aqui, ele retorna um resultado vazio diretamente para evitar lançar exceções NoneType.
    if not materials:
        return []

    # Somente materiais que passam na verificação de pré-processamento são devolvidos para evitar que imagens de baixa resolução entrem no processo de síntese de vídeo subsequente.
    valid_materials = []
    local_videos_dir = utils.storage_dir("local_videos", create=True)

    for material in materials:
        if not material.url:
            continue

        try:
            material_source_path = file_security.resolve_path_within_directory(
                local_videos_dir, material.url
            )
        except ValueError as exc:
            # O caminho do material video_source local vem dos parâmetros da API e deve ser restrito ao diretório de material dedicado.
            # Os usuários podem passar nomes de arquivos e também são compatíveis com caminhos absolutos retornados pelo histórico, mas não podem escapar para o sistema.
            # Outros diretórios para evitar leitura arbitrária de arquivos ou detecção de arquivos confidenciais locais via MoviePy.
            logger.warning(
                f"skip unsafe local material: {material.url}, "
                f"local_videos_dir: {local_videos_dir}, error: {str(exc)}"
            )
            continue

        ext = utils.parse_extension(material_source_path)
        try:
            # Os materiais de imagem são lidos diretamente como imagens para evitar erros de julgamento do VideoFileClip e desencadear ramificações alternativas instáveis.
            if ext in const.FILE_TYPE_IMAGES:
                clip, material_source_path = _open_image_clip_with_fallback(
                    material_source_path
                )
            else:
                clip = _open_video_clip_quietly(material_source_path)
        except Exception:
            # Ele retornará ao modo de imagem quando houver uma extensão fora do padrão ou a detecção falhar, o que é compatível com a situação histórica de upload direto do caminho da imagem local.
            try:
                clip, material_source_path = _open_image_clip_with_fallback(
                    material_source_path
                )
            except Exception as exc:
                logger.warning(
                    f"skip unreadable local material: {material.url}, error: {str(exc)}"
                )
                continue
        try:
            width = clip.size[0]
            height = clip.size[1]
            if not is_material_resolution_acceptable(width, height):
                logger.warning(
                    f"low resolution material: {width}x{height}, minimum "
                    f"{_MIN_MATERIAL_DIMENSION}x{_MIN_MATERIAL_DIMENSION} required "
                    f"(tolerance {_MIN_DIMENSION_TOLERANCE}px)"
                )
                # Feche o recurso imediatamente após detectar material de baixa resolução e não devolva o material para processos subsequentes.
                close_clip(clip)
                continue

            if ext in const.FILE_TYPE_IMAGES:
                logger.info(f"processing image: {material_source_path}")
                # O material foi aberto uma vez ao detectar o tamanho. Aqui, o identificador de detecção é liberado primeiro e, em seguida, o clipe de imagem para exportação é recriado.
                close_clip(clip)
                # Create an image clip and set its duration to 3 seconds
                clip = (
                    ImageClip(material_source_path)
                    .with_duration(clip_duration)
                    .with_position("center")
                )
                # Apply a zoom effect using the resize method.
                # A lambda function is used to make the zoom effect dynamic over time.
                # The zoom effect starts from the original size and gradually scales up to 120%.
                # t represents the current time, and clip.duration is the total duration of the clip (3 seconds).
                # Note: 1 represents 100% size, so 1.2 represents 120% size.
                zoom_clip = clip.resized(
                    lambda t: 1 + (clip_duration * 0.03) * (t / clip.duration)
                )

                # Optionally, create a composite video clip containing the zoomed clip.
                # This is useful when you want to add other elements to the video.
                final_clip = CompositeVideoClip([zoom_clip])

                # Output the video to a file.
                video_file = f"{material_source_path}.mp4"
                final_clip.write_videofile(video_file, fps=30, logger=None)
                close_clip(clip)
                close_clip(final_clip)
                material.url = video_file
                logger.success(f"image processed: {video_file}")
            else:
                # Os materiais de vídeo comuns só precisam ler o tamanho para verificação e liberar a alça imediatamente após a conclusão da verificação.
                close_clip(clip)
                # Update url to the resolved absolute path so that downstream
                # stages (combine_videos) can open the file without re-resolving.
                material.url = material_source_path
        except Exception:
            close_clip(clip)
            raise

        valid_materials.append(material)

    return valid_materials
