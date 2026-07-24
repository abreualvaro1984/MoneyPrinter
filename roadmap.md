# Roadmap MoneyPrinter

Fonte da verdade do produto. Ao concluir uma entrega, marque `[x]` e atualize a data.

**Última sync:** 2026-07-24 — Plano de vídeo (`/planos/`); nichos com histórico/Add in-place; cancel wait.

**Decisões:** UI Django + HTMX (tema escuro colorido) · MVP = itens 1 + 5 + Plano · áreas separadas (nunca um botão único “fazer tudo”).

Contexto: [`agents/`](agents/) · skill painel: [`.cursor/skills/moneyprinter-panel/SKILL.md`](.cursor/skills/moneyprinter-panel/SKILL.md)

---

## Fase 0 — Memória do projeto

- [x] `roadmap.md` criado
- [x] Pasta `agents/` com papéis documentados
- [x] Regra Cursor `.cursor/rules/moneyprinter-context.mdc` (alwaysApply)
- [x] Skill do painel `.cursor/skills/moneyprinter-panel/SKILL.md`

---

## Fase 1 — Shell UI (item 7 parcial)

- [x] App `panel/ui` com layout dark colorido + nav por áreas
- [x] Login Django + SEO básico (`lang=pt-BR`, title/meta por página)
- [x] Placeholders para Cortes / Create / Contas / Publicar

---

## Item 1 — Trends multi-plataforma (MVP)

Área **Trends** — só pesquisa e sugere temas; **não** gera vídeo.

- [x] Formulário: nicho obrigatório + plataformas
- [x] YouTube: pesquisa por keywords/views + consolidação LLM
- [x] TikTok / Instagram / Facebook / Kwai: discovery com fallback honesto (heuristic) se API faltar
- [x] Cards de temas + ação **Usar este tema** → cria rascunho de roteiro (sem render)
- [x] Ordenar/enriquecer por **view_count** real (YouTube statistics)
- [x] IA recomenda **adicionar** ou **pular** (`recommendation` + `heat_score`)
- [x] Cadastro de várias APIs de IA (`/apis/`) e seleção na pesquisa

---

## Item 5 — Roteiro humano + anti-IA (MVP)

Área **Roteiros** — gera/edita texto; **não** enfileira render sozinha.

- [x] Gerar roteiro a partir de trend/tema + briefing do nicho (tom humano PT-BR)
- [x] Editar / versionar rascunhos no painel
- [x] Score anti-IA (Gemini / Google AI Studio; fallback GPTZero ou heurística local) exibido no card
- [x] Regenerar se score indicar texto “muito IA”

---

## Item 9 — Plano de vídeo (MVP parcial)

Área **Plano** (`/planos/`) — planeja e edita; **não** enfileira Create/Dub.

- [x] Formulário: nicho + formato + tema opcional + IA
- [x] IA sugere roteiro editável, assets (stock vs gravado conforme formato), voz TTS
- [x] Sugestões simples de dublagem (vídeos gringos / query)
- [x] Salvar / regenerar / enviar roteiro para área Roteiros
- [x] Histórico de planos com IA usada
- [ ] Enfileirar Create/Dub a partir do plano (futuro)

---

## Item 2 — Cortes YouTube com visão (futuro)

- [ ] Download + análise visual/contextual (TwelveLabs / Whisper)
- [ ] Cortes com contexto contínuo (não quebrados)
- [ ] Área **Cortes** separada na UI

---

## Item 3 — Cortes de vídeos locais (futuro)

- [ ] Mesmo pipeline do item 2 com source = pasta local
- [ ] Biblioteca de arquivos locais por nicho

---

## Item 4 — Create com imagens no momento certo (futuro)

- [ ] Alinhar trechos do roteiro ↔ busca de stock (Pexels/Pixabay/etc.)
- [ ] Ex.: fala “Jesus” → frame coerente com a narração

---

## Item 6 — Contas agrupadas por nicho (parcial / futuro UI)

- [x] Modelo `SocialAccount` com FK opcional para `Niche` (`panel/publishing`)
- [x] UI amigável de contas + tutorial por plataforma (`/contas/`)
- [x] Descoberta de nichos/subnichos pela IA com botão Add (SQLite)
- [x] Descoberta ancorada em sinais reais YouTube (mostPopular BR + buscas recentes por views)
- [x] Filtro de **formato de vídeo** na descoberta (dark / dormir / tela preta / ambiente / aparecendo / híbrido / tela / qualquer) com validação da IA
- [x] Add de sugestão **sem sair da lista** (HTMX) + botão Detalhes
- [x] Histórico de pesquisas de nichos com IA usada
- [x] YouTube API key digitável na UI `/apis/` (banco; `.env` só fallback)
- [x] Botão **Testar** YouTube API key (chamada barata mostPopular BR)
- [x] Cadastro de API de IA só com key (ChatGPT, Gemini, Grok, Kimi, DeepSeek, Z.AI)
- [x] Botão Testar API (prompt mínimo) no cadastro/lista de IAs
- [x] Skill Cursor do painel (`.cursor/skills/moneyprinter-panel/`)

---

## Item 7 — UI gráfica + SEO (contínuo)

- [x] Shell dark colorido (Fase 1)
- [x] Overlay “IA pensando” com botão **Parar / cancelar** (Esc também cancela)
- [ ] Polish visual, motion leve, acessibilidade
- [ ] SEO contínuo nas páginas públicas/operacionais

---

## Item 8 — Gate nicho + plataforma antes de gerar (futuro)

- [ ] Obrigatório escolher nicho + plataforma(s) antes do render
- [ ] Presets por plataforma (duração, aspect, metadados, regras de conteúdo)

---

## Critério MVP “feito”

- [x] Trends YT ponta a ponta na UI
- [x] Roteiro gerado + editável + score anti-IA (Gemini)
- [x] Nichos com sinais reais YouTube + API key na UI
- [x] Plano de vídeo (planejar/editar sem render)
- [x] Checks deste arquivo atualizados a cada entrega
