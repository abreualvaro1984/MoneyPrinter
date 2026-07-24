# Product Owner — MoneyPrinter

## Visão

Fábrica pessoal multi-nicho: descobrir o que está bombando, escrever roteiros humanos, gerar/cortar vídeos e publicar em várias contas (YouTube, TikTok, IG, Facebook, Kwai), **uma conta (ou mais) por nicho**.

Não é SaaS (por enquanto): uso local / WSL, painel Django.

## Regras de negócio

1. **Áreas separadas** — Trends, Roteiros, Plano, Cortes, Create, Contas, Publicar. Cada uma faz uma coisa.
2. **Nicho primeiro** — operação relevante exige nicho escolhido (nome, briefing, keywords, voz, aspect).
3. **Antes de gerar vídeo (item 8)** — obrigatório nicho + plataforma(s) destino; respeitar presets da plataforma.
4. **Trends não gera vídeo** — só pesquisa e sugere temas; “Usar este tema” abre Roteiros.
5. **Roteiros não renderizam sozinhos** — usuário aprova e escolhe o próximo passo.
6. **Plano não renderiza** — só planeja roteiro/assets/voz/dublagem; Create/Dub ficam para depois.
7. **Contas agrupadas por nicho** — várias contas por plataforma permitidas.
8. **Credenciais na UI** — YouTube Data API e IAs em `/apis/` (banco); usuário não precisa editar `.env` no dia a dia.
9. **Idioma** — produto e UI em português (pt-BR).
10. **Roadmap vivo** — concluir entrega = marcar checkbox em `roadmap.md`.

## MVP atual

Itens **1** (Trends), **5** (Roteiro + anti-IA Gemini), **9** (Plano de vídeo) + nichos com sinais YouTube + `/apis/`.

## Fora de escopo imediato

Clip com visão, create contextual, enfileirar Create/Dub a partir do plano, gate de plataforma no render, polish SEO avançado — ver `roadmap.md`.
