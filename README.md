# BOT AFILIADO — serviço local

Motor local de ofertas com Shopee assistida, Amazon Creators API BR, Awin Product Feed e Mercado Livre assistido, curadoria determinística, Telegram, tracking, conversões, analytics, hub e criativos para Instagram/TikTok. Começa em `DRY_RUN=true`, funciona sem LLM e não publica em redes sociais automaticamente.

O P1 gera:

- catálogo pesquisável em `/offers` e página consciente em `/o/{slug}`;
- carrossel 1080×1080, Story 1080×1920, thumbnails e storyboards;
- Reels de 18 s e rascunho TikTok de 15 s quando `FFMPEG_PATH` está disponível;
- copy, roteiro, legenda, hashtags e pacote Telegram com URL `/go` específicos por canal;
- fila local de revisão/publicação, sem OAuth ou chamada externa.

O P2 adiciona adapters selecionáveis por `--adapter`: Awin lê CSV/gzip oficial e Mercado Livre preserva links gerados manualmente pelos meios oficiais. O contrato Amazon Creators API está coberto por mocks, mas o uso real e a importação operacional permanecem bloqueados até aprovação escrita e desenho de retenção compatível com os termos brasileiros.

O P3 adiciona painel administrativo, autenticação Basic fail closed quando exposto, analytics segmentado e feedback determinístico com volume mínimo. O hub público continua em `/offers`; painel e `/api/*` ficam separados.

## Notebook Acer com Lubuntu

O alvo local é um i3-6100U com 4 GB de RAM, HDD e sem GPU. O bot não depende mais de Qwen/Ollama: sem IA configurada ele usa templates determinísticos. Quando `GEMINI_API_KEY` estiver presente, Gemini é o provider remoto principal; Granite GGUF via llama.cpp existe apenas como fallback local experimental e permanece desligado por padrão (`AI_LOCAL_ENABLED=false`) até ser homologado nesse hardware. Nenhum provider pode controlar preços, descontos, estoque, URLs, tracking, compliance ou publicação.

No notebook, após autenticar o Git para acessar o repositório:

```bash
git clone https://github.com/iagoluch/bot-afiliado.git
cd bot-afiliado
bash scripts/install.sh
bash scripts/test.sh
bash scripts/start.sh worker
```

Em outro terminal, `bash scripts/start.sh web` inicia a interface apenas em `127.0.0.1:8000`. A instalação não baixa modelos. A configuração de IA, systemd, status e recuperação está em [RUNBOOK.md](RUNBOOK.md). Para o comando **“Traga o repositório para esse notebook”**, o contexto operacional de migração está em [AGENTS.md](AGENTS.md).

Consulte [RUNBOOK.md](RUNBOOK.md) para executar, [STATUS.md](STATUS.md) para limites atuais e [COMPLIANCE.md](COMPLIANCE.md) antes de usar conteúdo real.
