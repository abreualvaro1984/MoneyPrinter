# Agentes MoneyPrinter

Documentação permanente para humanos e para o Cursor. **Leia antes de implementar.**

| Arquivo | Papel |
|---------|--------|
| [product-owner.md](product-owner.md) | Visão, regras de negócio, áreas separadas |
| [architect.md](architect.md) | Stack, pastas, convenções, credenciais |
| [trends-researcher.md](trends-researcher.md) | Trends + nichos com sinais YT |
| [scriptwriter.md](scriptwriter.md) | Roteiro humano + anti-IA (Gemini) |
| [video-planner.md](video-planner.md) | Plano de vídeo (roteiro/assets/voz/dub) |
| [clip-director.md](clip-director.md) | Cortes YT / locais (futuro) |
| [visual-matcher.md](visual-matcher.md) | Imagem no momento certo (futuro) |
| [publisher.md](publisher.md) | Contas, metadados, upload |

Skill Cursor do painel: [`.cursor/skills/moneyprinter-panel/SKILL.md`](../.cursor/skills/moneyprinter-panel/SKILL.md)

## Como usar

1. Abra [`roadmap.md`](../roadmap.md) e veja o que está em andamento.
2. Leia o agent da área que vai tocar.
3. Implemente **só aquela área** (sem misturar Trends + Create numa ação).
4. Ao terminar, marque `[x]` no `roadmap.md`.

## Regra de ouro

Cada capacidade do produto = **área de UI + módulo de código** separados.  
Nunca um único botão “gerar tudo”.
