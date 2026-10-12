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

## Diagnóstico de busca com zero produtos

Se o popup mostrar `BUSCA_SEM_LINKS_DE_PRODUTO` com `anchors` maior que
zero e `productLinks=0`, ainda não sabemos se a busca foi redirecionada,
se o Chrome exibiu uma página de acesso ou se os links estão em outro layout.
Não há benefício em ampliar seletores sem inspecionar a página real.

1. Desative a busca a cada 6 horas no popup.
2. Atualize `main` com `git pull --ff-only origin main`.
3. Em `chrome://extensions`, clique **Recarregar** em IAgo Tech.
4. Abra o popup e clique **Diagnosticar página de busca (sem publicar)**.
5. Será aberta uma **guia visível** de resultados de notebook, que permanecerá
   aberta para inspeção. Abra novamente o popup para ver o relatório persistido.

O relatório inclui endereço final **sem querystring**, título, cabeçalho,
contagem de links, amostras de hosts/caminhos de links e indicação de aviso
de acesso. **Não extrai cookies, tokens ou dados de sessão**, não chama o
Gerador de Links, não envia nada à fila e não publica no Telegram.

Se a guia exibida pedir login ou apresentar uma restrição, não use novas
identidades, redes ou técnicas para contornar o acesso. Esse diagnóstico
serve para distinguir uma mudança de layout de um bloqueio/redirect normal.
A correção de seletores deve ser feita apenas depois de verificar o layout
que efetivamente apareceu no navegador autenticado.

## Correção da busca em segundo plano no Acer

O diagnóstico real de 12/10 mostrou `RESULTADOS_ENCONTRADOS` com
**136 cards, 52 links de produtos e 20 resultados** na página
`lista.mercadolivre.com.br/notebook`, sem aviso de acesso.

A busca antiga usava guia inativa e podia obter zero cards. Agora a
extensão primeiro tenta a execução em segundo plano e, se o resultado
vier vazio, **ativa a mesma guia uma única vez** para permitir que a
página renderize a grade. Ela também corrige a variável de deduplicação
`seen`, que estava ausente e interrompia a execução quando encontrava
produtos. O fluxo JavaScript passou a ser testado até o encaminhamento
à ponte Python, sem executar chamadas externas nos testes.

A aba de automação é fechada ao fim; a guia pessoal do Chrome permanece.
A primeira execução atualizada pode alternar a guia ativa brevemente.
Só tente o botão **Executar 1 busca e gerar link** depois de atualizar
com `git pull --ff-only origin main` e clicar **Recarregar** em
`chrome://extensions`.

Se resultar em `SEM_OFERTA_APROVADA`, observe as contagens separadas:
`product` (não conseguiu validar preço/compra), `generator`
(não recebeu link oficial) ou `bridge` (ponte rejeitou a oferta).
Não repita execuções sem inspecionar o estágio que falhou. Se ocorrer
`FILA_CRIADA`, o worker pode publicar uma oferta real.

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
