# BOT AFILIADO — contexto do repositório

Este projeto roda localmente em Python, FastAPI e SQLite. `main` é a branch de trabalho. Leia `STATUS.md`, `ARCHITECTURE.md` e `RUNBOOK.md` antes de alterar o fluxo. Preserve mudanças locais existentes e mantenha `DRY_RUN=true` por padrão.

## Notebook Acer com Lubuntu

O alvo de operação contínua é um notebook Acer com Intel i3-6100U, 4 GB de RAM, HDD, aproximadamente 8,5 GB de swap e sem GPU dedicada. O runtime não depende de LLM local. Gemini é o provider remoto opcional quando `GEMINI_API_KEY` estiver configurada; Granite GGUF via llama.cpp é fallback experimental, desligado por padrão e só deve ser ativado após homologação real nesse hardware. Sem provider disponível, `TemplateProvider` mantém o fluxo funcional.

Quando o usuário disser **“Traga o repositório para esse notebook”** no Codex do Acer: detecte o sistema e o diretório reais, consulte o estado Git e preserve qualquer trabalho local; obtenha ou atualize `iagoluch/bot-afiliado` na `main`; siga a seção Lubuntu de `RUNBOOK.md` para `.venv`, configuração local, checagens, worker, web e systemd. Nunca copie caminhos pessoais deste computador Windows para o Acer. Não baixe modelos automaticamente e não habilite Granite sem solicitação explícita.

## Limites operacionais

- Python determina produto, preços, descontos, cupom, estoque, URLs, tracking, score e compliance. IA contribui apenas conteúdo editorial validado; saída inválida, timeout, rate limit ou circuit breaker aberto volta ao próximo provider e por fim ao template.
- Não ative integrações Shopee, Telegram, Instagram, TikTok ou outras dependentes de aprovação, credencial, login ou OAuth. O status dessas pendências está em `STATUS.md`.
- Sem Docker, Redis, Celery, PostgreSQL ou Node. O worker e a web são processos locais separados; a fila e os estados persistem em SQLite.
- Valide alterações com testes offline. Testes não podem exigir marketplace, internet, Gemini real, modelo local ou credencial externa.
