# Mercado Livre no Chrome autenticado — alternativa ao Playwright

## Por que esse método existe

O navegador isolado do Playwright no Acer mostrou **limite de tentativas no login**.
Isso **não demonstra bloqueio da conta inteira**. Este método usa uma
extensão Manifest V3 no **Chrome/Chromium normal que já esteja legitimamente
autenticado** no Mercado Livre.

Não usa `playwright setup`, outro perfil, cookies exportados, remote debugging,
API privada, CAPTCHA bypass, proxy ou automação de login.

A extensão pesquisa produtos de tecnologia na interface pública, lê apenas os
dados visíveis (título, preço e presença do botão **Comprar agora**), abre o
**Gerador de produtos recomendados** da Central de Afiliados, gera **um
produto por vez** com a etiqueta já selecionada `telegram` e transmite o
link comissionado ao worker Python local. Nada é enviado ao Telegram caso não
seja encontrado um link válido.

A comunicação não abre porta HTTP: usa **Chrome Native Messaging** com ID da
extensão registrado no Linux. Não lê senhas ou cookies nem envia tokens à página.

Fontes oficiais:
- https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging
- https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions
- https://www.mercadolivre.com.br/l/afiliados-gere-seus-links

## 1. Abra o Chrome/Chromium normal no Acer

**Não use a janela criada pelo Playwright.** No navegador normal,
acesse sua conta Mercado Livre. Se a Central ainda exigir login ou houver
mensagem de limite de tentativas, não tente contornar o desafio;
aguarde a liberação normal da autenticação e acesse pelo fluxo oficial.
Não reinstale perfil nem exporte cookies de outro navegador.

## 2. Instale a extensão local (uma vez)

Pare o worker somente para atualizar o projeto, depois reinicie
normalmente. No terminal:

```bash
cd ~/bot-afiliado
git pull --ff-only origin main
```

No navegador Chrome/Chromium **normal**:

1. Abra `chrome://extensions`.
2. Ative **Modo do desenvolvedor**.
3. Clique em **Carregar sem compactação**.
4. Selecione a pasta `/home/iagol/bot-afiliado/extensions/ml-chrome`.
5. Copie o **ID de 32 letras** mostrado no cartão da extensão.

No Acer, registre o ID no host nativo, substituindo só o ID:

```bash
cd ~/bot-afiliado
.venv/bin/python -m scripts.install_ml_chrome_bridge ID_DA_EXTENSAO
```

O comando instala arquivos do usuário em
`~/.config/google-chrome/NativeMessagingHosts/` e
`~/.config/chromium/NativeMessagingHosts/`, sem `sudo`.
Retorne a `chrome://extensions` e clique em **Recarregar** na extensão.
Ao abrir a extensão, **Ponte local: OK** confirma a comunicação Python.

Não precisa usar `playwright install`.

## 3. Associe o Gerador do ML

Em uma guia normal já autenticada, abra a **Central de Afiliados e Criadores →
Gerador de produtos recomendados**. Selecione a etiqueta **telegram**, como no
print. Com essa guia ativa, clique no ícone **IAgo Tech — Mercado Livre**
e no botão **Registrar esta aba como Gerador**.

O retorno `GERADOR_CONFIGURADO` identifica a página certa.
Ele não realiza login nem gera um link nesta etapa.

## 4. Teste um ciclo antes de ligar a recorrência

Com worker iniciado e a configuração Telegram revisada, clique em
**Executar 1 busca e gerar link**. Este botão **pode resultar em publicação
real** se `DRY_RUN=false` e `TELEGRAM_REAL_ENABLED=true`.
O bot só aceita links oficiais gerados pela interface, preço numérico,
botão Comprar agora ativo e produtos do nicho tecnológico.

Confira no Acer:

```bash
.venv/bin/python -m app.cli events --limit 5
.venv/bin/python -m app.cli overview
```

Se vir `FILA_CRIADA` no popup, e posteriormente `PUBLISHED` nos eventos,
confira que a mensagem no Telegram contém o link do produto certo.

Se aparecer `SEM_OFERTA_APROVADA`, nenhum envio foi efetuado. Isso significa
que a página não apresentou os elementos esperados, que o botão de compra não
foi confirmado ou que o Gerador não apresentou link novo.
O código não inventará links nem preços.

## 5. Autonomia de seis em seis horas

Somente **depois** do teste real e de conferir seu grupo, ative a chave
**Buscar a cada 6 horas (Chrome aberto)** no popup. A extensão acorda em
intervalos de seis horas, abre uma guia em segundo plano, tenta uma oferta
por ciclo e fecha a guia. A fila e os limites de publicação pertencem ao
worker Python já existente, que deve permanecer ligado.

**O Chrome precisa ficar aberto com a sessão autorizada ativa.** Os alarmes
não iniciam o navegador quando ele está fechado. Não rode o timer Playwright
`bot-afiliado-ml-autopilot.timer` em paralelo: é outro mecanismo.

Para desligar a recorrência, desative a chave na extensão.

## Manutenção e segurança

- Se a conta requer login, a extensão **para**; não reinicia sessão
  automaticamente, não responde autenticação e não contorna restrições.
- A extensão só tem permissões de páginas Mercado Livre e de mensagem
  nativa; não solicita permissão de cookies e não roda em outros sites.
- O host nativo só aceita o ID instalado, validado contra 32 letras a–p.
- Os dados recebidos do navegador são revalidados antes de ir ao SQLite.
- Sem link comissionado gerado no Gerador, **não existe publicação**.
- A interface do site muda; os seletores precisam ser homologados no Acer.
  Testes de DOM offline não confirmam que a UI atual gera links.
- A plataforma pode restringir automatização de sua interface; respeite
  qualquer desafio e as regras do programa.
