# Roadmap MoneyPrinter

Fonte da verdade do produto. Ao concluir uma entrega, marque `[x]` e atualize a data.

**Decisões:** UI Django + HTMX (tema escuro colorido) · MVP = itens 1 + 5 · áreas separadas (nunca um botão único “fazer tudo”).

Contexto permanente dos agentes: [`agents/`](agents/).

---

## Fase 0 — Memória do projeto

- [x] `roadmap.md` criado
- [x] Pasta `agents/` com papéis documentados
- [x] Regra Cursor `.cursor/rules/moneyprinter-context.mdc` (alwaysApply)

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
- [x] Score anti-IA (GPTZero via `GPTZERO_API_KEY` ou heurística local) exibido no card
- [x] Regenerar se score indicar texto “muito IA”

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
- [ ] UI amigável de agrupamento nicho → várias contas/plataformas

---

## Item 7 — UI gráfica + SEO (contínuo)

- [x] Shell dark colorido (Fase 1)
- [ ] Polish visual, motion leve, acessibilidade
- [ ] SEO contínuo nas páginas públicas/operacionais

---

## Item 8 — Gate nicho + plataforma antes de gerar (futuro)

- [ ] Obrigatório escolher nicho + plataforma(s) antes do render
- [ ] Presets por plataforma (duração, aspect, metadados, regras de conteúdo)

---

## Critério MVP “feito”

- [x] Trends YT ponta a ponta na UI
- [x] Roteiro gerado + editável + score anti-IA
- [x] Checks deste arquivo atualizados a cada entrega
