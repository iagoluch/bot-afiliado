# Campanha Mercado Livre — variedade + Nemotron 3 Super

## Objetivo operacional

A extensão Chrome executa uma busca a cada 5 minutos e alterna 24 nichos:
SSD, áudio, notebook, mouse, teclado, monitor, celular, RAM, tablet,
carregador, GPU, rede, webcam, placa-mãe, smartwatch, videogame,
impressora, pendrive, gabinete, fonte, smart home, armazenamento, CPU e
microfone.

Isso permite até 4 ofertas a cada 20 minutos, ou 12 por hora, SE houver
produtos tecnológicos com preço, disponibilidade e link comissionado válidos.
Nenhuma meta justifica ofertas inventadas. A mesma categoria tem cooldown
de uma hora no banco e o mesmo produto não é republicado dentro de 24h.

**O worker possui limite independente**: ajuste no arquivo .env do Acer:

    TELEGRAM_MAX_POSTS_PER_HOUR=12

Se o limite continuar em 4, o worker publicará no máximo quatro por hora,
mesmo com a extensão tentando pesquisar a cada cinco minutos. A alteração
não liga publicação real por conta própria e não garante quantidade mínima.

## Nemotron 3 Super da NVIDIA

A API NVIDIA usa:
- Endpoint: https://integrate.api.nvidia.com/v1/chat/completions
- Modelo: nvidia/nemotron-3-super-120b-a12b
- Modelo oficial: https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b

Adicionar SOMENTE no .env local do Acer:

    NVIDIA_API_KEY=SUA_CHAVE_NVIDIA
    ML_COPY_NEMOTRON_ENABLED=true

Não publique a chave, nem a envie ao chat ou GitHub.
O Cloudflare pode continuar configurado para outras funções. O Nemotron
gera somente uma frase adicional curta a partir do título. O preço,
condição Pix, link de afiliado e identificação publicitária são colocados
pelo código já validado. A resposta da IA é validada e salva na oferta
para que a mensagem permaneça igual entre preparação e publicação.

Chamadas com até 8 segundos de timeout, sem retries e com resposta máxima
32KB. Se a API falhar, tiver quota atingida ou produzir texto inválido, o
bot usa o template tradicional. Nemotron não bloqueia a publicação.

## Ativação controlada

1. Faça git pull --ff-only origin main no Acer (inclui correção de preço PR #18).
2. Atualize o .env local com TELEGRAM_MAX_POSTS_PER_HOUR, NVIDIA_API_KEY
   e ML_COPY_NEMOTRON_ENABLED, sem imprimir os segredos no terminal.
3. Recarregue a extensão em chrome://extensions (não reinstale a ponte).
4. Reinicie um único worker para ler as novas configurações.
5. Teste uma oferta, confira preço, Pix, copy e link no Telegram.
6. Ative Buscar até 1 oferta a cada 5 minutos na extensão Chrome.

Não rode o timer Playwright antigo em paralelo. O Chrome normal precisa
permanecer aberto e autenticado. O alarme não acorda o Acer suspenso,
e a plataforma pode impor limites. Qualidade e regras de afiliado
continuam prioritárias à quantidade.
