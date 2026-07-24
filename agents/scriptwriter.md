# Scriptwriter

## Missão

Escrever roteiros **humanos** e **densos** em pt-BR a partir de um tema + pesquisa recente (vídeos quentes + artigos). Passar por checagem anti-IA. Não renderizar vídeo.

## Entradas

- Nicho + tema (vindo de Trends ou digitado)
- Credencial de IA (`LlmCredential`) escolhida na UI — independente da IA usada na pesquisa de nicho/trends
- Duração alvo / paragraph_number do nicho
- Tom do briefing
- YouTube Data API (vídeos ≤90 dias) + Google News RSS (PT-BR e EN)

## Pesquisa antes de escrever

1. Buscar vídeos com mais views sobre o tema (PT + EN), só dos últimos **90 dias**.
2. Buscar artigos/notícias (revistas/portais) em PT-BR e outras línguas (RSS).
3. Usar título + descrição dos vídeos como “entendimento” do ângulo (sem inventar transcript).
4. Prompt exige: fatos concretos, easter egg, aposta/opinião explícita, validação do que foi conferido, claims para o humano checar.
5. Evidências ficam em `ScriptDraft.ai_raw.research` + nota curta em `notes`.

## Saídas

- Título sugerido
- Roteiro completo (falado / narração)
- Hooks, CTA, hashtags sugeridas
- Lista de fontes usadas + claims_to_verify
- Score anti-IA via Gemini (Google AI Studio; fallback GPTZero ou heurística) + status (pass / review / regen)

## Estilo

- Conversacional, varia ritmo, evita clichês de LLM (“Neste vídeo vamos explorar…”, “É importante ressaltar…”).
- Parecer texto autoral de criador BR. Preferir específico a genérico.
- **Nomes:** sem iniciais (proibido RDJ etc.); usar como o público BR chama pessoa/obra/aparelho (não tradução automática literal).
- **Duração:** usuário escolhe alvo em segundos; roteiro calibra volume (~2.2–2.9 palavras/s).

## Anti-IA

- Principal: Google AI Studio (Gemini) via `GEMINI_API_KEY` em `panel/.env` ou credencial Gemini ativa em `/apis/`.
- Modelo default: `gemini-2.0-flash` (`GEMINI_DETECT_MODEL`). Key: https://aistudio.google.com/app/apikey
- Fallback opcional: `GPTZERO_API_KEY`; sem nenhum dos dois: heurística local + aviso.
- Score 0–100 (maior = mais IA). Status: pass &lt;45 · review 45–69 · regen ≥70.
- Botão **Humanizar p/ burlar anti-IA**: reescreve estilo sem mudar conteúdo + rescore (`scripts_humanize`).
- Score alto → também oferecer regenerar com pesquisa nova.

## UI

Área `/roteiros/` — lista, detalhe (fontes + variantes), **Sugerir temas (IA)** no formulário, gerar / regenerar / humanizar / score.

## Código

- `panel/ui/services/scripts.py`
- `panel/channels/youtube.py` (`published_after`, `relevance_language`)
