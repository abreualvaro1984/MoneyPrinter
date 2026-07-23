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
- Score anti-IA + status (pass / review / regen)

## Estilo

- Conversacional, varia ritmo, evita clichês de LLM (“Neste vídeo vamos explorar…”, “É importante ressaltar…”).
- Parecer texto autoral de criador BR.

## Anti-IA

- API configurável (`GPTZERO_API_KEY` ou similar em `panel/.env`).
- Sem chave: heurística local simples + aviso “detector não configurado”.
- Score alto de IA → oferecer regenerar com prompt anti-detecção.

## UI

Área `/roteiros/` — lista, detalhe editável, gerar / regenerar / score.
