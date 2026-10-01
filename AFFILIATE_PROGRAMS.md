# Programas e capabilities P0–P3

Legenda: `SUPPORTED` automatizado e validado localmente; `MANUAL` existe fluxo oficial assistido; `WAITING_FOR_CREDENTIALS` falta acesso; `WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN` requer autorização e arquitetura adicional; `MANUAL_OR_PENDING` não há contrato automatizável confirmado.

| Programa | Produtos | Ofertas | Preço | Cupons | Comissão | Link afiliado | Conversões | Relatórios | Criativos | Implementação |
|---|---|---|---|---|---|---|---|---|---|---|
| Shopee Afiliados | WAITING_FOR_CREDENTIALS | WAITING_FOR_CREDENTIALS | MANUAL | MANUAL | WAITING_FOR_CREDENTIALS | MANUAL | MANUAL | MANUAL | MANUAL | Implementado via importação oficial assistida |
| Amazon Associados | WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN | MANUAL_OR_PENDING | WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN | MANUAL_OR_PENDING | MANUAL_OR_PENDING | WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN | MANUAL_OR_PENDING | MANUAL_OR_PENDING | WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN | Contrato Creators API mockado; rede/import/hub/publicação bloqueados |
| Awin | SUPPORTED | MANUAL_OR_PENDING | SUPPORTED | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | WAITING_FOR_CREDENTIALS | WAITING_FOR_CREDENTIALS | SUPPORTED | Importação local de Product Feed CSV/gzip com `aw_deep_link` pronto |
| Mercado Livre | MANUAL | MANUAL | MANUAL | MANUAL | MANUAL | MANUAL | MANUAL | MANUAL | MANUAL_OR_PENDING | Importação assistida de link criado no portal/barra oficial |
| Magalu Influenciador | MANUAL | MANUAL_OR_PENDING | MANUAL | MANUAL_OR_PENDING | MANUAL | MANUAL | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | Links somente da própria vitrine; sem adapter operacional e sob revisão |
| AliExpress Affiliate | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | Documentação Affiliate API encontrada está marcada deprecated; sem adapter operacional |
| SHEIN Affiliate Brasil | MANUAL | MANUAL | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL | MANUAL_OR_PENDING | MANUAL | MANUAL_OR_PENDING | Links somente do centro de afiliados; sem adapter operacional e sob revisão |
| Admitad | MANUAL | MANUAL_OR_PENDING | MANUAL | MANUAL_OR_PENDING | MANUAL_OR_PENDING | MANUAL_OR_PENDING | WAITING_FOR_CREDENTIALS | WAITING_FOR_CREDENTIALS | MANUAL_OR_PENDING | CLI importa CSV local; `WAITING_FOR_REAL_EXPORT`, sem ciclo/publicação |

O status do adapter Shopee P0 é `MANUAL_OR_PENDING`; nenhuma célula manual é apresentada como API automatizada.

`CREATE_AFFILIATE_LINK` no Awin não é `SUPPORTED`: o adapter consome o `aw_deep_link` pronto do feed e não gera links novos. O P1 consome somente ofertas já normalizadas e links oficiais armazenados; as filas sociais não consultam marketplace.

No Admitad, `MANUAL` significa apenas leitura de um CSV exportado pelo publisher. O adapter aceita `url` somente como link HTTPS `ad.admitad.com/g/...` ou `fas.st/...` já presente no arquivo, preservando SubID. Como a documentação do feed não garante esse formato de `url` para todos os programas, a integração continua `WAITING_FOR_REAL_EXPORT` e exige revisão do export e das regras do programa antes de habilitar ciclos, publicação ou redirect.

Para Magalu e SHEIN, `MANUAL` descreve apenas a capacidade documentada do portal oficial; não existe importador desses programas no projeto. A distribuição e o redirect permanecem bloqueados por padrão até revisão explícita. No Magalu, o link deve sair da própria vitrine; na SHEIN, do centro de afiliados, pois link direto do catálogo não gera rastreamento de comissão segundo o FAQ brasileiro.
