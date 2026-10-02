# Manual operacional — BOT AFILIADO

Guia de uso diário no Acer/Lubuntu. O projeto inicia em modo de simulação; mantenha-o assim até validar o fluxo completo.

## Instalar e testar

```bash
git clone https://github.com/iagoluch/bot-afiliado.git
cd bot-afiliado
bash scripts/install.sh
bash scripts/test.sh
.venv/bin/python -m app.cli init-db
.venv/bin/python -m app.cli runtime-status
```

O instalador cria o ambiente Python, banco/diretórios locais e configuração inicial. FFmpeg é opcional: sem ele imagens e storyboards continuam funcionando, enquanto vídeo fica pendente.

## Primeiro fluxo ponta a ponta, sem publicação externa

```bash
.venv/bin/python -m app.cli cycle examples/shopee_offers.sample.csv --campaign primeiro-teste --adapter shopee
.venv/bin/python -m app.cli p1-cycle examples/shopee_offers.sample.csv --campaign primeiro-p1 --adapter shopee
.venv/bin/python -m app.cli overview
.venv/bin/python -m app.cli social-queue
.venv/bin/python -m app.cli events --limit 20
```

Esse caminho exercita ingestão, validação, curadoria, conteúdo, fila e registros locais.

## Web e worker

Terminal 1:

```bash
bash scripts/start.sh web
```

Abra `http://127.0.0.1:8000`.

Terminal 2:

```bash
bash scripts/start.sh worker
```

Status:

```bash
bash scripts/status.sh
.venv/bin/python -m app.cli runtime-status
.venv/bin/python -m app.cli events --limit 50
```

O worker usa lock exclusivo, heartbeat, scheduler e SQLite persistente. Não execute duas instâncias. Ctrl+C encerra processos iniciados no terminal.

Para serviços persistentes:

```bash
bash scripts/install-systemd.sh
bash scripts/status.sh
journalctl -u bot-afiliado-worker.service -u bot-afiliado-web.service -n 100 --no-pager
```

## Importar ofertas

Use somente arquivos e links obtidos por meios permitidos pelo programa afiliado.

```bash
.venv/bin/python -m app.cli import-offers caminho/ofertas.csv --adapter shopee
.venv/bin/python -m app.cli cycle caminho/ofertas.csv --adapter shopee --campaign validacao-real
```

Também existem adapters `awin` e `mercadolivre`; veja `RUNBOOK.md` para seus formatos e restrições. Não habilite publicação real apenas porque a importação funcionou.

## Copy e criativos

```bash
.venv/bin/python -m app.cli copy-preview 1 --channel telegram
.venv/bin/python -m app.cli copy-preview 1 --channel instagram_feed
.venv/bin/python -m app.cli p1-offer 1 --campaign editorial
.venv/bin/python -m app.cli social-queue
```

A IA é opcional e apenas editorial. Sem provider, o projeto usa templates. Preço, estoque, URLs, tracking e compliance permanecem determinísticos em Python.

## Publicação e fila

Antes de qualquer publicação externa, valide um ciclo completo em simulação, confira o link afiliado, o conteúdo, as regras do merchant e o status operacional. Credenciais ficam somente na configuração local e nunca no Git.

Telegram de resultado incerto permanece em `PROCESSING` para impedir reenvio cego. Pare o worker e liste:

```bash
.venv/bin/python -m app.cli telegram-processing
```

Depois confira manualmente o canal e use o procedimento de reconciliação de `RUNBOOK.md`.

Instagram Feed/Story continuam manuais. Reel possui preparação de container, mas a publicação afiliada automática permanece bloqueada pelo gate de compliance. TikTok permanece em revisão de política.

## Tracking, conversões e analytics

Links públicos `/go/{offer_id}` registram o clique antes do destino afiliado, sujeitos a validade e compliance.

```bash
.venv/bin/python -m app.cli import-conversions caminho/conversoes.csv
.venv/bin/python -m app.cli overview
```

O import é idempotente por rede e pedido externo. Quando há `click_id`, a atribuição é conferida contra o clique persistido. O painel local contém Overview e Analytics. Simulação não representa receita ou audiência real.

## Reinício e recuperação

Após reboot, reinicie web/worker ou os serviços. Confira:

```bash
bash scripts/status.sh
.venv/bin/python -m app.cli runtime-status
.venv/bin/python -m app.cli events --limit 50
```

Não apague manualmente itens `PROCESSING`. Falhas de envio incertas devem ser reconciliadas, não reenviadas.

Erros comuns:
- ambiente ausente: execute `bash scripts/install.sh`;
- FFmpeg ausente: vídeos ficam pendentes, imagens continuam;
- fila parada: confira `runtime-status`, `events`, fonte configurada e logs;
- worker já ativo: não inicie segunda instância;
- publicação bloqueada: confira configuração externa, HTTPS, regra do merchant e compliance; não contorne o gate.

## Backup e atualização

Com processos parados:

```bash
cp data/affiliate.db "data/affiliate-$(date +%Y%m%d-%H%M%S).db"
git pull --ff-only
bash scripts/install.sh
bash scripts/test.sh
```

Depois reinicie e confira `runtime-status`.

## Acer com 4 GB

Mantenha IA local experimental desligada. Evite múltiplos workers e renderizações concorrentes. Templates permitem operação sem modelo local; o pipeline usa processamento sequencial e SQLite.

## Checklist para primeira receita

Antes de ativar uma saída real, confirme: testes verdes; runtime saudável; oferta real de fonte permitida; link afiliado conferido; conteúdo revisado; ciclo de simulação completo; canal permitido pelo merchant; tracking público funcional quando necessário; e relatório de conversões disponível.

Comece com volume baixo e valide a primeira publicação, clique e conversão antes de ampliar. Aprovação de programa afiliado, acesso de plataforma, domínio público, permissões de rede social e vendas reais dependem de terceiros e não devem ser contornados.

Para configuração avançada, formatos de adapters, políticas, Instagram e troubleshooting detalhado, consulte `RUNBOOK.md`.
