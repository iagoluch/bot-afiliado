# BOT AFILIADO — contexto do repositório

Este projeto roda localmente em Python, FastAPI e SQLite. `main` é a branch de trabalho. Leia `STATUS.md`, `ARCHITECTURE.md` e `RUNBOOK.md` antes de alterar o fluxo. Preserve mudanças locais existentes e mantenha `DRY_RUN=true` por padrão.

## Notebook Acer com Lubuntu

O alvo de operação contínua é um notebook Acer com Intel i3-6100U, 4 GB de RAM, HDD, aproximadamente 8,5 GB de swap e sem GPU dedicada. O runtime local de IA é Ollama com `qwen3.5:2b`, uma inferência por vez, `think=false`, contexto 1024, temperatura 0.3 e keep-alive curto. Qwen 4B não é adequado para esse alvo.

Quando o usuário disser **“Traga o repositório para esse notebook”** no Codex do Acer: detecte o sistema e o diretório reais, consulte o estado Git e preserve qualquer trabalho local; obtenha ou atualize `iagoluch/bot-afiliado` na `main`; siga a seção Lubuntu de `RUNBOOK.md` para `.venv`, configuração local, checagens, worker, web e systemd. Nunca copie caminhos pessoais deste computador Windows para o Acer. Não baixe o modelo automaticamente; explique o comando manual ao usuário se ele estiver ausente.

## Limites operacionais

- Python determina produto, preços, descontos, cupom, estoque, URLs, tracking, score e compliance. IA local contribui somente texto genérico validado; saída inválida volta ao template.
- Não ative integrações Shopee, Telegram, Instagram, TikTok ou outras dependentes de aprovação, credencial, login ou OAuth. O status dessas pendências está em `STATUS.md`.
- Sem Docker, Redis, Celery, PostgreSQL ou Node. O worker e a web são processos locais separados; a fila e os estados persistem em SQLite.
- Valide alterações com testes offline. Testes não podem exigir marketplace, internet, Ollama instalado ou credencial externa.
