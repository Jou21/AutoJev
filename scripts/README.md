# Validação local

O servidor oficial [Playwright MCP](https://github.com/microsoft/playwright-mcp)
foi registrado no Codex como `playwright`. Nenhuma chave do OpenRouter é passada
ao MCP. A validação de navegador usa o protocolo MCP, não uma automação paralela
que contorne o explorador.

Instalação usada nesta máquina, fora do repositório:

```bash
npm install --prefix /home/jonny/.local/share/autojev-playwright-mcp @playwright/mcp@0.0.83 @modelcontextprotocol/sdk@1.32.0
```

Para executar a ponte local (com Google Chrome instalado):

```bash
node /home/jonny/.local/share/autojev-playwright-mcp/node_modules/@playwright/mcp/cli.js --headless --browser chrome --isolated --viewport-size 1440,900 --port 8931 --host 127.0.0.1 --shared-browser-context --output-dir ./data/mcp-validation
node scripts/mcp-browser.mjs session
```

A sessão aceita um comando JSON por linha, por exemplo:

```json
{"name":"browser_navigate","arguments":{"url":"http://localhost:3000"}}
{"name":"browser_snapshot","arguments":{}}
{"name":"browser_console_messages","arguments":{"level":"error"}}
```

Use `node scripts/mcp-browser.mjs list` para descobrir os esquemas atuais.
`AUTOJEV_MCP_MODULES` e `AUTOJEV_MCP_URL` permitem outro diretório/endpoint.
Não envie credenciais na linha de comando, nos comandos JSON ou em capturas.

Para uma checagem de negócio independente do SauceDemo pelo próprio MCP:

```bash
node scripts/validate-mcp.mjs
```

O script usa o login de `backend/.env` em memória, sem imprimir ou salvar os
transcripts de ferramentas que contêm esses valores. Verifica os seis produtos,
ordenação, composição do carrinho, três campos obrigatórios, subtotal, aritmética
do total e confirmação do pedido. É uma checagem independente de escopo fixo,
não uma afirmação de cobertura completa do explorador. Medições públicas ficam
em `data/mcp-validation/independent-business-checks.json`.

Para reproduzir comportamentos problemáticos com o perfil público
`problem_user`, registrando cada falha e continuando os outros checks:

```bash
node scripts/validate-demo-bugs.mjs
```

Resultados seguros ficam em `data/mcp-validation/problem-user-business-checks.json`.
São verificações de ordenação, identidade/preço dos produtos, retenção dos
campos e avanço do checkout; não pressupõem um baseline visual de design.

Para conferir a estabilidade do painel flutuante com o CSS/instalador reais,
em uma fixture isolada de localhost e sem chamadas de IA:

```bash
node scripts/validate-overlay-mcp.mjs
```

Verifica atualização sem recriar o painel, abertura imediata pelo J,
intervenção manual visível, cliques da automação atravessando o overlay,
ancoragem durante scroll e ausência da área com sombra abaixo do J, em desktop
e mobile. A fixture não altera o frontend nem o site testado.

Uma chamada curta de diagnóstico pode confirmar a capacidade real de visão do
modelo escolhido, com geração limitada e sem imprimir a chave ou erros brutos:

```bash
backend/.venv/bin/python scripts/validate-visual-provider.py data/artifacts/<id>/screenshots/<captura>.png
```

Essa chamada é independente do veredito dos CTs: apenas prova que o provedor
devolveu conteúdo estruturado para aquela captura, não cobertura visual total.
Os probes também usam Gemini 2.5 Flash-Lite por padrão; `--auxiliary-model`
seleciona outro modelo explicitamente.

Para medir o gerador auxiliar real em uma ação de compra preenchida já
persistida, sem alterar o CT ou seu relatório:

```bash
backend/.venv/bin/python scripts/validate-auxiliary-review.py <id>
```

O teste reutiliza capturas/asserções originais e reconstrói apenas o texto dos
valores verificados; não equivale à repetição de todo o DOM. Faz no máximo duas
requisições (incluindo eventual compatibilidade de formato), com timeout, e
salva somente métricas seguras em `data/mcp-validation/optimized-auxiliary-review.json`.

## Execução real com custo limitado

```bash
backend/.venv/bin/python scripts/validate-live.py --preflight
backend/.venv/bin/python scripts/validate-live.py --mode visual
```

Para comparar também um perfil público com comportamentos problemáticos do
demo, sem alterar as credenciais salvas:

```bash
backend/.venv/bin/python scripts/validate-live.py --mode visual --demo-profile problem_user
backend/.venv/bin/python scripts/validate-live.py --mode text --demo-profile problem_user
```

O perfil vale somente para a execução criada pelo runner. Não tenta descobrir
contas, não altera `.env` e não transforma um comportamento sem requisito
declarado automaticamente em defeito.

O runner atualiza a chave do provedor OpenRouter com a chave autorizada de
`backend/.env` antes da execução. Usa as credenciais de aplicação de
`backend/.env` em memória, com JEV 1.13 e Gemini 2.5 Flash-Lite solicitados pelo
usuário. Gemini é pago; `--auxiliary-model` permite selecionar explicitamente
outro modelo do catálogo (por exemplo, `qwen/qwen3.8-27b:free`). Não imprime
segredos nem modifica os limites padrão do aplicativo. Seu watchdog cancela
somente a execução criada pelo runner se houver intervenção manual, 20 minutos
de duração ou custo informado acima de US$ 0,10. Custos não informados pelo
provedor não são considerados conhecidos. Métricas ficam em
`data/mcp-validation/metrics-<id>.json` e o relatório permanece no localhost.

## Limites da revisão auxiliar

Por padrão há até 300 requisições auxiliares, 6 respostas de limitação e 600
segundos para concluir a fila após a exploração. Uma resposta 429 transitória
pode reenfileirar o CT até duas vezes; o tempo de espera respeita o provedor.
Chamadas reais a modelos `:free` são espaçadas em 3,1 segundos; cache exato e
caminhos sem requisição não acrescentam essa espera. Quota diária/autenticação rejeitada
não dispara repetição indefinida e não existe troca automática para modelo pago.

CTs e verificações locais são preservados imediatamente. A primeira revisão de
um CT novo ocorre em paralelo; depois que uma revisão visual foi despachada,
revisitas com imagens diferentes são agrupadas até o fim da cobertura. Só a
última evidência de cada CT entra novamente na fila paga. Cache exato continua
disponível em paralelo. Revisões anteriores ficam no histórico, nunca aprovam
uma captura diferente. O teto de 300 não é meta de gasto nem garantia ilimitada:
valores explícitos menores (como 120) continuam sendo respeitados, e retentativas
podem consumir a margem. Há até 3 tentativas por revisão, incluindo a primeira.

Configuração: `AUTOJEV_AUXILIARY_MAX_REQUESTS`,
`AUTOJEV_AUXILIARY_MAX_RATE_LIMITS`, `AUTOJEV_AUXILIARY_TAIL_TIMEOUT_SECONDS`,
`AUTOJEV_AUXILIARY_REQUEST_INTERVAL_SECONDS`.

PASS técnico, PASS com asserção de negócio, falha da automação, falha técnica e
indício visual são apresentados separadamente. O término do processamento não
é uma porcentagem de todos os cenários possíveis de uma aplicação.

No modo Visual a LLM auxiliar recebe texto e capturas, mas responde somente com achados
visuais. Descrição, passos e verificações do CT são montados localmente. Uma
resposta inválida não conta como revisão concluída; captura ausente ou rejeição
explícita de visão não gera uma chamada extra de texto. As origens da
documentação e da revisão ficam separadas na evidência.

O schema enviado ao provedor restringe o formato e os campos obrigatórios.
Limites de comprimento, coordenadas, confiança e até 12 achados são verificados
localmente, para evitar a explosão de estados de decodificação do Gemini sem
relaxar a admissão de evidências. O limite de saída permanece em 3.072 tokens.
