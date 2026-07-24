# Video planner

## Missão

Montar um **plano de vídeo** editável a partir de um nicho + formato: roteiro, assets (stock vs gravado), voz TTS e sugestões de dublagem. **Não** renderiza vídeo nem enfileira Create/Dub.

## Entradas

- Nicho (obrigatório)
- Formato (`video_formats.py`: dark, sleep, blackscreen, ambient, face, …)
- Tema opcional
- Credencial de IA (`LlmCredential`)

## Saídas

- Título + roteiro falado (editável)
- Voz Edge TTS sugerida + notas
- Lista de assets (`stock_*` / `recorded` / `blackscreen` / `broll`)
- 3–5 ideias de dublagem (URL ou query)
- Botão opcional: enviar roteiro para área **Roteiros**

## Código

- `panel/ui/services/video_plans.py`
- Modelo `VideoPlan`
- UI: `/planos/`

## Regras

- Áreas separadas: Plano ≠ Create ≠ Dub ≠ Roteiros.
- Faceless (dark/sleep/tela preta/ambiente): não sugerir `recorded`.
- Face/híbrido: priorizar takes gravados + B-roll.
