# Scriptwriter

## Missão

Escrever roteiros **humanos** em pt-BR a partir de um tema/trend + briefing do nicho. Passar por checagem anti-IA. Não renderizar vídeo.

## Entradas

- Nicho + tema (vindo de Trends ou digitado)
- Duração alvo / paragraph_number do nicho
- Tom do briefing

## Saídas

- Título sugerido
- Roteiro completo (falado / narração)
- Hooks, CTA, hashtags sugeridas
- Score anti-IA via Gemini (Google AI Studio; fallback GPTZero ou heurística) + status (pass / review / regen)

## Estilo

- Conversacional, varia ritmo, evita clichês de LLM (“Neste vídeo vamos explorar…”, “É importante ressaltar…”).
- Parecer texto autoral de criador BR.

## Anti-IA

- Principal: Google AI Studio (Gemini) via `GEMINI_API_KEY` em `panel/.env` ou credencial Gemini ativa em `/apis/`.
- Modelo default: `gemini-2.0-flash` (`GEMINI_DETECT_MODEL`). Key: https://aistudio.google.com/app/apikey
- Fallback opcional: `GPTZERO_API_KEY`; sem nenhum dos dois: heurística local + aviso.
- Score alto de IA → oferecer regenerar com prompt anti-detecção.

## UI

Área `/roteiros/` — lista, detalhe editável, gerar / regenerar / score.
