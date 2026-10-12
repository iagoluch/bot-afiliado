# Agendamento de coleta Shopee no Acer

Este agendamento usa o serviço do próprio sistema (systemd user timer), sem
adicionar processos residentes. A cada seis horas, faz uma consulta limitada
à **Shopee Affiliate Open API**, filtra produtos tecnológicos e, quando
as credenciais estão aprovadas e a API responde, armazena candidatos
no SQLite. **Nunca gera fila nem publica no Telegram.**

**Não habilite antes de receber as credenciais de afiliado aprovadas e
comprovar que `shopee-affiliate-discover --keyword ssd --limit 3` funciona
na sua conta**. Não confundir com a API de vendedores da Shopee.

## Instalação (uma única vez, no Acer)

```bash
cd ~/bot-afiliado
git pull --ff-only origin main

mkdir -p ~/.config/systemd/user
cp deploy/systemd/bot-afiliado-shopee-discovery.service ~/.config/systemd/user/
cp deploy/systemd/bot-afiliado-shopee-discovery.timer ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now bot-afiliado-shopee-discovery.timer
systemctl --user list-timers bot-afiliado-shopee-discovery.timer
```

O serviço assume que o projeto está em `~/bot-afiliado` e que o ambiente
Python está em `.venv` — caminho atual do Acer. Ele lê o `.env`
existente localmente sem incluir segredos nos logs.

### Execução manual do job agendado (não publica)

```bash
systemctl --user start bot-afiliado-shopee-discovery.service
systemctl --user status bot-afiliado-shopee-discovery.service --no-pager
journalctl --user -u bot-afiliado-shopee-discovery.service -n 20 --no-pager
```

A operação `--stage` escreve somente candidatos com estoque
`UNKNOWN`, mesmo com `DRY_RUN=false`. O worker de Telegram pode ficar
aberto: não envia esses candidatos, nem os itens legados fora do filtro
de tecnologia. O job exige App ID e Secret aprovados; do contrário,
não realiza consulta externa.

### Desativar

```bash
systemctl --user disable --now bot-afiliado-shopee-discovery.timer
```

Observação: systemd user timer funciona enquanto a sessão do usuário
permanece ativa; para executar com o Acer sem login, pode ser necessário
habilitar o linger do usuário conforme a política da instalação Linux.

## Limitação comercial

Isso automatiza a **descoberta/armazenamento**, não a comprovação de estoque.
Não há promessa de comissão até a Shopee validar o programa, os links e
as vendas. A publicação automática será adicionada somente após existir
uma fonte de estoque autorizado/verificado e o contrato real estar homologado.
