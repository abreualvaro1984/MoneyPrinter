# Clip Director (futuro)

## Missão

Analisar um vídeo (YouTube ou arquivo local), entender contexto e propor **cortes** coerentes — sem quebrar frases/cenas no meio.

## Abordagem prevista

1. Download (`yt-dlp`) ou path local
2. Transcrição (Whisper)
3. Visão/contexto (TwelveLabs Pegasus em `app/services/twelvelabs.py` quando habilitado)
4. LLM escolhe janelas start/end com justificativa
5. FFmpeg renderiza clips

## UI

Área **Cortes** — source URL ou pasta local → propostas → aprovar → render.

## Status

Fora do MVP. Ver `roadmap.md` itens 2 e 3.
