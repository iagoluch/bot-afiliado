# Compliance operacional

## Antes de publicar

1. O operador deve ser aceito no programa de afiliados.
2. O canal/site usado deve estar cadastrado e aprovado conforme os [Termos Shopee Brasil](https://help.shopee.com.br/portal/10/article/124094-Programa-de-Afiliados-da-Shopee-Termos-e-Condi%C3%A7%C3%B5es). A existência do bot não significa aprovação do canal.
3. O link precisa ser gerado por meio oficial e importado intacto.
4. `PUBLIC_BASE_URL` precisa ser um endpoint HTTPS público e aprovado; localhost serve apenas ao `DRY_RUN`.
5. A mensagem começa com `#publi`, de forma imediatamente visível, conforme o [guia CONAR/Shopee](https://help.shopee.com.br/portal/10/article/196794-Identifica%C3%A7%C3%A3o-de-Conte%C3%BAdo-Publicit%C3%A1rio%3A-Guia-CONAR-2026-para-Influenciadores-e-Afiliados).
6. Preço, cupom, estoque e comissão precisam vir de fonte válida e atualizada.

## Garantias do P0

- `DRY_RUN=true` por padrão e nenhuma chamada ao Telegram nesse modo.
- A fila Telegram revalida oferta e conteúdo imediatamente antes do envio. Alteração de preço/frete/cupom, expiração, indisponibilidade ou mudança de regra descarta a mensagem antiga sem rede e registra somente o motivo estruturado.
- Redirect apenas após clique consciente; não há redirecionamento automático.
- O `/go/{id}` registra dados mínimos e responde HTTP 302 para a URL armazenada sem reescrever parâmetros.
- Não há fingerprinting, scraping, browser automation ou endpoint privado.
- Clique nunca é contabilizado como venda; apenas conversões importadas em `APPROVED`/`PAID` entram em receita e comissão.
- Tokens não são gravados no banco nem exibidos em logs.

## Regras do P1

- `/o/{slug}` exibe publicidade, preço, cupom, loja e atualização antes do botão; não há redirect automático.
- Título, descrição e cupom importados são escapados. O site não embute URL de imagem externa.
- O gerador revalida preço, estoque e validade do cupom imediatamente antes de produzir assets.
- Copies usam somente fatos persistidos. Não adicionam escassez, urgência, estoque, frete, desconto ou cupom sem dado correspondente.
- `original_price` em CSV manual Shopee/Mercado Livre é referência não verificada. O sistema só anuncia “De”, “BAIXOU” ou percentual após duas observações anteriores distintas do preço atual no histórico SQLite, usando como referência o menor preço observado; a visão de verificação é calculada no banco, sem confiar em marker recebido na importação. Rascunhos legados não publicados são invalidados no upgrade.
- Imagens externas só são baixadas de hosts HTTPS oficiais permitidos, sem redirects e com limites de bytes/dimensões; falhas produzem placeholder local.
- `READY_FOR_PUBLISH` significa asset local aguardando revisão. Não significa autorização da plataforma nem publicação aprovada.
- Conteúdo TikTok com CTA promocional sobreposto fica `PENDING_POLICY_REVIEW`, mesmo com MP4 pronto. Não deve ser enviado pela Content Posting API sem revisão e uso aprovado.
- A Meta classifica post/Reel com link afiliado e comissão como branded content e exige o rótulo de parceria paga. `#publi` permanece disclosure textual do projeto, mas não substitui esse rótulo da plataforma.
- Somente Instagram Reel possui preparação Graph API para criar/consultar container. Rede real exige `INSTAGRAM_REEL_CONTAINER_API_ENABLED`, conta profissional/Page/permissão confirmadas e `--confirm-reviewed`. Feed, Story, carrossel e TikTok não entram nesse serviço.
- O URL do Reel é derivado de `INSTAGRAM_MEDIA_BASE_URL` mais o caminho relativo do MP4 persistido; o operador não pode trocar o asset pelo comando. Base e asset precisam ser HTTPS públicos, sem credencial/query, e o host precisa estar na allowlist.
- `DRY_RUN` não abre rede nem persiste tentativa Instagram. Em modo real, o token existe somente no ambiente/header Bearer. Redirects são recusados e erros persistem apenas códigos sanitizados.
- `media_publish` afiliado é bloqueado antes da rede e da mudança de estado. Um booleano local não comprova que o rótulo foi aplicado; publicação manual exige usar a ferramenta oficial de parceria paga. Reconciliação existe somente para estado legado.

## Regras dos programas P2/P3

- Amazon: usar somente `detailPageURL` retornada pela Creators API com o `partnerTag` configurado. `savingBasis` não é prova suficiente de preço anterior ou desconto.
- Amazon: os [termos brasileiros](https://associados.amazon.com.br/help/operating/policies/) exigem Links Especiais acessados diretamente, destino Amazon claro, identificação do agente e controles específicos sobre Product Advertising Content. Por decisão conservadora, rede, import operacional, hub, `/go`, imagens e filas ficam bloqueados até aprovação escrita e desenho de retenção validado.
- Amazon: não baixar nem persistir imagem do Program Content; não expor histórico/alerta de preço. O contrato HTTP mockado limita resposta a 40 KB e usa `User-Agent` no formato `Agent/...`.
- Awin: preservar `aw_deep_link`; aceitar apenas BRL. `rrp_price` é referência do anunciante e não aparece como “De” ou desconto sem histórico verificável. Chave de feed e token da Partner API são segredos distintos.
- Awin: tratar o feed como vencido após 24 horas quando `valid_to` não define prazo anterior. Este é um gate de qualidade baseado na recomendação oficial de atualização diária.
- Mercado Livre: gerar o link somente pelo portal/barra oficial e preservar etiquetas. Não há API de geração de link implementada.
- Mercado Livre: usar somente canais públicos aprovados. Grupos privados, publicidade em buscadores e mídia offline são proibidos pelas orientações oficiais verificadas.
- Magalu Influenciador: exigir link compartilhado da própria vitrine de divulgador aprovada, não um URL comum do catálogo. O [programa oficial](https://www.parceiromagalu.com.br/divulgador) descreve a vitrine e seu compartilhamento; sem conta/link reais verificados, catálogo, distribuição e `/go` ficam bloqueados por padrão.
- SHEIN Brasil: o [FAQ da nova plataforma](https://m.shein.com/br/campus--a-1500.html) afirma que apenas produtos/campanhas selecionados no centro de afiliados geram links rastreáveis e que links copiados diretamente do site/app não geram comissão. O programa antigo terminou em 2024. Até revisar conta e link reais, catálogo, distribuição e `/go` ficam bloqueados por padrão.
- AliExpress: a [documentação Affiliate API encontrada](https://developer.alibaba.com/docs/doc.htm?articleId=118193&docType=1&treeId=674) está marcada deprecated. Não usar seus endpoints antigos ou gerar link sem contrato vigente; distribuição e `/go` ficam bloqueados por padrão.
- Admitad: importar somente feed CSV exportado para o ad space e programa aceitos na conta do publisher, com link afiliado preservado. O [guia oficial do feed](https://admitad.useresponse.com/knowledge-base/article/product-feed-what-it-is-and-how-to-export-it-for-your-website_4) permite template e SubID; o [guia de deeplink](https://admitad.useresponse.com/knowledge-base/article/deeplink_20) exige vínculo do ad space ao programa para uso normal. Até revisar o programa, o canal e um export real, a regra padrão oculta catálogo/página e bloqueia distribuição e redirect; liberar ambos requer configuração explícita em `MERCHANT_CHANNEL_RULES_JSON`.
- Nenhum adapter faz scraping ou converte moeda. Uma moeda não suportada interrompe a importação com erro claro.
- Shopee e Mercado Livre manuais recebem janela conservadora de 24 horas quando o arquivo não informa `expires_at`. Oferta vencida não gera conteúdo ou fila nova; o hub oculta preço e CTA até atualização.
- Regras por merchant e visibilidade de canal podem ser ajustadas por `MERCHANT_CHANNEL_RULES_JSON` e `CHANNEL_VISIBILITY_JSON`. Mercado Livre bloqueia canal privado, busca paga e mídia offline.
- A geração de conteúdo não publica nem cria fila de publicação Telegram. A fila P0 continua exigindo execução explícita.

## Responsabilidade do operador

Validar termos e cadastro sempre que adicionar canal, domínio ou programa. Revisar cada pacote social antes da publicação. No Instagram, publicar manualmente pelo aplicativo oficial e confirmar que o rótulo de parceria paga aparece no perfil controlado; não há gate local que substitua essa ação. Não publicar urgência, desconto, estoque ou comissão sem evidência. No Mercado Livre, confirmar que o canal é público e permitido. Não comprar pelo próprio link e não fazer spam.
