# Revisões visuais — agendamento e orçamento finito

## Causa da repetição das 57 pendências

Execução auditada: `9fe04170-d2f0-4963-ab84-1e87f9fed97a`, Gemini 2.5 Flash-Lite.

- 150 CTs, limite explícito de 120 chamadas HTTP, todas utilizadas.
- Sem 429, retentativas, timeout ou pausa do provedor nessa execução.
- 83 respostas pagas permaneceram atuais; outras 37 analisaram evidências
  posteriormente substituídas e ficaram no histórico.
- Cache: 16 reutilizações, sendo 10 atuais e 6 históricas.
- Resultado: 93 revisões atuais válidas e 57 indisponíveis.
- Houve 80 atualizações de CTs. Por exemplo, CT022 recebeu 4 revisões pagas;
  CT026 e CT043 receberam 3 cada.

A correção anterior permitia retomar pendências, mas não resolvia o orçamento
insuficiente nem as revisitas pagas nas novas execuções. Não era falta de
crédito nem efeito do limite de três tentativas.

## Correção

- Novas execuções: padrão explícito de 300 chamadas HTTP; não é meta de gasto.
  Overrides do usuário/ambiente continuam válidos. Um limite explícito de 120
  jamais vira 300 silenciosamente.
- CTs e suas verificações locais continuam sendo preservados imediatamente.
- A primeira revisão visual de um CT ocorre em paralelo com a exploração.
- Depois de despachar a primeira revisão, revisitas pagas daquele CT são
  agrupadas até o encerramento da cobertura. Só a evidência mais recente volta
  à fila paga; o histórico não substitui a análise da evidência atual.
- Cache de evidência idêntica continua aplicável em paralelo e após o teto.
- Retentativas permanecem limitadas a três tentativas totais por revisão.
- A fila final respeita o mesmo teto e timeout; cancelamento não inicia novas
  chamadas. Mais revisitas que a capacidade da fila são escoadas sem perda.

300 chamadas cobrem 150 primeiras revisões e 150 finais sem retentativas. Não
garantem revisão ilimitada de qualquer aplicação: falhas transitórias, CTs
adicionais ou um limite escolhido menor ainda podem deixar pendências explícitas.
Revisar todas as capturas registradas não significa testar todos os cenários
possíveis, usuários, entradas ou viewports da aplicação.

## Verificações automatizadas

- Backend: 900 testes aprovados na suíte geral (integração isolada separada).
- Após o ajuste final de prioridade: 90 regressões direcionadas aprovadas.
- Integração isolada com Chromium real: oito casos aprovados, incluindo modos
  texto/visual em loja saudável e com defeitos injetados, cancelamento e espera
  de API. Com 16 regressões de orçamento na mesma invocação: 24 testes aprovados
  em 515,89 segundos, sem modelos pagos nem alterações no histórico do usuário.
- Regressões adicionais: 150 CTs com três revisitas cada, limite explícito de
  120, 270 revisitas acima da capacidade da fila, cache, cancelamento, primeiro
  CT novo em paralelo e remoção/recriação de CT.
- Frontend: 33 testes, build e lint aprovados.
- Playwright MCP: formulário com padrão 300, seleção Visual + Gemini,
  confirmação de custo da retomada, andamento independente de revisões e
  ausência de overflow/erros de console.

## Validação real

Nova execução completa: `e1044fa9-9c62-49db-97cd-b7448812302a`.
Recuperação das 57 pendências: lote `c5ad890e-0460-408b-9d21-61c1f3cc8c9e`,
sem repetir o navegador/JEV, com teto autorizado de 120 chamadas.

| Medida | Execução antiga original | Antiga após retomada | Nova automática |
| --- | ---: | ---: | ---: |
| CTs | 150 | 150 | 150 |
| Revisões visuais válidas atuais | 93 | 150 | 150 |
| Revisões indisponíveis | 57 | 0 | 0 |
| Requisições HTTP auxiliares | 120 | 177 | 175 |
| Custo JEV (USD) | 0.001437702 | 0.001437702 | 0.001438584 |
| Custo LLM (USD) | 0.037611000 | 0.058587150 | 0.057230590 |
| Custo total (USD) | 0.039048702 | 0.060024852 | 0.058669174 |

### Retomada das 57 pendências

Concluída com 57 chamadas adicionais e 57 respostas válidas na primeira
tentativa; nenhum contexto limitado. Custo adicional: **USD 0.02097615**.
JEV, navegador e testes locais não foram repetidos. Documentos, status e
capturas mantiveram o SHA-256
`68643bf65fb65de7c25f09711a22f68c3714af57bac8f33950054b24a83dfdca`.
O `finished_at` original permaneceu `2026-10-08T02:06:28.374157+00:00`.

### Nova execução

- 316 ações, 150 CTs; 119 PASS de negócio e 31 PASS somente de interação.
- 150/150 revisões visuais válidas, nenhuma pendência/contexto limitado.
- Cobertura observada: 55 estados, zero fronteiras pendentes e zero restrições.
- 175/300 chamadas: 149 revisões atuais pagas, 25 históricas pagas e uma
  resposta inválida inicial. Cache: uma revisão atual e duas históricas.
- CT001 recuperou a resposta inválida na segunda tentativa.
- 43 eventos de agrupamento; isso **não** significa 43 HTTP economizados.
  Na comparação de resultados completos, a redução real foi 177 → 175 HTTP.
- Primeira revisão concluída em aproximadamente 11 segundos. Ao encerrar a
  cobertura, 125/150 revisões atuais estavam prontas; as 25 restantes foram
  concluídas automaticamente na fila final, sem lote manual.
- Duração total: 7m44s, incluindo revisões e relatório. A retomada da execução
  anterior ocorreu em paralelo durante parte desta validação; os tempos não
  são um benchmark isolado de desempenho.

Playwright MCP confirmou os resultados na tela e no relatório, ausência do
botão de retomada quando não há pendências, custos separados, console sem
erros e ausência de overflow em 1440 e 390 px.

Capturas de validação:
`data/mcp-validation/visual-budget-300-completed-desktop.png` e
`data/mcp-validation/visual-budget-300-completed-mobile.png`.
Métricas detalhadas: `data/mcp-validation/metrics-e1044fa9-9c62-49db-97cd-b7448812302a.json`.

Custo total informado das duas validações deste ajuste: **USD 0.079645324**.
Trabalho mantido exclusivamente no localhost, sem publicação ou envio ao Git.
