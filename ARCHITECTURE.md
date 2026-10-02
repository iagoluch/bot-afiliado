# Arquitetura local P0 a P3 e runtime Lubuntu

Monólito Python local com SQLite, worker contínuo e web separada. Não usa Docker ou navegador. Pillow renderiza imagens e FFmpeg é um executável configurável, usado somente quando instalado. A camada de IA é opcional: Gemini remoto quando configurado, Granite GGUF via llama.cpp apenas como fallback local explicitamente habilitado e TemplateProvider como garantia final; operação, score e publicação não dependem de LLM.

```text
Fontes oficiais/assistidas
  ├─ Shopee CSV/JSON com link oficial
  ├─ Amazon Creators API BR: contrato mockado, uso operacional bloqueado
  ├─ Awin Product Feed CSV/gzip
  ├─ Mercado Livre CSV/JSON com link oficial manual
  └─ Admitad export CSV de publisher: somente import local, aguardando export real
  -> adapter selecionado -> normalização -> offers + price_history
  -> compliance -> score determinístico -> cooldown
  ├─ Telegram -> publish_queue -> DRY_RUN ou Bot API
  └─ P1 -> ContentPackages por canal
       ├─ Telegram canônico com /go, sem enfileirar publicação
       ├─ site /offers e /o/{slug} -> clique consciente /go/{id}
       ├─ Pillow -> feed/story/frames/thumbnails
       ├─ FFmpeg opcional -> Reels/TikTok MP4
       └─ social_queue -> revisão
            ├─ Reel -> preparação Instagram Graph API com gates explícitos
            └─ Feed/Story/TikTok -> publicação manual ou política pendente
  -> clicks -> conversões importadas -> analytics
  -> feedback determinístico por categoria/canal/horário -> ajuste limitado do score
```

## Responsabilidades

- `app/adapters`: fronteira por programa e capabilities declaradas.
- `app/adapters/amazon_creators.py`: OAuth em memória, SearchItems BR, resposta limitada e parser estrito. Recusa redirects para não encaminhar segredo/Bearer.
- `app/adapters/awin_feed.py`: leitura limitada e em streaming de CSV/gzip oficial; BRL obrigatório.
- `app/adapters/mercadolivre_manual.py`: importação assistida de URL oficial, sem API inventada.
- `app/adapters/admitad_feed.py`: importação CSV local do publisher, com URL da rede preservada; requer export real e revisão do programa antes de distribuição.
- `app/db.py`: persistência, idempotência, fila, tracking, ContentPackages e fila social.
- `app/services/curation.py`: score 0–100 determinístico.
- `app/services/analytics.py`: agregações por produto, categoria, merchant, canal, campanha, formato, horário, dia e criativo.
- `app/services/admin_dashboard.py`: consultas somente leitura do painel, sem segredos.
- `app/services/compliance.py`: revalida preço, estoque e cupom antes de gerar conteúdo.
- `app/services/pipeline.py`: pipeline P0 e estados da fila Telegram.
- `app/services/social_content.py`: copy, roteiro e hashtags específicos por canal.
- `app/services/llm.py`: provider Gemini por REST com chave somente em header, Granite opcional via llama.cpp, fallback em cadeia, circuit breaker e validação conservadora de hook; a IA não calcula fatos nem controla publicação.
- `app/worker.py`: loop único de `run_tick`, geração P1 sequencial por oferta, lock exclusivo, heartbeat SQLite e backoff.
- `app/services/media.py`: imagens determinísticas, download restrito de imagem oficial e chamada segura ao FFmpeg sem shell.
- `app/services/p1.py`: orquestra criativos e estados da fila social.
- `app/services/instagram_graph.py`: cliente fixo da Meta e máquina de estados somente para Reels; token fica no header e falha ambígua nunca é repetida automaticamente.
- `app/web.py`: painel administrativo autenticado, catálogo público, oferta, API protegida e redirect consciente.

## Painel e autenticação P3

`/`, `/admin/*` e `/api/*` são administrativos. Em localhost com bind `127.0.0.1`, podem funcionar sem senha para desenvolvimento. Se `PUBLIC_BASE_URL` ou `WEB_HOST` indicar exposição, o app falha ao iniciar sem `ADMIN_PASSWORD` e HTTPS configurado. A comparação Basic usa tempo constante; Settings expõe apenas valores seguros e indicadores booleanos.

`/offers`, `/o/{slug}`, `/go/{id}`, `/health` e assets continuam públicos. O hub não oferece link para o painel. Amazon não aparece no hub e `/go` Amazon retorna 403 enquanto a pendência de licença permanecer.

## Analytics e feedback P3

As consultas agrupam eventos SQLite com índices próprios. Receita e comissão usam apenas conversões `APPROVED`/`PAID`. CTR permanece `null` sem impressão marcada como real; simulações não viram impressão. CVR, EPC e valores por post usam denominador real ou `null`.

Na importação de conversões, `click_id` existente determina `offer_id`, canal e campanha. Valores explícitos divergentes são rejeitados antes da gravação; sem `click_id`, a atribuição usa apenas os campos disponíveis no relatório. Pedidos sem ID externo são recusados para preservar a idempotência. O schema do relatório oficial Shopee segue pendente de validação autenticada.

O ajuste de curadoria combina produto, loja, categoria, canal e hora UTC. Usa somente impressões marcadas como reais, cliques e conversões `APPROVED`/`PAID` ligadas a cliques. CTR exige pelo menos 20 impressões; CVR exige pelo menos cinco cliques; EPC e receita exigem pelo menos duas conversões monetárias. Cada escopo usa suavização, pesos limitados e teto agregado de ±8 pontos. Um evento isolado não muda prioridade, e cliques sem impressão ou resultado financeiro não geram bônus. A classificação `GOOD` começa em 45 para manter elegíveis ofertas fortes de preço estável sem alegar desconto.

## Persistência e estados P1

`content_packages` mantém caption, formato, payload JSON, assets e campanha. `content_key` torna regeneração idempotente. `social_queue` não possui worker automático:

- `READY_FOR_PUBLISH`: asset completo, aguardando revisão editorial; Reel pode somente criar/consultar container pela Graph API;
- `ASSET_PENDING`: storyboard existe, mas falta o MP4 por ausência/falha do FFmpeg;
- `PENDING_POLICY_REVIEW`: uso automatizado não foi aprovado para o canal;
- `PUBLISHED`: publicação externa confirmada por fluxo permitido; o publisher afiliado Instagram atual não produz esse estado;
- `CANCELLED`: reservado para cancelamento explícito.

`instagram_publications` persiste o fluxo ativo `CONTAINER_CREATED -> PROCESSING -> READY_TO_PUBLISH` ou `PROCESSING_FAILED`. A aplicação não possui método HTTP para `media_publish`: o contrato oficial acessível aceita somente `creation_id` nessa etapa e não oferece parâmetro confirmado para aplicar o rótulo de parceria paga exigido no conteúdo afiliado. Estados `PUBLISHING`, `PUBLISH_AMBIGUOUS` e `PUBLISHED` permanecem no schema apenas para compatibilidade e recuperação de tentativas legadas; o fluxo atual não entra neles.

O URL enviado à Meta não vem do operador. Ele é derivado de `INSTAGRAM_MEDIA_BASE_URL` mais o caminho relativo do único MP4 persistido no ContentPackage. O app serve `CREATIVES_PATH` em `/media`; para uso real, esse endpoint ou uma CDN equivalente precisa estar em HTTPS público, na allowlist e preservar o mesmo conteúdo/caminho. Feed, Story e carrossel não entram neste serviço porque o contrato oficial atual não foi confirmado em detalhe suficiente nesta pesquisa.

TikTok permanece `PENDING_POLICY_REVIEW` mesmo quando o MP4 existe. O rascunho local contém texto promocional e não é tratado como elegível para a Content Posting API.

Telegram também é um `ContentPackage` canônico com o mesmo template P0 e URL `/go`. Gerar P1 não cria item em `publish_queue`; a publicação Telegram continua sendo responsabilidade explícita do pipeline P0.

## Segurança e limites

Valores monetários ficam em centavos. URLs afiliadas são redirecionadas sem alteração. O hub escapa todo dado importado e não embute imagens externas. O gerador só tenta baixar imagens HTTPS de hosts oficiais permitidos, recusa redirects e limita bytes, dimensões e pixels antes da conversão; se falhar, produz placeholder local.

`DRY_RUN` isola a fila Telegram e faz os comandos Instagram retornarem `SIMULATED` sem rede nem tentativa persistida. A criação real de container exige feature gate próprio, revisão editorial explícita, conta/Page/permissão confirmadas, MP4 local pronto e URL HTTPS derivada. `media_publish` afiliado permanece bloqueado independentemente da configuração. O token vai somente no header Bearer, nunca em URL, banco ou erro persistido; redirects e respostas acima de 64 KB são recusados. Tokens Amazon ficam somente em memória e chamadas recusam redirects. Feeds Awin rejeitam moedas não BRL em vez de inventar conversão.

Magalu Influenciador, AliExpress Affiliate e SHEIN Affiliate Brasil têm regra de revisão por programa. Mesmo que uma oferta seja adicionada localmente, catálogo, fila e `/go` ficam bloqueados enquanto `merchant_review` estiver ativo. Essa barreira impede tratar link comum Magalu/SHEIN ou contrato AliExpress deprecated como link comissionado. Qualquer liberação operacional exige confirmar conta, link oficial, canal e termos específicos.

O cliente Telegram impõe intervalo de 1,05 s entre tentativas reais na mesma instância, recusa redirect e lê no máximo 64 KB. Resposta 429 usa `parameters.retry_after` (1 a 3600 s) para pausar o canal inteiro em SQLite e reagendar a fila sem incrementar o circuit breaker; o ciclo para após esse evento para evitar repetir a requisição imediatamente. Mensagens de erro não incluem o URL da Bot API que contém o token.

Após reclamar um item vencido, o worker Telegram revalida oferta, canal e texto canônico contra o conteúdo enfileirado. Oferta inválida ou mensagem diferente é retirada da fila antes de qualquer chamada externa, com evento `queue_rejected` no mesmo commit. Isso evita publicar preço/cupom/estoque antigos após um retry ou nova coleta. Transporte ou resposta ambígua e falha de persistência após envio deixam o item em `PROCESSING`, sem retry automático, porque o envio remoto pode ter ocorrido. O operador verifica o canal e reconcilia por ID da mensagem publicada ou confirmação explícita de ausência; ambas as decisões são transações locais auditadas.

A fila guarda uma cópia da URL afiliada ao ser criada. Mudança do link antes do envio invalida o item; a URL gravada na publicação e na reconciliação é a cópia enfileirada. Novas mensagens Telegram incluem `publication_key` no link `/go`; o redirect confere oferta, canal, campanha e criativo contra uma publicação real antes de usar a URL arquivada. Assim, uma nova coleta não troca o destino de um post antigo. Links do hub e mensagens legadas sem chave continuam usando a URL atual da oferta. Chaves de simulação não habilitam redirect. O clique com chave registra o SubID da URL arquivada. A validação atual de oferta (preço, estoque, validade e cupom) retorna 410 sem clique quando indisponível, e a regra do programa ainda pode bloquear qualquer redirect.

Os comandos `cycle` e `scheduler-once` gravam eventos operacionais estruturados em `operation_events` no mesmo SQLite: UTC, modo, adapter, status, ID da fila ou contagens e tipo de erro. Execuções `idle` são omitidas, e o histórico é limitado aos 2.000 eventos recentes. O logger não recebe URL, conteúdo, token ou mensagem bruta de exceção. Uma falha secundária de log gera aviso JSON no stderr sem transformar publicação já concluída em falha do ciclo.

Amazon permanece `WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN`: o opener de rede padrão recusa busca, a CLI recusa busca/importação e imagens Amazon não entram no renderer. O código mockado prova apenas o contrato HTTP/parser, não prontidão operacional.

Admitad permanece `WAITING_FOR_REAL_EXPORT`: só `import-offers --adapter admitad` aceita o CSV local com nome explícito do programa; ciclos P0/P1 e agendador não selecionam esse adapter. A regra padrão bloqueia publicação e redirect até revisão do vínculo do ad space, regras do anunciante e link do export real.

## IA auxiliar opcional

`copy-preview` continua leitura sem persistência. O P1 usa no máximo um hook validado por oferta/campanha, reaproveita o resultado persistido na mesma chave idempotente e o aplica apenas à cena inicial de vídeo. O modelo pode usar somente tokens seguros do título/categoria para tornar o hook menos genérico; legenda, preço, desconto, cupom, estoque, URLs, disclosure, score e compliance continuam vindo dos templates e dados verificados em Python. Saída com números, URL ou vocabulário factual proibido é recusada. A ordem automática é Gemini, Granite somente se `AI_LOCAL_ENABLED=true`, e template. Cada provider tem timeout curto; após falhas/rejeições consecutivas o circuit breaker pausa novas tentativas por uma janela configurável. Cada tentativa emite um evento JSON seguro no stderr com provider, estado, duração e classe de erro, sem prompt, resposta, URL ou segredo. A IA não acessa banco, shell, scheduler nem publisher.

## Worker e observabilidade no Lubuntu

`app.worker` mantém um lock local exclusivo durante toda a execução. Em cada tick, `run_tick` processa um slot de ingestão ou um item da fila persistente; após um ciclo, o P1 gera ContentPackages e assets por oferta, isolando falhas individuais. Idle dorme 30 s por padrão; falhas repetidas usam backoff progressivo. O heartbeat e último ciclo ficam em SQLite e são lidos por `runtime-status`; a consulta expõe somente estado seguro, sem URL afiliada, conteúdo ou segredo. O processo respeita SIGTERM/SIGINT e libera o lock. A fila SQLite conserva estados de retry e `PROCESSING` para reconciliação após reinício, sem reenvio automático de entrega incerta.

As units systemd executam worker e web como usuário normal, com diretório do clone, restart somente em falha e web em `127.0.0.1:8000`. No alvo Acer (i3-6100U, 4 GB, HDD, sem GPU), LLM local permanece desligada por padrão: a operação usa Gemini quando houver chave ou TemplateProvider sem rede. Granite local só entra após homologação explícita. FFmpeg ausente degrada somente a geração de vídeo, preservando imagens e asset pendente.

O preço riscado de importação manual fica em metadado não verificado. `price_history` registra apenas preços atuais observados; `verified_history_discount` exige duas observações anteriores em timestamps distintos e compara o preço atual com o menor deles. `verified_offer_data` monta uma prova efêmera, recalculada a partir do banco e consumida por Telegram, site e criativos. Claims antigos em rascunhos ainda não publicados são invalidados na migração de inicialização.

O fallback Granite usa parâmetros documentados pela [CLI oficial do llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/cli/README.md), inclusive `--offline`, `--single-turn`, `--simple-io`, CPU-only (`-ngl 0`) e limites de tokens/contexto. A execução usa argv sem shell, timeout local configurável e limite de saída aceito de 2048 caracteres.
