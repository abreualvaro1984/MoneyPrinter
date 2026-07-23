# Product Owner — MoneyPrinter

## Visão

Fábrica pessoal multi-nicho: descobrir o que está bombando, escrever roteiros humanos, gerar/cortar vídeos e publicar em várias contas (YouTube, TikTok, IG, Facebook, Kwai), **uma conta (ou mais) por nicho**.

Não é SaaS (por enquanto): uso local / WSL, painel Django.

## Regras de negócio

1. **Áreas separadas** — Trends, Roteiros, Cortes, Create, Contas, Publicar. Cada uma faz uma coisa.
2. **Nicho primeiro** — operação relevante exige nicho escolhido (nome, briefing, keywords, voz, aspect).
3. **Antes de gerar vídeo (item 8)** — obrigatório nicho + plataforma(s) destino; respeitar presets da plataforma.
4. **Trends não gera vídeo** — só pesquisa e sugere temas; “Usar este tema” abre Roteiros.
5. **Roteiros não renderizam sozinhos** — usuário aprova e escolhe o próximo passo.
6. **Contas agrupadas por nicho** — várias contas por plataforma permitidas.
7. **Credenciais na UI** — YouTube Data API e IAs em `/apis/` (banco); usuário não precisa editar `.env` no dia a dia.
8. **Idioma** — produto e UI em português (pt-BR).
9. **Roadmap vivo** — concluir entrega = marcar checkbox em `roadmap.md`.

## MVP atual

Itens **1** (Trends) e **5** (Roteiro humano + anti-IA Gemini) + shell UI + nichos com sinais YouTube reais + `/apis/` (YT + LLMs com Testar).

## Fora de escopo imediato

Clip com visão, create contextual, gate de plataforma no render, polish SEO avançado — ver `roadmap.md`.
