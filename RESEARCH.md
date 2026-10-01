# Pesquisa oficial — P0 a P3

Pesquisa realizada em 30/09/2026 e revisada em 01/10/2026. A implementação só afirma capacidades que aparecem em documentação oficial acessível. Nenhum endpoint privado, scraping ou contrato inferido foi usado.

## Shopee Afiliados

- O [Help Center brasileiro](https://help.shopee.com.br/portal/10/article/128461-Como-gerar-seus-links-de-Afiliado-ou-ID-de-produto-para-compartilhar) documenta geração de links afiliados e uso de `Sub_id` pelos meios oficiais.
- O [Affiliate API Help Center](https://help.shopee.sg/portal/10/article/191702-API-Access) descreve API oficial com ofertas, short links e relatórios de conversão/validação.
- O [explorer brasileiro](https://open-api.affiliate.shopee.com.br/explorer) exige AppId e Secret. Sem credenciais, o schema GraphQL brasileiro não pôde ser validado.
- O [portal brasileiro](https://affiliate.shopee.com.br/open_api) exige login e JavaScript.

Decisão P0: `integration_status = MANUAL_OR_PENDING`. O adapter importa CSV/JSON com link já gerado por meio oficial. A URL afiliada fica intacta. A futura integração autenticada permanece `WAITING_FOR_CREDENTIALS`; não há código com query GraphQL inventada.

## Telegram

A [Bot API oficial](https://core.telegram.org/bots/api) documenta `sendMessage`, `message_id` e `ResponseParameters.retry_after` quando o flood control é excedido. A [FAQ oficial](https://core.telegram.org/bots/faq) orienta evitar mais de uma mensagem por segundo no mesmo chat. O publisher espaça envios reais, respeita o prazo de `retry_after`, recusa redirects e limita a resposta; em `DRY_RUN=true`, não abre conexão de rede nem aguarda intervalo.

## Amazon Creators API Brasil — P2

- A integração usa a Creators API atual e não usa PA-API 5. O [exemplo oficial com cURL](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/get-started/using-curl) documenta OAuth `client_credentials` em `https://api.amazon.com/auth/o2/token`, escopo `creatorsapi::default` e Bearer token.
- [SearchItems](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/api-reference/operations/search-items) usa `POST https://creatorsapi.amazon/catalog/v1/searchItems`, `marketplace=www.amazon.com.br`, `partnerTag`, `keywords`, `itemCount` e `resources`.
- [Headers e parâmetros comuns](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/concepts/common-request-headers-and-parameters) exigem `Authorization`, `Content-Type` e `x-marketplace`.
- O parser lê preço atual somente de `offersV2.listings.price.money`, exige BRL e preserva `detailPageURL` com o `partnerTag` configurado. `savingBasis` não é convertido em preço anterior ou desconto. As requisições de contrato usam `User-Agent: Agent/BotAfiliado`.
- Os [Termos do Programa de Associados Brasil](https://associados.amazon.com.br/help/operating/policies/) exigem acesso direto aos Links Especiais, transparência do destino Amazon e trazem restrições de agregação/análise, armazenamento de imagem, cache de conteúdo, tamanho de payload e exibição de preço/estoque. A interpretação conservadora do projeto é que a arquitetura local atual precisa de aprovação escrita e desenho específico de retenção antes de uso real.
- Decisão P3: `amazon-search`, `amazon-json`, hub, `/go`, fila Telegram e publicação social ficam bloqueados. O cliente HTTP e parser permanecem somente para teste mockado de contrato; imagem Amazon não é baixada nem renderizada.

## Awin Product Feed — P2

- O [download da lista de feeds](https://help.awin.com/developers/docs/product-feed-list-download) e o [guia de Product Feed para publishers](https://help.awin.com/developers/docs/product-feed-publisher-guide-intro) confirmam feeds oficiais e campos como `aw_deep_link`, `product_name`, `aw_product_id`, `merchant_name`, `merchant_id`, `search_price` e `currency`.
- O adapter importa CSV ou gzip local em streaming, limita tamanho/linhas, aceita somente BRL e preserva `aw_deep_link`. Hosts fora de `awin1.com` exigem allowlist explícita do programa.
- O [guia oficial de gerenciamento de feed](https://help.awin.com/developers/docs/en/managing-your-feed) recomenda atualização pelo menos a cada 24 horas. O adapter usa 24 horas como gate de qualidade quando `valid_to` não informa prazo anterior; oferta vencida não entra em nova publicação e o hub oculta preço/CTA.
- `rrp_price` é guardado como preço de referência em metadados; não gera alegação “De”, preço anterior ou desconto sem histórico verificável.
- A [API de transações para publisher](https://help.awin.com/apidocs/returns-a-list-of-transactions-for-a-given-publisher) foi confirmada, mas não foi implementada sem credenciais. Chave de feed e token da Partner API são credenciais distintas.

## Mercado Livre Afiliados — P2

- O [checklist oficial](https://www.mercadolivre.com.br/l/checklist), a página [Gere seus links](https://www.mercadolivre.com.br/l/afiliados-gere-seus-links) e as [perguntas frequentes](https://www.mercadolivre.com.br/l/primeiros-passos-perguntas-frequentes-para-afiliados) descrevem geração manual pelo portal/barra oficial.
- A página de [direcionamento de visitas](https://www.mercadolivre.com.br/l/afiliados-direcionamento-de-visitas) orienta uso em site, blog e redes sociais próprias e não autoriza redirecionamento automático.
- Não foi localizada API oficial para gerar links afiliados. O adapter fica `MANUAL_OR_PENDING`, aceita somente URL oficial já gerada e a preserva inteira, inclusive etiquetas.
- O checklist permite canais públicos, incluindo Telegram público, e proíbe grupos privados, publicidade em buscadores e mídia offline. Essas restrições permanecem responsabilidade operacional antes da publicação.

## Plataformas avaliadas para fases futuras

- AliExpress: o índice oficial [Affiliate API](https://developer.alibaba.com/docs/doc.htm?articleId=118193&docType=1&treeId=674) está expressamente marcado como *deprecated*. Páginas antigas de `aliexpress.affiliate.product.query` continuam indexadas, mas isso não comprova contrato atual. Sem acesso aprovado e documentação vigente, nenhuma chamada automatizada ou geração de link é habilitada. Estado: `MANUAL_OR_PENDING`.
- Magalu: o [Influenciador Magalu](https://www.parceiromagalu.com.br/divulgador) orienta criar a própria loja e compartilhar os links dos produtos dessa vitrine. O [contrato do divulgador](https://www.parceiromagalu.com.br/divulgador/venda-mais/post/contrato-do-divulgador.html) vincula bonificação às vendas intermediadas pela vitrine. A API localizada em [Magalu Devs](https://developers.magalu.com/docs/apis/) atende seller/marketplace; não confirma API para Influenciador. Um link comum do catálogo Magalu não comprova comissão. Estado: link manual da própria vitrine, integração `MANUAL_OR_PENDING` até validar conta e formato reais.
- SHEIN Brasil: o [FAQ oficial da nova plataforma](https://m.shein.com/br/campus--a-1500.html) informa que o programa antigo encerrou em 29/08/2024 e exige novo cadastro/validação de conta social. Produtos e campanhas devem ser escolhidos no centro de afiliados; links compartilhados diretamente do site ou app não são rastreados para comissão. O FAQ descreve janela de 24 horas para o pedido e atribuição ao último clique. Não foi confirmada API ou feed oficial público para este programa. Estado: link manual emitido pelo centro de afiliados, integração `MANUAL_OR_PENDING` até aprovar conta e inspecionar export/link reais.

O FAQ SHEIN foi consultado pelo conteúdo oficial indexado; a abertura direta da página retornou HTTP 429 nesta revisão. Revalidar no portal após acesso aprovado antes de usar qualquer link real.

Magalu, AliExpress e SHEIN ficam sob revisão obrigatória no motor de compliance. Até confirmar cadastro, link, canal e forma de atribuição, o sistema não os distribui nem permite `/go`; uma configuração operacional explícita só deve ser usada após essa revisão.

## Admitad Product Feed — import local pendente de export real

- O [guia oficial de Product Feed da Mitgo/Admitad](https://admitad.useresponse.com/knowledge-base/article/product-feed-what-it-is-and-how-to-export-it-for-your-website_4) confirma exportação CSV pelo publisher para um ad space e programa selecionados. Um template pode escolher e renomear colunas, definir moeda, separador e SubID. Ele recomenda `categoryId`, `currencyID`, `name`, `picture`, `price` e `url`; também documenta `article` e `vendorCode` para identificação.
- O [guia oficial de Deeplink](https://admitad.useresponse.com/knowledge-base/article/deeplink_20) mostra links da rede em `https://ad.admitad.com/g/...` e shortlinks `https://fas.st/...`, com SubID. A coluna `url` da documentação do feed é descrita como URL da página do produto; sua forma real num export brasileiro ainda não foi verificada. O adapter local exige que `url` já seja um desses links HTTPS da rede, sem gerar nem reescrever o link. Export com URL direta do anunciante é recusado, pois não prova atribuição afiliada.
- O [acesso à API de publisher](https://admitad.useresponse.com/knowledge-base/article/api_20) depende de credenciais da conta. Nenhuma chamada de rede ou geração automática foi implementada.
- Contrato conservador local: CSV UTF-8, `article` ou `vendorCode`, `name`, `price`, `currencyID=BRL` e `url` da rede; colunas renomeadas somente com mapeamento explícito. O nome do programa é informado pelo operador. `picture`, `categoryId`, descrição e vendor são opcionais. O campo `oldprice` não alimenta desconto ou preço anterior sem histórico independente. Arquivo, linhas e campos têm limites; o link/SubID permanece integral.
- Estado: `MANUAL_OR_PENDING` / `WAITING_FOR_REAL_EXPORT`. Ainda faltam um arquivo real de programa aprovado, confirmação do schema e da URL rastreável, regras do programa/canal e revisão editorial para a primeira publicação. A CLI permite apenas importação local; ciclos, agendador e publicação não selecionam este adapter.

## Instagram e TikTok — P1/P3

- A [coleção oficial da Instagram API da Meta](https://www.postman.com/meta/instagram/folder/u4g5a2a/instagram-api-with-facebook-login) documenta publicação para contas profissionais, exige Page vinculada no fluxo Facebook Login e permissão `instagram_content_publish`; Stories estão limitados a contas business.
- A [documentação oficial da coleção](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api) confirma o fluxo Reel: `POST /{ig_user_id}/media` com `media_type=REELS` e `video_url` público, `GET /{container_id}?fields=status_code,status` até `FINISHED`, e então `POST /{ig_user_id}/media_publish` com `creation_id`.
- O [sample oficial da Meta para Reels](https://github.com/fbsamples/reels_publishing_apis/blob/main/insta_reels_publishing_api_sample/README.md) confirma processamento assíncrono, necessidade de aguardar upload concluído e requisitos como MP4/MOV, H.264 ou HEVC, 23–60 fps e recomendação 9:16. Também descreve carrossel em alto nível, mas não implementa esse formato no sample. O gerador local produz MP4 H.264, 1080×1920, 30 fps e sem áudio; as fontes acessíveis especificam AAC quando existe áudio, mas não afirmam que áudio seja obrigatório. A aceitação de um arquivo silencioso permanece pendente de container real controlado.
- A [Central de Ajuda oficial da Meta](https://www.facebook.com/help/instagram/616901995832907) diz expressamente que post ou Reel com link afiliado e comissão é branded content e deve usar o rótulo de parceria paga. Por isso `#publi` sozinho não libera a publicação.
- Na coleção oficial acessível, a requisição de publicação de Reel expõe apenas `creation_id`; não foi encontrado parâmetro oficial confirmado para aplicar o rótulo de parceria paga nessa operação. Inferência conservadora: um booleano local não garante o rótulo, então o sistema bloqueia `media_publish` para conteúdo afiliado.
- O acesso direto à página `developers.facebook.com` retornou 429 nesta verificação. Feed, Story e carrossel não foram implementados porque não foi possível confirmar um contrato atual completo e testável nas fontes oficiais acessíveis. Story continua manual e restrito a business conforme a coleção; o suporte de carrossel observado permanece pendente de contrato detalhado.
- A implementação P3 prepara apenas Reels, com origem Graph fixa, Bearer no header, redirects desabilitados, limite de resposta, URL do asset derivada do MP4 persistido e máquina de estados SQLite. `DRY_RUN` faz zero chamadas. O cliente implementa apenas criação e consulta de container; não existe chamada `media_publish` no código da aplicação.
- A [TikTok Content Posting API](https://developers.tiktok.com/docs/en/content-posting-api-get-started) exige app, autorização do criador com escopo `video.publish` e auditoria para postagem pública. As [Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines), atualizadas em 04/08/2026, proíbem branding, links e texto promocional sobrepostos no conteúdo compartilhado pela integração e dizem que Direct Post não serve a um utilitário limitado às contas da própria equipe.

Decisão P3: Instagram completo entra em fila local `READY_FOR_PUBLISH`; Reel pode avançar somente até container `FINISHED`. A postagem afiliada permanece manual no aplicativo oficial, onde o operador aplica o rótulo de parceria paga. Nenhuma chamada real foi executada. TikTok afiliado com preço/cupom/CTA sobreposto é gerado somente como rascunho local e fica `PENDING_POLICY_REVIEW`; não é apresentado como pronto para a Content Posting API.

## Benchmarks públicos de produto

Foram observados apenas padrões públicos: curadoria moderada e distribuição multicanal no [Pechinchou](https://pechinchou.com.br/nossa-historia), mensagens de canal no [Setup Humilde](https://t.me/SetupHumilde) e [Fraguas84](https://t.me/s/Fraguas84Oficial), e filtros de loja/categoria/horário no [DivulgaNinja](https://linkafiliado.lovable.app/). Não foram copiados código, marca, identidade ou conteúdo. Frequência e performance não foram inferidas sem dados.

O [VerticalVideoGenerator](https://github.com/princeofscale/VerticalVideoGenerator) foi usado apenas como benchmark público de produto para etapas de frames, recorte vertical, legenda e thumbnail. A implementação local não adotou seu código, LLM ou dependências pesadas.

## Limites da pesquisa

O contrato autenticado da Shopee Brasil, a resposta real da Creators API, feeds Awin reais, autorizações de canais, OAuth social e relatórios reais só podem ser confirmados após login/aprovação e credenciais. Essas dependências estão isoladas do restante do pipeline.
