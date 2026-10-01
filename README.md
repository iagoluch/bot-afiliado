# BOT AFILIADO — P0 a P3 local

Motor local de ofertas com Shopee assistida, Amazon Creators API BR, Awin Product Feed e Mercado Livre assistido, curadoria determinística, Telegram, tracking, conversões, analytics, hub e criativos para Instagram/TikTok. Começa em `DRY_RUN=true`, funciona sem LLM e não publica em redes sociais automaticamente.

O P1 gera:

- catálogo pesquisável em `/offers` e página consciente em `/o/{slug}`;
- carrossel 1080×1080, Story 1080×1920, thumbnails e storyboards;
- Reels de 18 s e rascunho TikTok de 15 s quando `FFMPEG_PATH` está disponível;
- copy, roteiro, legenda, hashtags e pacote Telegram com URL `/go` específicos por canal;
- fila local de revisão/publicação, sem OAuth ou chamada externa.

O P2 adiciona adapters selecionáveis por `--adapter`: Awin lê CSV/gzip oficial e Mercado Livre preserva links gerados manualmente pelos meios oficiais. O contrato Amazon Creators API está coberto por mocks, mas o uso real e a importação operacional permanecem bloqueados até aprovação escrita e desenho de retenção compatível com os termos brasileiros.

O P3 adiciona painel administrativo, autenticação Basic fail closed quando exposto, analytics segmentado e feedback determinístico com volume mínimo. O hub público continua em `/offers`; painel e `/api/*` ficam separados.

Consulte [RUNBOOK.md](RUNBOOK.md) para executar, [STATUS.md](STATUS.md) para limites atuais e [COMPLIANCE.md](COMPLIANCE.md) antes de usar conteúdo real.
