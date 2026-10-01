# Status atual

Data: 01/10/2026. Componentes P0 a P3 e runtime local para Lubuntu implementados e validados offline sem publicação social externa; o MVP real ainda depende de acessos e aprovações.

Repositório GitHub privado: https://github.com/iagoluch/bot-afiliado (`main`).

A automação da etapa anterior foi pausada em 01/10/2026. Para validar o MVP real faltam aprovação e credenciais Shopee, token/chat e canal Telegram aprovado, domínio HTTPS público aprovado e export oficial de conversões. Instagram/TikTok e demais programas têm aprovações próprias descritas abaixo. Nenhum envio real foi feito.

## Runtime Lubuntu 24/7

- Homologação no Acer em 01/10/2026 confirmou a causa da configuração divergente: `Settings.from_env()` lia somente `os.environ`, enquanto apenas `start.sh` e systemd interpretavam `.env`. O carregamento agora é único em Python, ancorado na raiz, com precedência `ambiente > .env > padrão`; CLI, worker, web, scripts e units recebem a mesma configuração. O timeout efetivo do processo foi confirmado em 300 s.
- `REAL_LOCAL_AI` ainda não foi comprovado neste Acer. Com o Qwen descarregado, `copy-preview` expirou em 300 s e voltou corretamente ao template; o Ollama cancelou a requisição com HTTP 499 enquanto o runner ainda carregava o modelo. O host estava configurado com contexto global 2048, divergente do projeto; após corrigir para 1024 e limitar um modelo carregado, um ensaio diagnóstico com timeout de processo de 900 s também falhou após 927,35 s. O evento seguro registrou `TimeoutError` em 911.230 ms e o Ollama encerrou o load com HTTP 499 após cerca de 15 minutos, ainda em `llm server loading model`, com forte uso de HDD/swap. Não houve resposta válida do Qwen nem estado warm nessa rodada; o padrão permaneceu em 300 s.
- Após um load cancelado, `/api/tags` também apresentou latência acima de 1 s e erro 500 transitório. A sonda local agora permite até 3 s e uma única repetição para erro de transporte/timeout; modelo ausente ou payload inválido continuam falhando imediatamente. Cada inferência local registra somente provider, estado, duração, motivo de fallback e classe de erro em JSON no stderr, sem prompt, resposta, URL ou segredo.
- `OllamaProvider` local usa `qwen3.5:2b`, `think=false`, contexto 1024, temperatura 0.3 e keep-alive 2m; resposta fora da linguagem genérica permitida ou falha volta ao template. O P1 grava o hook seguro no ContentPackage de vídeo; fatos, captions, score e compliance permanecem em Python. Um lock compartilhado serializa Ollama e FFmpeg entre processos no Linux.
- `python -m app.worker` executa `run_tick`, fila Telegram em DRY_RUN e jobs P1 persistidos; heartbeat, idle, backoff, lock singleton e SIGTERM/SIGINT estão implementados. Jobs P1 nascem com o upsert, são versionados pelos fatos e modo DRY_RUN/REAL, recuperam PROCESSING após crash, ignoram versões obsoletas e reutilizam conteúdo de fatos inalterados entre slots.
- `runtime-status` e `/health?details=true` mostram apenas banco, worker/heartbeat/ciclo, profundidade da fila, Ollama/modelo, FFmpeg e DRY_RUN. `scripts/*.sh` e templates systemd usam caminhos do clone e usuário normal. A web permanece em `127.0.0.1:8000`.
- Validação no Acer: suíte completa com **164 testes aprovados** em 01/10/2026, executada em 35,17 s sem depender de Ollama, marketplace, credencial ou publicação externa. FFmpeg 8.0.1 foi instalado neste host; o teste de MP4 que antes era pulado passou em 8,43 s. O E2E offline cobre sample, ingestão, curadoria, fila `SIMULATED`, fallback `TEMPLATE_FALLBACK`, ContentPackage, assets locais, tracking `/go`, cinco cliques locais, importação idempotente de duas conversões, analytics, idle, heartbeat e persistência após restart. Nenhum redirect externo é seguido no teste.
- Smoke operacional descartável: worker real em `DRY_RUN` por três minutos, com banco e criativos em `/tmp`, Ollama e FFmpeg indisponíveis por configuração do ensaio; 1 oferta, fila `SIMULATED`, job `COMPLETED`, 7 pacotes e heartbeat, seguido de encerramento `STOPPED` por SIGTERM. Web real em loopback temporário respondeu 200 em `/health?details=true` e `/offers` e encerrou limpo. Nenhum destino externo recebeu chamada.

| COMPONENT | STATUS | TESTED | EXTERNAL DEPENDENCY | NEXT ACTION |
|---|---|---|---|---|
| Pesquisa oficial | COMPLETE_LOCAL | Shopee, Telegram, Meta, TikTok, Amazon, Awin, Mercado Livre, Admitad e llama.cpp revisados | Login pode alterar conteúdo acessível | Revalidar antes de uso externo |
| Shopee adapter | MANUAL_OR_PENDING | CSV/JSON, normalização e URLs | AppId/Secret e aprovação | Validar schema BR autenticado |
| SQLite/modelo canônico | COMPLETE | Upsert, histórico, estados e migração P1 | Nenhuma | Usar dados reais aprovados |
| Curadoria/deduplicação | COMPLETE | Score, histórico, fadiga, cooldown e desconto somente com duas observações anteriores | Histórico operacional | Ajustar pesos com dados reais |
| Telegram | WAITING_FOR_CREDENTIALS | Template, DRY_RUN, 429, revalidação da fila, entrega incerta sem retry e reconciliação local | Token, chat e canal aprovado | Teste real controlado |
| Tracking `/go/{id}` | COMPLETE | Clique, atribuição, 302, destino publicado preservado e 410 para oferta indisponível | Domínio público para uso externo | Configurar domínio aprovado |
| Conversões/analytics | MANUAL_COMPLETE_LOCAL | Importação idempotente e painel | Relatório oficial | Mapear export real Shopee |
| Site/hub `/offers` e `/o/{slug}` | COMPLETE | Pesquisa, 404, escape XSS e clique consciente | Domínio HTTPS aprovado | Publicar o domínio após aprovação |
| ContentPackage multicanal | COMPLETE | 6 formatos idempotentes por oferta, incluindo Telegram | Dados reais válidos | Revisão editorial inicial |
| Imagens Feed/Story | COMPLETE | PNG real 1080×1080 e 1080×1920, determinismo | Imagem oficial é opcional | Validar identidade visual própria |
| Reels generator | COMPLETE_WITH_OPTIONAL_BINARY | MP4 H.264 real, 1080×1920, 30 fps, 18 s e sem áudio; teste curto com FFmpeg passou no Acer | Aceitação do arquivo silencioso ainda não testada na Meta | Validar container controlado após aprovações |
| TikTok generator | PENDING_POLICY_REVIEW | MP4 rascunho real, 1080×1920, 15,03 s | Uso aprovado, OAuth e auditoria | Revisar política antes de qualquer API |
| Instagram queue | READY_FOR_PUBLISH_LOCAL | Feed, Story e Reel persistidos; somente Reel elegível para container Graph | Revisão, conta profissional, Page e OAuth | Validar container controlado; publicação permanece manual |
| Instagram Reel Graph | CONTAINER_READY_MEDIA_PUBLISH_BLOCKED | 9 testes: contrato HTTP/CLI, DRY_RUN, gates, estados, segredo, asset vinculado e bloqueio sem rede | Conta/Page/permissão, host HTTPS, token e versão ativa; API de rótulo não confirmada | Criar/consultar container controlado; publicar manualmente com rótulo |
| Amazon Creators API BR | WAITING_FOR_WRITTEN_APPROVAL_AND_RETENTION_DESIGN | OAuth/SearchItems/parser mockados, UA e limite 40 KB | Aprovação escrita e arquitetura de retenção | Manter rede/import/hub/publicação bloqueados |
| Awin Product Feed | COMPLETE_LOCAL | CSV/gzip, limites, BRL, URL e idempotência | Feed oficial real; API de transações separada | Validar feed de programa aprovado |
| Mercado Livre | COMPLETE_MANUAL | CSV/JSON e URL oficial preservada | Geração manual no portal/barra | Validar canal público aprovado |
| Admitad Product Feed | WAITING_FOR_REAL_EXPORT | CSV local, BRL, limites, URL/SubID, CLI import-only e bloqueio de distribuição | Export de programa/ad space aprovados e regras do anunciante | Validar schema/link reais antes de habilitar ciclo |
| Magalu, AliExpress, SHEIN | MANUAL_OR_PENDING | Pesquisa oficial e bloqueio testado no hub, fila e redirect | Conta, links reais, termos e contrato vigente por programa | Validar portal/export; só então considerar adapter |
| Painel administrativo | COMPLETE_LOCAL | 14 páginas, Basic Auth, fail closed público e segredo oculto | HTTPS e senha para exposição | Operar localmente ou atrás de HTTPS |
| Analytics segmentado | COMPLETE_LOCAL | 9 dimensões, CVR/EPC/receita/comissão; CTR real-only | Fonte oficial de impressões | Integrar export real antes de CTR |
| Feedback determinístico | COMPLETE_LOCAL | Produto/loja/categoria/canal/hora, CTR/CVR/EPC/receita com volume mínimo, smoothing e limite; 1 evento não altera prioridade | Histórico real suficiente | Reavaliar pesos com operação real |
| Logs estruturados | COMPLETE_LOCAL | SQLite limitado, CLI `events`, ciclo DRY_RUN e falha de log sem repetição | Nenhuma | Consultar eventos na operação |
| Compliance configurável | COMPLETE_LOCAL | Regras merchant/canal, freshness, Amazon/ML/Admitad fail closed | Termos e canais aprovados | Revisar overrides antes da exposição |
| IA local auxiliar | TEMPLATE_FALLBACK_VALIDATED_REAL_LOCAL_AI_BLOCKED | Mocks offline, timeout, bloqueio de alegações, CLI somente leitura e cold real pelo bot; Qwen não concluiu load em 900 s | Limite de RAM/HDD/swap do Acer durante load do `qwen3.5:2b` | Manter fallback; só declarar IA real após o Qwen responder pelo bot |

## Dependências externas pendentes

- Aprovação Shopee e cadastro/aprovação do site e dos canais.
- AppId/Secret para contrato oficial brasileiro e token/chat Telegram para publicação real.
- Domínio HTTPS público aprovado para o hub e tracking.
- Conta profissional Meta, Page vinculada pelo Facebook Login, `instagram_content_publish`, versão Graph ativa e revisão editorial.
- Host HTTPS público que exponha o MP4 em caminho derivado de `CREATIVES_PATH`; processamento real do arquivo silencioso ainda não foi testado.
- Contrato oficial para aplicar o rótulo de parceria paga via API; enquanto ausente, `media_publish` permanece bloqueado e a postagem é manual.
- App TikTok, `video.publish`, autorização do criador, auditoria e validação explícita do caso de uso promocional.
- Aprovação escrita Amazon e desenho de retenção/uso compatível com Product Advertising Content; credenciais isoladamente não liberam busca.
- Feed Awin de programa aprovado; chave de feed e token da Partner API continuam separados.
- Link Mercado Livre gerado pelo portal/barra oficial e canal público permitido.
- Export Admitad de programa/ad space aprovados, com confirmação de que `url` é link afiliado da rede; sem ele a distribuição permanece bloqueada.
- Provider remoto de IA pendente de contrato e política de dados; a operação local não depende dele.

## Evidência de validação

- `scripts\test.bat`: 111 testes aprovados em 01/10/2026, com `DRY_RUN=true`, incluindo fluxo P0–P3, tracking, reconciliação Telegram, desconto histórico verificável, migração de rascunhos, feedback e limites. O banco operacional `data/affiliate.db` tinha 0 ofertas, 0 itens na fila, 0 publicações, 0 cliques e 0 conversões após o teste.
- Validação direcionada da atribuição de conversões: 8 testes aprovados. Um relatório com `click_id` e `offer_id`/canal divergentes agora é recusado; campos ausentes são preenchidos pelo clique, sem alterar o registro anterior quando a reimportação conflita.
- Revisão P3 Magalu/AliExpress/SHEIN: documentos oficiais ligados em `RESEARCH.md`; seis testes direcionados confirmam revisão obrigatória e que oferta SHEIN não aparece no hub, não entra na fila Telegram nem habilita `/go`. Dois testes Awin passaram após a mudança de prioridade da regra por merchant. Não houve integração de rede ou publicação externa.
- Eventos operacionais: 3 testes novos aprovados, incluindo CLI real em `DRY_RUN=true` com 1 oferta, 1 item e publicação `SIMULATED`; 7 testes direcionados de scheduler/Telegram e 10 de CLI social/Admitad também passaram. Os eventos armazenam somente metadados seguros, e falha de log não transforma uma publicação concluída em tentativa repetida.
- Revalidação da fila Telegram: quatro testes novos confirmam que mudança de preço, indisponibilidade e cupom expirado não chamam o publisher e que publicação já registrada não pode ser descartada. O módulo P0, os limites Telegram e esses casos passaram juntos: 28 testes. Uma fila `PROCESSING` após interrupção ainda exige reconciliação humana para evitar duplicata.
- Reconciliação Telegram: 37 testes direcionados passaram. Transporte incerto, HTTP 5xx e resposta inválida ficam em `PROCESSING`, sem segundo envio; falha ao persistir um envio confirmado também preserva essa proteção. CLI local lista pendências sem URL afiliada e registra publicação confirmada por `message_id` ou ausência confirmada, em transação com evento auditável. A decisão sobre o que ocorreu no canal continua humana; nenhuma chamada real à Bot API foi feita.
- Fila Telegram guarda a URL afiliada no enfileiramento; se ela mudar antes do envio, o item é descartado. Novos posts usam `publication_key` para vincular `/go` à URL arquivada, com validação de oferta/canal/campanha/criativo. Chave inválida, não publicada ou simulada recebe 404 sem clique; links legados e do hub continuam com a URL atual. Testes P0/P1/P2/freshness/reconciliação: 50 aprovados; teste direcionado adicional comprovou bloqueio do link simulado. Nenhum redirect foi exercido contra marketplace real.
- `/go` e o CTA do hub agora revalidam preço, estoque, validade e cupom no momento do acesso. Oferta indisponível retorna 410 sem registrar clique, inclusive com chave de publicação; catálogo/página retiram o CTA. Os 39 testes P0/P3 passaram juntos após a alteração. Nenhum redirect externo foi seguido nos testes.
- Ciclo descartável atual com `DRY_RUN=true` e `examples/shopee_offers.sample.csv`: 1 oferta importada, 1 item enfileirado e 1 publicação `SIMULATED`; nenhum efeito externo.
- Preço anterior do CSV manual Shopee/Mercado Livre permanece apenas como metadado não verificado. Site, Telegram e criativos só alegam queda/percentual quando duas observações anteriores distintas no SQLite comprovam preço maior; importação inicial de oferta forte continua elegível sem alegar desconto.
- Upgrade do SQLite cancela rascunhos sociais e remove da fila Telegram itens não publicados que possam conter o preço riscado legado. No banco local `data/affiliate.db`, a inspeção encontrou 0 ofertas, 0 pacotes e 0 itens na fila.
- Instagram: 9 testes aprovados confirmam origem Graph fixa, Bearer fora da URL/corpo, redirects recusados, URL derivada do MP4 persistido, CLI com zero rede em DRY_RUN, gates de conta/container e bloqueio de `media_publish` sem chamada nem mudança de estado.
- Admitad: 13 testes do adapter e teste CLI confirmam importação local, preservação de SubID e zero itens em filas; teste de compliance confirma bloqueio de distribuição/redirect antes da revisão. Teste de hub confirma catálogo oculto, página e `/go` em 403, sem clique gravado.
- IA local: 4 testes confirmam fallback, filtragem de sugestão e CLI `copy-preview` sem escrita em pacote/fila.
- P2 cobre contrato HTTP Amazon mockado, cache OAuth curto, recusa de redirect, tag/moeda/segredo, Awin CSV/gzip/limites/BRL, Mercado Livre manual, slots por adapter e E2E multicanal idempotente.
- Amazon P2 foi corrigido para fail closed após revisão dos termos BR: testes cobrem contrato mockado; CLI/pipeline, hub, `/go`, imagem e filas operacionais permanecem bloqueados.
- E2E P3 descartável em `DRY_RUN=true`: Mercado Livre importado, Telegram `SIMULATED`, 6 ContentPackages, 14 páginas administrativas, 1 clique 302, CTR `null`, `publications=0` e limpeza confirmada.
- E2E P1 descartável: 1 oferta GOOD, 6 ContentPackages, 3 itens Instagram `READY_FOR_PUBLISH`, 1 TikTok `PENDING_POLICY_REVIEW`, Telegram canônico sem enqueue/publicação e `external_publications=0`.
- Inspeção do FFmpeg: Reel H.264/yuv420p 1080×1920, 30 fps, 18,00 s e sem trilha de áudio; TikTok H.264/yuv420p 1080×1920, 30 fps, 15,03 s. A Meta não recebeu esses arquivos nesta etapa.
- Fallback sem binário: Feed, Story, thumbnails e frames gerados; Reel persiste `ASSET_PENDING` com instrução `FFMPEG_PATH`.
- Segurança do hub: conteúdo malicioso escapado, imagem externa arbitrária não embutida e nenhum clique gravado antes do botão `/go`.
- Segurança administrativa: painel/API retornam 401 sem Basic quando configurado; bind público falha sem `ADMIN_PASSWORD` ou HTTPS; Settings não expõe senhas/tokens/secrets.
- Persistência: cada contexto SQLite fecha a conexão após commit/rollback; o E2E descartável remove o banco no Windows sem arquivo bloqueado.
- Feedback: um evento isolado preserva o score; histórico real com volume mínimo altera a prioridade com smoothing por produto, loja, categoria, canal e hora, limitado a ±8 pontos. Conversões rejeitadas ou pendentes não entram em EPC/receita.

Nenhuma publicação social externa real foi executada nesta etapa.
