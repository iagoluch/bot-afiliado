# Mercado Livre — autopiloto no Acer

Este modo tenta executar **descoberta tecnológica → preço/estoque na página
oficial → Gerador de produtos recomendados → link comissionado original →
fila do worker Telegram**.

Implementação por navegador Playwright com perfil persistente, sem usar
endpoints privados, sem capturar/exportar cookies e sem contornar autenticação
ou CAPTCHA. A interface visual pode mudar; qualquer falta de campo faz a
execução falhar de modo fechado, sem gerar publicações.

## Instalar e configurar uma única vez no Acer

Use o usuário Linux que vai executar o serviço. Pare o worker no terminal
`Ctrl+C` durante a configuração para não haver concorrência na atualização.

```bash
cd ~/bot-afiliado
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m app.mercadolivre_autopilot setup
```

O comando `setup` abre o navegador. Faça login com a conta de afiliado,
abra **Central de Afiliados e Criadores → Gerador de produtos recomendados**,
selecione a etiqueta `telegram` (como no seu print) e pressione
**ENTER no terminal**, sem fechar a janela do navegador antes.
O bot confere a presença do `textarea`, do botão **Gerar** e da etiqueta.
O perfil fica em `data/ml_browser_profile/`; não copie essa pasta,
não faça commit e não execute usando o perfil do navegador pessoal.

A URL do gerador fica registrada em `data/ml_autopilot_config.json`
(apenas URL oficial). Não precisa colar cookie, código de sessão ou segredo
do navegador em outro lugar.

### Acer com Lubuntu/Ubuntu 26.04

**Necessário Playwright 1.61 ou superior.** O pin antigo `1.55.0` gera
`ERROR: Playwright does not support chromium on ubuntu26.04-x64` e,
consequentemente, `Executable doesn't exist at ~/.cache/ms-playwright/...`.
A versão fixada no projeto agora é `1.63.0`.

Se o erro ocorrer em uma instalação antiga, **não repita somente o comando
`playwright install`**: primeiro atualize o pacote no mesmo `.venv`:

```bash
cd ~/bot-afiliado
git pull --ff-only origin main
.venv/bin/python -m pip install --upgrade -r requirements-browser.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m app.mercadolivre_autopilot setup
```

O Playwright precisa da compilação de navegador compatível com sua versão.
Se a instalação ainda falhar, envie a saída de
`.venv/bin/python -m pip show playwright` e do comando de instalação —
não execute scripts externos para corrigir o cache e não use `--no-sandbox`.

Referências: https://playwright.dev/python/docs/intro e
https://github.com/microsoft/playwright/issues/40117.


## Teste da automação de uma oferta

Com o worker do Telegram ativo, execute **uma única vez**:

```bash
cd ~/bot-afiliado
ML_AUTO_HEADLESS=false .venv/bin/python -m app.mercadolivre_autopilot once --keyword "fone bluetooth"
```

O bot navega na busca pública, abre um resultado de tecnologia,
comprova botão de compra ativo e preço visível, abre o Gerador oficial,
envia um produto por vez e recupera o link oficial `meli.la`
ou `mercadolivre.com/sec/` pela própria página. Se qualquer condição
falhar, retorna `NO_VERIFIED_OFFER` ou erro `BLOCKED`;
**não publica um link comum**, não inventa preço ou estoque.

Se retornar `QUEUED` com `queued: 1`, o worker vai consumir o job
e publicar quando as regras de `DRY_RUN`, limite de posts e política
permitirem. O comando em si **não envia mensagem diretamente**.

Confirme:
```bash
.venv/bin/python -m app.cli events --limit 10
.venv/bin/python -m app.cli overview
```

Uma execução bem-sucedida do teste offline **não comprova** a execução
na sua sessão do Mercado Livre. Os seletores e o navegador real precisam
passar por este teste local de ponta a ponta uma vez. Se a página bloquear
navegação automática ou o portal mudar, a automação para em segurança,
sem técnicas de bypass.

## Automatizar a cada seis horas sem manter Chromium aberto

**Habilite somente após o teste `once` gerar uma URL correta e publicar
no grupo público.** O job abre o Chromium temporariamente em modo headless,
executa e fecha. Tenta uma oferta verificada por ciclo, alternando termos
tecnológicos. O worker é outro serviço.

Primeiro, encerre o worker iniciado manualmente com `Ctrl+C`.
Em seguida:

```bash
cd ~/bot-afiliado
mkdir -p ~/.config/systemd/user
cp deploy/systemd/bot-afiliado-telegram-worker.service ~/.config/systemd/user/
cp deploy/systemd/bot-afiliado-ml-autopilot.service ~/.config/systemd/user/
cp deploy/systemd/bot-afiliado-ml-autopilot.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now bot-afiliado-telegram-worker.service
systemctl --user enable --now bot-afiliado-ml-autopilot.timer
systemctl --user list-timers bot-afiliado-ml-autopilot.timer
```

Com `DRY_RUN=false` e `TELEGRAM_REAL_ENABLED=true`, esse sistema
pode **publicar automaticamente** os links aprovados pelo fluxo. Mantenha
`TELEGRAM_MAX_POSTS_PER_HOUR=1` durante o piloto.

```bash
systemctl --user status bot-afiliado-telegram-worker.service --no-pager
journalctl --user -u bot-afiliado-ml-autopilot.service -n 40 --no-pager
.venv/bin/python -m app.cli overview
```

Por padrão, o timer funciona com a sessão do usuário ativa. Em um Acer
sem sessão, pode ser necessário habilitar linger. Se o navegador perder
a autenticação, execute `setup` novamente. O script não tenta superar
desafios de acesso automaticamente.

Para desativar coleta (sem desligar seu worker):
```bash
systemctl --user disable --now bot-afiliado-ml-autopilot.timer
```

## Limites de produção

- Fonte atual: **interface web do Mercado Livre**; não é uma Affiliate API
  homologada. Os termos do programa e de desenvolvedores podem restringir
  robôs; cabe confirmar autorização para operação recorrente.
- Se não houver preço normal visível, botão de compra habilitado e
  link gerado pelo portal, a oferta **não é enviada**.
- Não publica mais de um candidato por varredura e bloqueia repetição
  do mesmo item durante 24h. A fila tem verificação de prazo de 15 min.
- O preço do produto é observado na página; pode mudar depois do
  momento da observação. Não afirmar desconto real sem histórico.
- Não agrega APIs alternativas da Shopee, cujo acesso está pendente.

**Checklist de homologação:** setup gravou URL, execução `once` retornou
`queued=1`, o evento `PUBLISHED` apareceu no banco e o post contém o
link curto certo. Só então ative o timer.
