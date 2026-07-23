from __future__ import annotations

"""Formatos de vídeo para descoberta de nichos (dark, dormir, tela preta, face…)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VideoFormatSpec:
    id: str
    label: str
    short: str
    """Instruções para a IA: o que procurar e como validar."""
    llm_rules: str
    """Queries extras de seed no YouTube (além das genéricas)."""
    seed_queries: tuple[str, ...]
    """Palavras/padrões que ajudam a validar fit no pós-processamento leve."""
    prefer_terms: tuple[str, ...]
    avoid_terms: tuple[str, ...]


VIDEO_FORMATS: dict[str, VideoFormatSpec] = {
    "dark": VideoFormatSpec(
        id="dark",
        label="Dark / sem aparecer",
        short="Voz + B-roll / stock / texto — você NÃO aparece (faceless).",
        llm_rules=(
            "FORMATO OBRIGATÓRIO: dark / faceless.\n"
            "- Só sugira nichos viáveis SEM o criador na câmera "
            "(narração, stock, IA voice, legendas, slideshow, compilação).\n"
            "- REJEITE nichos que dependem de vlog, reação facial, "
            "opinião em talking-head, dance, ou presença pessoal.\n"
            "- Em cada nicho preencha format_fit (0-100) e format_ok (true/false) "
            "explicando como produzir SEM aparecer.\n"
            "- keywords devem servir para achar vídeos dark/faceless (não 'vlog meu dia')."
        ),
        seed_queries=(
            "curiosidades narradas shorts",
            "fatos históricos narrados",
            "top 10 curiosidades brasil",
            "histórias reais narradas",
            "reddit stories português",
            "misterios narrados",
            "finanças explicadas animação",
            "ciência curiosidades narradas",
            "canal dark facts",
            "página dark curiosidades",
        ),
        prefer_terms=(
            "narrad",
            "curiosidade",
            "história",
            "fatos",
            "top ",
            "mister",
            "compil",
            "explicad",
            "shorts",
            "dark",
        ),
        avoid_terms=("vlog", "reagindo", "meu dia", "grwm", "dance", "dance challenge"),
    ),
    "sleep": VideoFormatSpec(
        id="sleep",
        label="Canal para dormir",
        short="Áudio calmo, chuva, histórias longas, ASMR sleep — para dormir/relaxar.",
        llm_rules=(
            "FORMATO OBRIGATÓRIO: canal para dormir / sleep content.\n"
            "- Só nichos cujo valor é relaxar, adormecer ou acompanhar a noite "
            "(chuva, ruído branco, soft voice, bedtime stories, sleep meditation, "
            "histórias longas em voz baixa, piano/lofi sleep).\n"
            "- Duração típica: longform (horas) ou loops; sem gancho gritante de Shorts.\n"
            "- SEM aparecer na câmera (ou só ambiência/visual abstrato).\n"
            "- REJEITE: vlog, humor alto, polêmica, dance, reaction, clicksbait agitado.\n"
            "- format_fit/format_ok + format_notes: tipo de áudio/visual e por que ajuda a dormir.\n"
            "- keywords devem achar canais sleep/bedtime (não 'curiosidades shorts')."
        ),
        seed_queries=(
            "chuva para dormir 10 horas",
            "histórias para dormir narradas",
            "ruído branco para dormir",
            "meditação para dormir",
            "soft spoken sleep story",
            "canal para dormir português",
            "piano para dormir",
            "lofi rain sleep",
            "ASMR sleep no talking",
            "bedtime stories adults",
            "ondas do mar para dormir",
            "voz calma para dormir",
        ),
        prefer_terms=(
            "dormir",
            "sleep",
            "chuva",
            "ruído",
            "medita",
            "bedtime",
            "soft",
            "relax",
            "ondas",
            "hora",
            "asmr",
        ),
        avoid_terms=(
            "vlog",
            "reagindo",
            "polêmica",
            "dance",
            "shorts engraçado",
            "top 10 chocante",
        ),
    ),
    "blackscreen": VideoFormatSpec(
        id="blackscreen",
        label="Tela preta",
        short="Tela preta / escura + áudio (chuva, estudo, sleep, podcast só áudio).",
        llm_rules=(
            "FORMATO OBRIGATÓRIO: tela preta / black screen.\n"
            "- Nichos onde o vídeo é essencialmente TELA PRETA ou quase sem imagem "
            "(black screen rain, dark screen study, black screen sleep, podcast áudio-only).\n"
            "- O produto é o ÁUDIO; a imagem é preta/minimalista de propósito "
            "(economia de bateria, foco, dormir sem luz).\n"
            "- SEM rosto, SEM B-roll colorido obrigatório, SEM slideshow cheio de cortes.\n"
            "- REJEITE: vlog, dark page com stock animado, talking-head, memes visuais.\n"
            "- format_fit/format_ok + format_notes: o que toca no áudio e por que a tela fica preta.\n"
            "- keywords: 'black screen', 'tela preta', 'dark screen', etc."
        ),
        seed_queries=(
            "black screen rain for sleep",
            "tela preta chuva para dormir",
            "black screen white noise",
            "dark screen study with me",
            "tela preta para estudar",
            "black screen podcast",
            "black screen soft spoken",
            "tela preta meditação",
            "black screen thunder rain",
            "audio only black screen",
            "tela preta 10 horas",
            "black screen lofi",
        ),
        prefer_terms=(
            "tela preta",
            "black screen",
            "dark screen",
            "chuva",
            "ruído",
            "sleep",
            "study",
            "áudio",
            "audio",
        ),
        avoid_terms=("vlog", "reagindo", "stock footage", "animação", "dance", "compilação visual"),
    ),
    "ambient": VideoFormatSpec(
        id="ambient",
        label="Ambiente / loop visual",
        short="Loops longos: lareira, aquário, cidade à noite, café — sem falar na câmera.",
        llm_rules=(
            "FORMATO OBRIGATÓRIO: ambiente / visual loop (fireplace, aquarium, city night, café).\n"
            "- Nichos de vídeo longo em loop para fundo (trabalho, estudo, relax).\n"
            "- Pouca ou nenhuma narração; se houver áudio, é ambiente/musica.\n"
            "- SEM aparecer; REJEITE talking-head e Shorts agitados.\n"
            "- format_fit/format_ok + o tipo de loop/visual."
        ),
        seed_queries=(
            "lareira lareira crackling 10 hours",
            "aquarium for cats",
            "coffee shop ambience",
            "cidade à noite ambience",
            "rainy window ambience",
            "espaço stars ambience",
            "biblioteca ambience study",
            "loop visual relaxante",
        ),
        prefer_terms=(
            "ambience",
            "ambiente",
            "loop",
            "hours",
            "lareira",
            "chuva",
            "café",
            "relax",
        ),
        avoid_terms=("vlog", "reagindo", "opinião", "dance", "tutorial excel"),
    ),
    "face": VideoFormatSpec(
        id="face",
        label="Aparecendo (fala pra câmera)",
        short="Você aparece: talking-head, opinião, vlog, reação.",
        llm_rules=(
            "FORMATO OBRIGATÓRIO: criador APARECE na câmera.\n"
            "- Só sugira nichos onde presença, carisma ou opinião pessoal "
            "são o diferencial (talking-head, reação, vlog, dicas face-to-camera).\n"
            "- REJEITE nichos tipicamente dark/faceless (só narração + stock "
            "sem necessidade de rosto), sleep e tela preta.\n"
            "- Em cada nicho: format_fit, format_ok e como o rosto/presença agrega.\n"
            "- keywords devem achar canais onde a pessoa aparece."
        ),
        seed_queries=(
            "vlog brasil shorts",
            "opinião polêmica shorts",
            "reagindo shorts brasil",
            "dicas falando pra câmera",
            "storytime brasil",
            "coach motivacional shorts",
            "review produto falando",
            "relacionamento conselhos shorts",
        ),
        prefer_terms=(
            "vlog",
            "reagindo",
            "opinião",
            "storytime",
            "falando",
            "eu ",
            "meu ",
            "review",
        ),
        avoid_terms=(
            "narrado",
            "stock footage",
            "sem aparecer",
            "faceless",
            "dark page",
            "tela preta",
            "black screen",
            "para dormir",
        ),
    ),
    "hybrid": VideoFormatSpec(
        id="hybrid",
        label="Híbrido",
        short="Mistura: você aparece em trechos + B-roll / cortes.",
        llm_rules=(
            "FORMATO OBRIGATÓRIO: híbrido (face + B-roll).\n"
            "- Nichos onde o criador aparece em hooks/CTA mas o miolo "
            "pode ser stock, tela ou clips.\n"
            "- Evite extremos: 100% faceless, sleep, tela preta ou 100% vlog.\n"
            "- format_fit/format_ok: diga o que é face e o que é B-roll."
        ),
        seed_queries=(
            "explicando com cortes shorts",
            "dicas com b-roll brasil",
            "tutorial falando e tela",
            "finanças shorts explicando",
            "produtividade tips shorts",
        ),
        prefer_terms=("explicando", "dicas", "tutorial", "cortes", "shorts"),
        avoid_terms=("tela preta", "black screen", "para dormir 10 horas"),
    ),
    "screen": VideoFormatSpec(
        id="screen",
        label="Tela / tutorial",
        short="Screen recording, slides, software, jogos com voz.",
        llm_rules=(
            "FORMATO OBRIGATÓRIO: tela / tutorial (screen capture, slides, gameplay+voz).\n"
            "- Nichos ensinando na tela: Excel, apps, código, jogos, PowerPoint.\n"
            "- Rosto opcional; o valor está na tela (não é tela preta vazia).\n"
            "- REJEITE nichos que exigem só talking-head ou só black screen sleep.\n"
            "- format_fit/format_ok obrigatórios."
        ),
        seed_queries=(
            "tutorial excel shorts",
            "como usar app shorts",
            "gameplay dicas brasil",
            "photoshop tutorial shorts",
            "canva tutorial shorts",
            "programação shorts brasil",
        ),
        prefer_terms=("tutorial", "como usar", "excel", "app", "gameplay", "passo a passo"),
        avoid_terms=("vlog", "meu dia", "dance", "tela preta", "para dormir"),
    ),
    "any": VideoFormatSpec(
        id="any",
        label="Qualquer formato",
        short="Sem filtro — inclui dark, dormir, tela preta, face, etc.",
        llm_rules=(
            "FORMATO: qualquer. Ainda assim marque format_fit/format_ok "
            "sugerindo o formato mais natural do nicho "
            "(dark|sleep|blackscreen|ambient|face|hybrid|screen)."
        ),
        seed_queries=(),
        prefer_terms=(),
        avoid_terms=(),
    ),
}

DEFAULT_VIDEO_FORMAT = "dark"

VIDEO_FORMAT_CHOICES = tuple(
    (spec.id, f"{spec.label} — {spec.short}") for spec in VIDEO_FORMATS.values()
)


def get_video_format(format_id: str | None) -> VideoFormatSpec:
    key = (format_id or DEFAULT_VIDEO_FORMAT).strip().lower()
    return VIDEO_FORMATS.get(key) or VIDEO_FORMATS[DEFAULT_VIDEO_FORMAT]
