# Shopee Affiliate Open API — descoberta de ofertas tecnológicas

## Objetivo e estado

Fonte destinada ao programa de **afiliados da Shopee**, não à Open Platform
de vendedores. A API GraphQL aceita App ID + Secret aprovados pela **Shopee
Affiliate Open API** e a operação `productOfferV2` pode retornar
`offerLink` com rastreamento de afiliado da própria conta.

**Importante:** a API precisa ser testada com suas credenciais aprovadas.
A implementação está verificada por mocks/testes offline, e **ainda não
comprovamos acesso/contrato GraphQL com a conta brasileira do Acer**.
Nada deve ser publicado automaticamente com dados de estoque não verificados.

Referências:
- Shopee Help (capacidades de Affiliate API):
  https://help.shopee.sg/portal/10/article/191702-API-Access
- Explorador brasileiro, que requer AppId/Secret:
  https://open-api.affiliate.shopee.com.br/explorer
- Ajuda oficial do programa de afiliados no Brasil:
  https://help.shopee.com.br/portal/4/article/93461-Programa-de-Afiliados

## Ativar apenas a descoberta após aprovação

Sem alterar `DRY_RUN`, `TELEGRAM_REAL_ENABLED` ou o seu token Telegram,
preencha localmente no `.env` (não faça commit nem cole segredos no chat):

```dotenv
SHOPEE_APP_ID=SEU_APP_ID_APROVADO
SHOPEE_SECRET=SEU_SECRET_APROVADO
```

Se ainda não foram liberados, execute somente `collection-status` ou
`shopee-affiliate-discover`: este último retorna
`BLOCKED_MISSING_SHOPEE_AFFILIATE_CREDENTIALS` sem abrir conexão.

Após aprovação:

```bash
cd ~/bot-afiliado
.venv/bin/python -m app.cli shopee-affiliate-discover --keyword ssd --limit 3
```

O comando usa **HTTPS exclusivamente com o domínio oficial da API Shopee**,
assina o JSON enviado com SHA256 usando as credenciais locais e solicita
um número limitado de resultados. Não usa scraping, login de navegador,
cookie do painel de afiliados ou API privada.

Os candidatos exibem identificação do produto, preço único
(`priceMin == priceMax`), `offerLink`, comissão da API e os metadados
disponíveis. URLs suspeitas, produtos fora do filtro tecnológico, comissão
inválida, campanhas expiradas e preços em faixa são descartados.

Para **armazenar candidatos** no SQLite sem gerar job, publicação ou
fila Telegram:

```bash
.venv/bin/python -m app.cli shopee-affiliate-discover \
  --keyword ssd --keyword notebook --limit 5 --stage
```

A opção `--stage` marca todos como `UNKNOWN` em estoque, com
`source_type=SHOPEE_AFFILIATE_API_STOCK_UNVERIFIED`. A pipeline bloqueia
esses produtos tanto na criação da fila quanto antes da publicação.
Ela nunca confirma `IN_STOCK` com base apenas na presença de `offerLink`.

## Para ativar publicação realmente autônoma

Ainda precisamos validar com a Shopee uma **fonte autorizada de estoque
atualizado** (a consulta `productOfferV2` documentada não fornece estoque)
e confirmar os campos da resposta real para sua aplicação. Somente após isso
deve ser criada a transição explícita de `UNKNOWN` para `IN_STOCK`, com
prova atualizada, deduplicação e limite de envios.

Sem estoque confiável, publicar automaticamente apenas pelo preço ou
pelo `offerLink` seria divulgar ofertas potencialmente indisponíveis.
O `offerLink` é a URL afiliada fornecida pela API; o programa da Shopee
continua responsável por atribuir e validar as comissões.

## Diagnóstico

- `BLOCKED_MISSING_SHOPEE_AFFILIATE_CREDENTIALS`: aprovação ainda pendente.
- `SHOPEE_AFFILIATE_API_ERROR_10020`: verificar assinatura/credenciais.
- `SHOPEE_AFFILIATE_API_ERROR_10035`: conta sem acesso Open API.
- `SHOPEE_AFFILIATE_API_ERROR_SCHEMA_OR_ACCESS`: conferir schema no Explorer
  oficial com credenciais liberadas; não inventar nomes de campos.
- `SHOPEE_AFFILIATE_HTTP_429`: limite; esperar, não repetir continuamente.
- `CANDIDATES_ONLY_STOCK_UNVERIFIED`: dados recebidos; não significa
  autorização de publicação.
