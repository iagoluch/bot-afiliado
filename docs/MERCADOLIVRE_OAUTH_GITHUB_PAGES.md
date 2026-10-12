# Conectar Mercado Livre Developers com GitHub Pages

**Objetivo:** o GitHub Pages fornece somente a página HTTPS de retorno OAuth. O Client Secret e os tokens ficam exclusivamente no Acer. Isso não libera automaticamente uma API de afiliados, nem gera links comissionados.

## 1. Publicar a página no GitHub

Acesse https://github.com/iagoluch/bot-afiliado/settings/pages e configure Build and deployment → Deploy from a branch → main → /(root) → Save.

Aguarde o GitHub publicar a página e confira no navegador:

**https://iagoluch.github.io/bot-afiliado/**

A página deve mostrar 'Bot Afiliado · Autorização Mercado Livre'. Se retornar 404, confira o deploy em Settings → Pages antes de iniciar OAuth.

No ML Developers, a URL cadastrada deve ser exatamente `https://iagoluch.github.io/bot-afiliado/` (com barra final, sem query). O valor do campo de configuração local também precisa corresponder, caractere por caractere.

## 2. Configuração local no Acer

Na pasta `~/bot-afiliado`, atualize a main e abra o arquivo `.env` (nunca faça commit desse arquivo):

```bash
cd ~/bot-afiliado
git pull --ff-only origin main
nano .env
```

Adicione as seguintes variáveis, copiando os dados apenas do painel privado do ML Developers:

```dotenv
MERCADOLIVRE_APP_ID=SEU_APP_ID
MERCADOLIVRE_CLIENT_SECRET=SEU_CLIENT_SECRET
MERCADOLIVRE_REDIRECT_URI=https://iagoluch.github.io/bot-afiliado/
MERCADOLIVRE_PKCE_ENABLED=false
```

Use `MERCADOLIVRE_PKCE_ENABLED=true` **somente após habilitar PKCE no ML Developers**. Recomenda-se PKCE para maior proteção, mas o valor deve coincidir com a configuração real da aplicação.

## 3. Primeiro login OAuth

No Acer, gere link e estado aleatório:

```bash
.venv/bin/python -m app.mercadolivre_oauth start
```

Abra o endereço impresso no navegador do Acer ou tablet, entre com a conta proprietária no Mercado Livre e autorize. No retorno ao GitHub Pages, use os botões para copiar **código** e **state**.

Em até 15 minutos, volte ao **mesmo Acer** e execute:

```bash
.venv/bin/python -m app.mercadolivre_oauth finish
```

Cole código e state apenas no terminal, quando solicitado. O programa valida o state e troca o código por tokens na API oficial. Os tokens são salvos localmente em `data/mercadolivre_oauth_tokens.json` (permissão 0600), com rotação atômica.

Verifique a conexão sem expor nenhum segredo:

```bash
.venv/bin/python -m app.mercadolivre_oauth status
```

Para renovar depois, quando o access token estiver a até 5 minutos de expirar:

```bash
.venv/bin/python -m app.mercadolivre_oauth refresh
```

## 4. Restrições importantes

- Não copie Client Secret, código OAuth ou tokens para chats, issues ou commits.
- A página do GitHub Pages não faz chamadas de rede e não grava códigos em cookies/localStorage; remove os parâmetros da barra assim que carrega.
- Um código OAuth é temporário. Se falhar ou o state não corresponder, reinicie com `start`.
- A renovação só usa o Refresh Token quando necessário. O Mercado Livre invalida o token de refresh usado e devolve um novo.
- A autenticação de APIs gerais do Mercado Livre não equivale a acesso à API do programa de afiliados. A coleta automática de links comissionados permanece não homologada.

Por último, verifique as limitações atuais das fontes:

```bash
.venv/bin/python -m app.cli collection-status
```

Fonte oficial de OAuth: https://developers.mercadolivre.com.br/autenticacao-e-autorizacao
