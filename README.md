# Atlas de Leonida

Guias de GTA 6 (coletáveis, carros, armas, golpes, rotas de dinheiro) coletados automaticamente.

- `pipeline/run.py`: busca YouTube, Reddit e GTA Wiki, extrai fichas com o Claude, deduplica e confirma por múltiplas fontes.
- `.github/workflows/coletar.yml`: roda a cada 4 horas e salva `public/data/fichas.json`.
- `public/`: o site. A Vercel republica sozinha a cada atualização.

Secrets do repositório: `ANTHROPIC_API_KEY` (obrigatório), `YOUTUBE_API_KEY` (recomendado), `REDDIT_CLIENT_ID` e `REDDIT_CLIENT_SECRET` (opcionais).
Para controlar custo, mude `MAX_NOVOS` no workflow (fontes processadas por rodada).
