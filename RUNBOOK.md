# Runbook P0 a P3 — Windows

## Instalação

Requer Python 3.11 ou superior. No CMD, a partir da raiz:

```bat
scripts\install.bat
copy .env.example .env
set DRY_RUN=true
set DATABASE_PATH=data\affiliate.db
set CREATIVES_PATH=data\creatives
```

O projeto carrega `.env` automaticamente a partir da raiz do repositório, inclusive quando o comando é iniciado em outro diretório. Variáveis já definidas no ambiente têm precedência sobre `.env`, e `.env` tem precedência sobre os padrões da aplicação. O arquivo aceita pares literais `KEY=VALUE`; não é executado como shell.

## P0: Telegram, tracking e analytics

```bat
.venv\Scripts\python.exe -m app.cli cycle examples\shopee_offers.sample.csv --campaign dry-run
.venv\Scripts\python.exe -m app.cli overview
scripts\start.bat
```

Abra `http://127.0.0.1:8000`. O `/go/{id}` registra o clique e preserva a URL afiliada. Para conversões:

O campo `original_price` do CSV manual não autoriza alegar desconto. A primeira importação publica apenas o preço atual; “De” e percentual aparecem somente depois de duas coletas anteriores em horários distintos registradas pelo sistema e de uma queda real frente ao menor preço anterior. Ao atualizar uma instalação legada, rascunhos não publicados que continham a alegação antiga são cancelados e devem ser gerados novamente.

```bat
.venv\Scripts\python.exe -m app.cli import-conversions caminho\relatorio.csv
```

O CSV de conversões exige `external_order_id` e `status`; pode informar `offer_id`, `click_id`, `merchant`, `network`, `value`, `commission`, `channel`, `campaign` e `timestamp`. Quando `click_id` está presente, o sistema preenche oferta, canal e campanha a partir do clique gravado. Se o relatório trouxer valores diferentes, a linha é rejeitada para evitar atribuição incorreta; corrija o mapeamento antes de reimportar. A chave `(network, external_order_id)` torna a reimportação idempotente. O formato final do export Shopee ainda precisa ser validado com relatório oficial real.

## P1: hub e criativos

Sem FFmpeg, o comando ainda gera carrossel, Story, thumbnails e storyboards; Reels fica `ASSET_PENDING` e mostra a ação necessária.

```bat
.venv\Scripts\python.exe -m app.cli p1-cycle examples\shopee_offers.sample.csv --campaign p1-manual
.venv\Scripts\python.exe -m app.cli social-queue
scripts\start.bat
```

Abra `/offers`, pesquise a oferta e acesse `/o/{slug}`. A página nunca redireciona automaticamente: o clique explícito em “Ir para a oferta” usa `/go/{id}`.

Para gerar MP4, instale FFmpeg separadamente e informe o executável:

```bat
set FFMPEG_PATH=C:\caminho\ffmpeg.exe
.venv\Scripts\python.exe -m app.cli p1-cycle examples\shopee_offers.sample.csv --campaign p1-video
```

Para validar o render sem instalação global, use o pacote de desenvolvimento opcional:

```bat
.venv\Scripts\python.exe -m pip install -r requirements-media-test.txt
scripts\test-media.bat
```

Instagram Feed, Story e Reels completos entram como `READY_FOR_PUBLISH`. Feed e Story continuam manuais. Somente Reel possui preparação Graph API nesta versão. TikTok fica `PENDING_POLICY_REVIEW`: o vídeo é somente rascunho local até confirmar uso aprovado e aderência às diretrizes.

## Instagram Reel: preparação oficial sem postagem automática

O fluxo usa os endpoints oficiais de container. A aplicação serve `CREATIVES_PATH` em `/media`; para a Meta buscá-lo, exponha esse endpoint em um domínio HTTPS público aprovado. Uma CDN também pode ser usada, desde que preserve o mesmo caminho relativo. Por exemplo, o arquivo local `data/creatives/oferta-x/instagram-reel.mp4` precisa existir publicamente como `https://afiliados.seudominio.example/media/oferta-x/instagram-reel.mp4` quando `INSTAGRAM_MEDIA_BASE_URL=https://afiliados.seudominio.example/media`.

Primeiro valide sem rede. Consulte o `queue_id` em `social-queue` e configure uma base fictícia HTTPS coerente com o mapeamento final:

```bat
set DRY_RUN=true
set INSTAGRAM_MEDIA_BASE_URL=https://media.seudominio.example/media
set INSTAGRAM_ASSET_ALLOWED_HOSTS=media.seudominio.example
.venv\Scripts\python.exe -m app.cli instagram-reel-create 3 --confirm-reviewed
```

O resultado é `SIMULATED`, com `network_calls=0`; nenhum container é criado ou persistido. Não existe argumento para informar um vídeo arbitrário: o URL é sempre derivado do único MP4 salvo no ContentPackage.

Antes de qualquer teste controlado de container, confirme no Meta App e no perfil: conta profissional, Page vinculada pelo Facebook Login, permissão `instagram_content_publish`, URL pública acessível e revisão editorial. Conteúdo com link afiliado exige o rótulo de parceria paga segundo a própria Meta; `#publi` no texto não substitui a ferramenta da plataforma. A coleção oficial acessível não oferece parâmetro confirmado para aplicar esse rótulo ao publicar, portanto o sistema não chama `media_publish`.

```bat
set DRY_RUN=false
set INSTAGRAM_GRAPH_API_VERSION=vNN.N
set INSTAGRAM_USER_ID=id_numerico_da_conta_profissional
set INSTAGRAM_ACCESS_TOKEN=token_obtido_pelo_fluxo_oficial
set INSTAGRAM_MEDIA_BASE_URL=https://media.seudominio.example/media
set INSTAGRAM_ASSET_ALLOWED_HOSTS=media.seudominio.example
set INSTAGRAM_FACEBOOK_LOGIN_READY=true
set INSTAGRAM_REEL_CONTAINER_API_ENABLED=true
```

Use a versão ativa indicada no painel/documentação da Meta no lugar de `vNN.N`; o projeto não define uma versão padrão que possa ficar obsoleta. O token fica apenas no ambiente e no header Bearer.

O operador pode criar o container e consultar seu processamento:

```bat
.venv\Scripts\python.exe -m app.cli instagram-reel-create 3 --confirm-reviewed
.venv\Scripts\python.exe -m app.cli instagram-reel-status 3
```

Repita somente `instagram-reel-status` enquanto o retorno estiver `PROCESSING`. Em `FINISHED`, o estado local para em `READY_TO_PUBLISH`. O comando abaixo existe como barreira verificável e sempre falha antes da rede, sem alterar estado:

```bat
.venv\Scripts\python.exe -m app.cli instagram-reel-publish 3 --confirm-reviewed
```

Para publicar conteúdo afiliado nesta versão, o operador deve usar o aplicativo oficial do Instagram e aplicar o rótulo de parceria paga. `INSTAGRAM_REEL_CONTAINER_API_ENABLED` habilita somente criação e consulta de container. O comando de reconciliação permanece apenas para recuperar eventual estado legado já ambíguo; o fluxo atual não cria novos estados `PUBLISHING` ou `PUBLISH_AMBIGUOUS`.

Feed, Story e carrossel permanecem fora desses comandos. Nenhum comando TikTok foi criado. O MP4 atual é H.264, 1080×1920, 30 fps e silencioso; a documentação oficial acessível especifica AAC quando há áudio, mas não confirmou que uma trilha de áudio seja obrigatória. A aceitação real continua pendente do processamento de um container controlado pela Meta.

## P2: fontes adicionais

O padrão continua Shopee. Selecione o adapter explicitamente para as novas fontes:

```bat
.venv\Scripts\python.exe -m app.cli import-offers examples\awin_feed.sample.csv --adapter awin
.venv\Scripts\python.exe -m app.cli import-offers caminho\feed.csv.gz --adapter awin
.venv\Scripts\python.exe -m app.cli cycle examples\mercadolivre_offers.sample.csv --adapter mercadolivre --campaign p2-dry-run
.venv\Scripts\python.exe -m app.cli p1-cycle examples\mercadolivre_offers.sample.csv --adapter mercadolivre --campaign p2-content
```

Amazon real e `amazon-json` permanecem bloqueados. Credenciais sozinhas não liberam uso: os termos brasileiros exigem aprovação escrita e um desenho de retenção/uso de Product Advertising Content que esta versão não implementa. `amazon-search` falha fechado sem abrir conexão.

Para Awin, baixe o Product Feed oficial pelo painel e importe o arquivo local. `AWIN_FEED_API_KEY` e `AWIN_PARTNER_API_TOKEN` são credenciais diferentes e não são usadas pelo import local. Se um programa entregar `aw_deep_link` direto no domínio do anunciante, liste somente esse host autorizado:

```bat
set AWIN_ALLOWED_AFFILIATE_HOSTS=loja-aprovada.example
```

Para Mercado Livre, gere o link no portal/barra oficial e preencha o CSV/JSON do exemplo. O adapter não gera links e recusa URLs que não sejam de produto ou `/sec/` oficial. Use apenas canais públicos permitidos.

Para Admitad, exporte um feed CSV no painel de publisher para um ad space e programa aos quais sua conta esteja vinculada. Configure o template com `article` ou `vendorCode`, `name`, `price`, `currencyID` e `url`, em BRL. O `url` deve ser um link HTTPS da rede (`ad.admitad.com/g/...` ou `fas.st/...`), já com o SubID desejado. O nome do programa é obrigatório:

```bat
.venv\Scripts\python.exe -m app.cli import-offers caminho\export-admitad.csv --adapter admitad --merchant-name "Nome do programa"
```

Esse comando só grava as ofertas. O export brasileiro real ainda precisa ser conferido, inclusive a forma de `url`; links diretos da loja são recusados. Campanhas P0/P1 e agendamento não aceitam Admitad. Até revisar as regras do anunciante, canal público e link, a configuração padrão oculta a oferta do hub e bloqueia publicação, página pública e `/go` para essa rede.

Feeds Awin e arquivos manuais Shopee/Mercado Livre recebem janela de 24 horas quando não há validade anterior. Reimporte dados oficiais atualizados antes de gerar conteúdo novamente.

## P3: painel administrativo e analytics

Em desenvolvimento local, `scripts\start.bat` usa `WEB_HOST=127.0.0.1`; abra `/` para Overview e `/offers` para o hub público. O painel inclui Overview, Offers, Queue, Published, Content, Channels, Merchants, Affiliate Programs, Clicks, Conversions, Revenue, Analytics, Compliance e Settings.

Para exposição atrás de HTTPS, configure os três valores. O processo recusa bind público sem senha ou com URL HTTP:

```bat
set WEB_HOST=0.0.0.0
set PUBLIC_BASE_URL=https://afiliados.seudominio.example
set ADMIN_PASSWORD=use-uma-senha-forte
scripts\start.bat
```

O usuário Basic é `admin`. Não coloque a senha na URL. O painel não mostra tokens, client secrets ou `ADMIN_PASSWORD`.

Analytics está em `/admin/analytics` e `/api/analytics?dimension=channel`. Dimensões: `product`, `category`, `merchant`, `channel`, `campaign`, `format`, `hour`, `day`, `creative`. A dimensão `hour` e o peso por horário do feedback usam UTC; não interprete esses valores como horário de Brasília. CTR fica `null` até existir impressão real importada por integração confiável; o DRY_RUN não cria impressões. O feedback usa volume mínimo, só considera conversões aprovadas/pagas e limita o ajuste agregado do score. `GOOD` começa em 45 para não exigir desconto informado de uma oferta forte.

Overrides de compliance são opcionais:

```bat
set CHANNEL_VISIBILITY_JSON={"telegram":"public","instagram":"public","tiktok":"public","site":"public"}
set MERCHANT_CHANNEL_RULES_JSON={}
set AWIN_ALLOWED_IMAGE_HOSTS=cdn-do-anunciante-aprovado.example
```

## Preview editorial opcional com IA

Após importar uma oferta, consulte o ID com `overview` ou no painel e execute:

```bat
.venv\Scripts\python.exe -m app.cli copy-preview 1 --channel instagram_feed
```

Sem credenciais remotas ou modelo local homologado, o comando retorna `source=template`. O provider remoto padrão é Cloudflare Workers AI com Gemma 4 26B A4B. No painel Workers AI, use **Use REST API**, crie um API Token e copie o Account ID. Depois configure:

```bat
set CLOUDFLARE_ACCOUNT_ID=seu_account_id
set CLOUDFLARE_API_TOKEN=seu_token
set CLOUDFLARE_AI_MODEL=@cf/google/gemma-4-26b-a4b-it
.venv\Scripts\python.exe -m app.cli copy-preview 1 --channel instagram_feed
```

Granite GGUF via llama.cpp existe como fallback experimental e fica desligado por padrão. Para um host que já tenha sido homologado:

```bat
set AI_LOCAL_ENABLED=true
set GRANITE_CLI_PATH=C:\caminho\llama-cli.exe
set GRANITE_MODEL_PATH=C:\caminho\granite.gguf
```

Os canais aceitos são `telegram`, `instagram_feed`, `instagram_story`, `instagram_reel`, `tiktok` e `site`. `copy-preview` continua `REVIEW_ONLY` e não grava `ContentPackage`, não enfileira nem publica. A IA pode usar somente tokens seguros do título/categoria no hook; preço, desconto, cupom, estoque, URLs, disclosure, score e compliance continuam em Python. A ordem automática é Cloudflare Workers AI -> Granite (somente se habilitado) -> TemplateProvider. Timeout, resposta inválida ou rate limit não interrompem o pipeline.

Duas falhas/rejeições consecutivas abrem o circuit breaker do provider por 300 s por padrão. Cada tentativa grava no stderr somente `provider`, `status`, `duration_ms`, `fallback_reason` e `error_type`; chave, prompt, resposta e URL não são logados. Workers AI oferece 10.000 Neurons/dia sem custo no plano Free. Cloudflare declara que não usa Customer Content do Workers AI para treinar modelos ou melhorar serviços sem consentimento explícito.

## Agendamento P0

Use o Agendador de Tarefas do Windows a cada cinco minutos:

```bat
cmd /c "cd /d C:\caminho\BOT AFILIADO && set DRY_RUN=true && .venv\Scripts\python.exe -m app.cli scheduler-once caminho\ofertas.csv --adapter shopee"
```

Os slots operacionais `07:00`, `10:00`, `13:00`, `16:00`, `19:00` e `22:00` são interpretados explicitamente no horário de Brasília (`America/Sao_Paulo`), independentemente da timezone configurada no sistema operacional. Timestamps persistidos e as dimensões `hour`/`day` de analytics continuam em UTC. Slots são separados por modo e adapter. Falhas confirmadas usam backoff e circuit breaker. O cliente Telegram espaça tentativas reais no mesmo processo. Se a Bot API retornar 429, o canal inteiro fica pausado no SQLite até o `retry_after` oficial e o lote atual para; a próxima execução de `scheduler-once` retoma quando vencer o prazo, sem contar isso como falha do canal. Transporte interrompido, resposta inválida/5xx ou falha ao gravar um envio confirmado deixam a fila em `PROCESSING`, sem retry automático.

Antes de cada envio, a fila revalida a oferta e recompõe a mensagem com os dados atuais. Se preço, cupom, frete, disponibilidade, validade ou regra do canal tiver mudado, a mensagem antiga é descartada sem chamar o Telegram. O resultado traz `REJECTED_STALE` e o evento `queue_rejected` registra apenas o código do motivo. Execute um novo ciclo com a fonte atualizada para gerar uma mensagem nova.

Uma URL afiliada alterada enquanto a mensagem aguarda na fila também invalida esse item. Novas mensagens Telegram trazem uma chave de publicação no `/go`: após publicação real, o clique usa a URL enfileirada, mesmo que uma nova coleta altere a oferta. Chave simulada ou inexistente recebe 404; dados de campanha adulterados também. Links do hub e mensagens Telegram antigas sem chave continuam usando a URL atual da oferta. Oferta vencida, sem estoque ou com cupom vencido recebe 410 sem gravar clique. Uma regra de compliance que passe a bloquear o programa impede o redirect mesmo para post antigo.

Para reconciliar `PROCESSING` após interrupção ou `NEEDS_RECONCILIATION`, pare primeiro qualquer worker/agendador em execução, liste os envios e confira manualmente o canal Telegram configurado. Use o ID da mensagem que realmente apareceu no canal; caso tenha certeza de que ela não foi publicada, confirme a ausência. A confirmação de ausência remove o item da fila e exige um novo ciclo com dados atuais. Esses comandos não chamam a Bot API e funcionam mesmo que `DRY_RUN=true` no ambiente, pois tratam apenas filas reais:

```bat
.venv\Scripts\python.exe -m app.cli telegram-processing
.venv\Scripts\python.exe -m app.cli telegram-reconcile 123 --published-message-id 456 --confirm-worker-stopped
.venv\Scripts\python.exe -m app.cli telegram-reconcile 123 --confirmed-not-published --confirm-worker-stopped
```

Execute somente uma das duas últimas linhas para cada item, após verificar o resultado no canal. Se houver dúvida, mantenha `PROCESSING` e não reenvie. A reconciliação grava `queue_reconciled` no log operacional, sem armazenar token ou texto da mensagem.

Consulte os eventos operacionais persistidos em SQLite:

```bat
.venv\Scripts\python.exe -m app.cli events --limit 50
```

`cycle` e `scheduler-once` registram ciclos e itens de fila processados; chamadas sem trabalho (`idle`) não geram evento. Cada linha tem horário UTC, modo `dry`/`real`, adapter, status, contagens e, em falha, apenas o tipo da exceção. O histórico mantém os 2.000 eventos mais recentes; não grava token, URL afiliada, texto da oferta ou mensagem bruta de erro. Uma falha ao gravar o evento emite aviso JSON no stderr, mas não faz o comando repetir uma publicação já concluída.

## Publicação real do Telegram

Somente após programa, canal, domínio e link estarem aprovados:

```bat
set DRY_RUN=false
set PUBLIC_BASE_URL=https://dominio-publico-aprovado
set TELEGRAM_BOT_TOKEN=token_fornecido_pelo_BotFather
set TELEGRAM_CHAT_ID=@canal_aprovado
```

Isso não habilita Instagram ou TikTok. Instagram Reel usa os gates e comandos próprios acima; TikTok não possui publisher.

## Testes

```bat
scripts\test.bat
```

O teste FFmpeg é executado quando `ffmpeg` está no PATH ou `imageio-ffmpeg` está instalado; caso contrário, ele é pulado e o fallback `FFMPEG_UNAVAILABLE` continua coberto.

## Execução contínua no notebook Acer com Lubuntu

O alvo é i3-6100U, 4 GB de RAM, HDD, cerca de 8,5 GB de swap e sem GPU dedicada. Qwen/Ollama não fazem mais parte da rota operacional. Sem `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN`, o bot usa `TemplateProvider` e continua funcional; com ambos configurados, Cloudflare Workers AI é o provider principal. Granite local permanece `AI_LOCAL_ENABLED=false` até uma homologação específica demonstrar latência e uso de RAM/swap aceitáveis.

Depois de autenticar o GitHub no notebook:

```bash
git clone https://github.com/iagoluch/bot-afiliado.git
cd bot-afiliado
bash scripts/install.sh
```

`install.sh` cria `.venv`, instala as dependências Python e cria os diretórios locais. Não baixa nenhuma IA. FFmpeg ausente produz aviso e mantém imagens/storyboards com vídeo pendente.

Mantenha `DRY_RUN=true` em `.env`. Com `WORKER_SOURCE_PATH` vazio, o worker usa `examples/shopee_offers.sample.csv` somente em DRY_RUN. Teste e inicie os processos:

```bash
bash scripts/test.sh
bash scripts/start.sh worker
```

Em outro terminal, `bash scripts/start.sh web` abre a interface local em `http://127.0.0.1:8000`. O worker obtém lock local exclusivo, executa slots e drena a fila SQLite, gera pacotes/ativos de cada oferta de forma sequencial, dorme em idle e registra heartbeat. SIGTERM/SIGINT encerram o processo; após reinício, o estado da fila permanece no banco. Envios Telegram reais de resultado incerto continuam em `PROCESSING` e exigem reconciliação manual, sem reenvio cego.

Para instalar worker e web como serviços persistentes do systemd:

```bash
bash scripts/install-systemd.sh
bash scripts/status.sh
.venv/bin/python -m app.cli runtime-status
```

`runtime-status` mostra banco, heartbeat, último ciclo, profundidade da fila, configuração segura dos providers de IA, FFmpeg e DRY_RUN sem exibir chaves. Scripts e units delegam a leitura de `.env` ao mesmo parser Python; as units usam o usuário normal, `WorkingDirectory` do clone atual e `Restart=on-failure` após 10 s. Consulte `journalctl -u bot-afiliado-worker.service -u bot-afiliado-web.service -n 100 --no-pager`; para reiniciar, use `sudo systemctl restart bot-afiliado-worker.service bot-afiliado-web.service`.

