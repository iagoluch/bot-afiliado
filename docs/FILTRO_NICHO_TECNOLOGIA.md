# Filtro tecnológico do Bot Afiliado

O nicho de publicação no **Telegram-only** é exclusivamente tecnologia:
computadores, notebooks, peças de PC, armazenamento, periféricos, celulares,
tablets, capas/películas/carregadores, redes, dispositivos gamer, áudio,
streaming, segurança eletrônica e acessórios relacionados.

A filtragem compara **título, categoria e marca** normalizados (sem acentos
ou diferenças entre hífen/espaço). Exemplo: `USB-C`, `placa-mãe` e `Wi-Fi`
não são descartados. Palavras genéricas isoladas como `capa`, `kit`,
`suporte` ou `acessório` não bastam: o item precisa trazer termo técnico
específico no título/categoria/marca. A descrição promocional e a URL não
servem para aprovar uma oferta.

A checagem ocorre em três pontos:
1. **Importação assistida:** ofertas fora do nicho são rejeitadas; o contador
   `rejected_non_technology` indica quantas.
2. **Criação de fila Telegram-only:** nenhuma oferta fora do nicho entra na
   fila, mesmo que tenha score alto ou confirmação manual.
3. **Antes do envio:** se título/categoria/marca mudarem para fora do nicho,
   o envio é descartado. A fila legada não pode contornar a regra.

As configurações de autenticação, comprovação de link comissionado e
revisão de preço/estoque continuam independentes e obrigatórias.

## Configuração

Por padrão é usada a lista ampliada em `app/config.py`, chamada
`DEFAULT_TECH_OFFER_TERMS`. A variável opcional `TECH_OFFER_TERMS`
**substitui** a lista inteira (se configurada, precisa incluir todos os
termos desejados). Apague essa variável do `.env` para usar o catálogo
completo do projeto. Evite termos genéricos como `produto`, `kit`,
`oferta`, pois permitem itens não tecnológicos.

A filtragem por termos é uma primeira barreira determinística; não é uma
classificação semântica infalível. Antes da operação 100% autônoma, a
integração de coleta deve mapear categorias oficiais para evitar ambiguidades.
Nenhum termo garante preço correto, permissão de afiliação ou comissão.

A alteração do código local exige `git pull --ff-only origin main`; se o
worker estiver ativo, reinicie-o para carregar as regras novas.
