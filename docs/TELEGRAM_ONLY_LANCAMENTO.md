# Telegram-only: primeiro lançamento no Acer

Este modo publica **somente no Telegram**, com link afiliado original, sem site externo, /go, Instagram, TikTok ou FFmpeg no worker. O modo anterior continua compatível e desligado por padrão. Não há descoberta automática autenticada de produtos até liberação e homologação de APIs/feeds oficiais. Não use scraping nem links genéricos no lugar do link de comissão.

## 1. Preparação

No clone do Acer, com o worker parado:

```bash
git fetch origin
git switch codex/assisted-tech-collection-2026-10-11
git pull --ff-only
bash scripts/install.sh
bash scripts/test.sh
```

Insira no `.env`, **sem compartilhar ou commitar segredos**:

```dotenv
DRY_RUN=true
TELEGRAM_ONLY_MODE=true
TELEGRAM_REAL_ENABLED=false
TELEGRAM_MAX_POSTS_PER_HOUR=4
WORKER_SOURCE_MODE=database
WORKER_SOURCE_PATH=
TELEGRAM_BOT_TOKEN=<token real do BotFather>
TELEGRAM_CHAT_ID=@seu_grupo_publico
CHANNEL_VISIBILITY_JSON={"site":"public","telegram":"public","instagram":"public","tiktok":"public"}
# PUBLIC_BASE_URL pode permanecer http://127.0.0.1:8000; nao sera usado no Telegram-only
```

O bot precisa ter permissão de publicar no grupo público, registrado conforme os termos dos afiliados.

## 2. Simulação, sem mensagem real

Coloque **dados verdadeiros, atualizados e obtidos pelos portais oficiais**. Exemplo de formato (nao utilize URLs/valores ficticios para publicar):

```bash
.venv/bin/python -m app.cli add-offer \
  --adapter shopee \
  --external-id ID_REAL \
  --title "SSD NVMe 1TB" \
  --category "informatica" \
  --price 299.90 \
  --source-url "https://shopee.com.br/product/ID_LOJA/ID_REAL" \
  --affiliate-url "https://s.shopee.com.br/LINK_AFILIADO_REAL" \
  --image-url "https://down-br.img.susercontent.com/FOTO_ORIGINAL_REAL.jpg" \
  --stock-status IN_STOCK
```

Os campos `--rating`, `--sales-count`, `--commission-rate`, `--coupon` e `--shipping` são opcionais. Só informe valores verificados. A curadoria **pode rejeitar** ofertas sem sinais suficientes de qualidade e sem queda histórica; não invente avaliação, comissão ou cupom para atingir o score.

Para Mercado Livre, use `--adapter mercadolivre`, `--source-url` de uma página de produto oficial e `--affiliate-url` gerada pela **Barra/Gerador de Links**, incluindo URLs `https://meli.la/...` ou `https://mercadolivre.com/sec/...` quando fornecidas. O sistema não converte URL comum em URL de afiliado.

Com o worker em outro terminal:

```bash
bash scripts/start.sh worker
```

Para verificar:

```bash
.venv/bin/python -m app.cli runtime-status
.venv/bin/python -m app.cli events --limit 20
.venv/bin/python -m app.cli overview
```

Resultado esperado da simulação, para oferta aceita: `SIMULATED`. Nenhuma chamada externa ao Telegram ocorre com `DRY_RUN=true`. O modo `database` não abre CSV e não gera mídia promocional artificial.

## 3. Primeira publicação real, com autorização

Primeiro confirme que o link é comissionado, a oferta continua em estoque, o grupo é público e autorizado e a simulação não teve falhas.

Pare o worker com `Ctrl+C`. Altere:

```dotenv
DRY_RUN=false
TELEGRAM_ONLY_MODE=true
TELEGRAM_REAL_ENABLED=true
TELEGRAM_MAX_POSTS_PER_HOUR=1
WORKER_SOURCE_MODE=database
```

Reinsira uma **oferta atual e verdadeira** pelo comando `add-offer`. Itens simulados não devem ser promovidos automaticamente à fila real. Inicie o worker:

```bash
bash scripts/start.sh worker
```

Confira no Telegram se chegou exatamente uma mensagem, com foto oficial se aceita ou texto caso a mídia seja recusada. Confira o `message_id` em `events` e o histórico de publicações. A Bot API não garante idempotência de envio; se uma tentativa ficar em `PROCESSING` ou `NEEDS_RECONCILIATION`, **não reenvie cegamente**. Pare o worker e use `telegram-processing` e `telegram-reconcile` após conferir o canal.

Depois do piloto, para limitar volume, mantenha `TELEGRAM_MAX_POSTS_PER_HOUR` em 1–4. O limite é por publicações reais nas últimas 60 minutos e o worker drena a fila gradualmente.

## 4. O que as credenciais da Shopee/ML ainda não resolvem

- **Shopee:** integração de coleta automática não existe neste PR. Após receber AppId/Secret, homologar schema, assinatura, permissões e geração de link na Open API oficial antes de ativar conector.
- **Mercado Livre:** as APIs gerais de catálogo não são comprovadamente APIs de afiliado; não há API pública de geração de links comissionados confirmada. Até contrato autorizado, use links obtidos pela ferramenta oficial.
- **Vídeo de produto:** não existe fonte de vídeo oficial validada para esses adapters; não extraia vídeo das páginas nem republique sem permissão. Por ora a publicação usa foto original aprovada ou texto.
- **Mensuração:** sem site `/go`, os cliques locais deixam de ser coletados. Use etiquetas oficiais/SubID e relatórios do programa para medir conversões.

### Critério objetivo de entrega

Worker ativo em `database`, produto verdadeiro com link oficial, score elegível, publicação Telegram `PUBLISHED` confirmada pelo ID, sem duplicação. A coleta autônoma ainda depende do acesso/documentação externa. 
