import numpy as np
from moviepy import Clip, ColorClip, CompositeVideoClip, vfx
from PIL import Image


# FadeIn
def fadein_transition(clip: Clip, t: float) -> Clip:
    return clip.with_effects([vfx.FadeIn(t)])


# FadeOut
def fadeout_transition(clip: Clip, t: float) -> Clip:
    return clip.with_effects([vfx.FadeOut(t)])


# SlideIn
def slidein_transition(clip: Clip, t: float, side: str) -> Clip:
    width, height = clip.size

    # O SlideIn integrado do MoviePy é instável para materiais de tela inteira na cadeia de processamento atual.
    # Haverá uma situação em que “a transição é aplicada logicamente, mas quase não há mudança no quadro”.
    # Aqui ele é alterado para fundo preto explícito + animação de deslocamento para garantir que o efeito de transição seja visível e o comportamento seja controlável.
    def position(current_time: float):
        progress = min(max(current_time / max(t, 0.001), 0), 1)

        if side == "left":
            return (-width + width * progress, 0)
        if side == "right":
            return (width - width * progress, 0)
        if side == "top":
            return (0, -height + height * progress)
        if side == "bottom":
            return (0, height - height * progress)
        return (0, 0)

    background = ColorClip(size=(width, height), color=(0, 0, 0)).with_duration(
        clip.duration
    )
    moving_clip = clip.with_position(position)
    return CompositeVideoClip([background, moving_clip], size=(width, height)).with_duration(
        clip.duration
    )


# SlideOut
def slideout_transition(clip: Clip, t: float, side: str) -> Clip:
    width, height = clip.size
    transition_start = max(clip.duration - t, 0)

    # SlideOut também foi alterado para deslocamento explícito para garantir que o final do clipe possa deslizar para fora da tela de forma estável.
    def position(current_time: float):
        if current_time <= transition_start:
            return (0, 0)

        progress = min(
            max((current_time - transition_start) / max(t, 0.001), 0), 1
        )

        if side == "left":
            return (-width * progress, 0)
        if side == "right":
            return (width * progress, 0)
        if side == "top":
            return (0, -height * progress)
        if side == "bottom":
            return (0, height * progress)
        return (0, 0)

    background = ColorClip(size=(width, height), color=(0, 0, 0)).with_duration(
        clip.duration
    )
    moving_clip = clip.with_position(position)
    return CompositeVideoClip([background, moving_clip], size=(width, height)).with_duration(
        clip.duration
    )


# Manter a faixa de zoom de 20% do design original proporciona uma sensação claramente visível do movimento de Ken Burns, mesmo em clipes curtos de cerca de três segundos.
# A estabilidade da escala é garantida pela amostragem central de subpixel abaixo, sem mascarar a cintilação da codificação do vídeo de origem, reduzindo a magnitude do efeito.
_ZOOM_MAX_SCALE = 1.2


def _zoom_frame(frame: np.ndarray, scale_factor: float) -> np.ndarray:
    """Use o corte central de subpixel para obter efeitos de zoom estáveis ​​e sem bordas pretas.

    Você não pode primeiro converter a largura e a altura do recorte em números inteiros: quando a proporção de escala muda continuamente, os limites dos números inteiros saltam em etapas diferentes.
    E ao alternar entre tamanhos ímpares e pares, a fase de amostragem de meio pixel é alterada, o que acaba se manifestando como tremulação da tela. EXTENSÃO DO TRAVESSEIRO
    A transformação pode receber diretamente limites de ponto flutuante e amostragem completa de subpixels na tela de saída fixa; limites esquerdo e direito, superior e inferior
    É sempre simétrico em torno do mesmo centro de ponto flutuante, por isso é adequado para cenas em que todo o vídeo continua a aumentar lentamente o zoom."""
    if scale_factor <= 0:
        raise ValueError("scale_factor must be greater than zero")

    # O zoom 1x retorna diretamente ao quadro original para evitar uma reamostragem sem sentido, causando um leve desfoque do primeiro quadro.
    if abs(scale_factor - 1.0) < 1e-9:
        return frame

    height, width = frame.shape[:2]
    crop_width = width / scale_factor
    crop_height = height / scale_factor
    left = (width - crop_width) / 2
    top = (height - crop_height) / 2
    right = left + crop_width
    bottom = top + crop_height

    image = Image.fromarray(frame)
    transformed = image.transform(
        (width, height),
        Image.Transform.EXTENT,
        (left, top, right, bottom),
        # O dimensionamento contínuo de vídeo presta mais atenção à consistência dos quadros adjacentes. BICUBIC/LANCZOS Embora o quadro único seja mais nítido,
        # No entanto, texturas de alta frequência são propensas a zumbidos e oscilações de brilho ao cruzar a grade de amostragem; BILINEAR é mais suave e
        # Uma pequena perda de nitidez pode ser trocada por uma aparência dinâmica mais estável.
        resample=Image.Resampling.BILINEAR,
    )
    return np.asarray(transformed)


def zoomin_transition(clip: Clip, t: float) -> Clip:
    """Aumente suavemente o zoom do original para 1,2x em todo o clipe."""
    # t está temporariamente reservado para manter uma assinatura de chamada unificada com outras funções de transição; o dimensionamento precisa cobrir todo o clipe,
    # Caso contrário, a imagem congelará repentinamente após um breve zoom, o que não é adequado para materiais estáticos ou de baixo movimento.
    _ = t
    duration = max(clip.duration, 0.001)

    def scale_effect(get_frame, current_time: float):
        progress = min(max(current_time / duration, 0), 1)
        scale_factor = 1 + (_ZOOM_MAX_SCALE - 1) * progress
        return _zoom_frame(get_frame(current_time), scale_factor)

    return clip.transform(scale_effect)


def zoomout_transition(clip: Clip, t: float) -> Clip:
    """Diminua suavemente o zoom de 1,2x para o quadro original em todo o clipe."""
    # Consistente com zoomin_transition, t é usado apenas para ser compatível com a interface de chamada de transição unificada.
    _ = t
    duration = max(clip.duration, 0.001)

    def scale_effect(get_frame, current_time: float):
        progress = min(max(current_time / duration, 0), 1)
        scale_factor = _ZOOM_MAX_SCALE - (_ZOOM_MAX_SCALE - 1) * progress
        return _zoom_frame(get_frame(current_time), scale_factor)

    return clip.transform(scale_effect)
