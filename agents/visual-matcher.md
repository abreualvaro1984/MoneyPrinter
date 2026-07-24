# Visual Matcher (futuro)

## Missão

No pipeline Create, alinhar **trechos da narração** a buscas de stock para que a imagem corresponda ao que está sendo dito (ex.: citar Jesus → frame coerente).

## Abordagem prevista

1. Segmentar roteiro por timestamps TTS
2. Extrair entidade/ação por segmento (LLM)
3. Buscar Pexels/Pixabay/Coverr com query específica
4. Fallback para keyword genérica do nicho

## Status

Fora do MVP. Ver `roadmap.md` item 4.
