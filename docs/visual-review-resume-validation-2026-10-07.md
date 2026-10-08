# Retomada de revisões visuais — validação local

Registro da primeira correção (retomada). O padrão de novas execuções e o
agrupamento de revisitas foram ajustados posteriormente; veja
[a validação do agendamento visual](visual-review-scheduling-validation-2026-10-07.md).

## Política implementada

- Até 3 tentativas totais por revisão, incluindo a primeira; a compatibilidade de formato também respeita o limite de 3 requisições HTTP.
- Orçamento local finito configurável por execução/lote, padrão de 120 chamadas. Abrir a tela ou o relatório não inicia chamadas pagas.
- Cache de evidência exatamente idêntica consultado antes de orçamento, autenticação e pausas do provedor. Uma revisão inválida nunca entra como cache válido.
- Retentativas apenas para falhas transitórias/respostas inválidas, com espera limitada. Sem insistência em autenticação, imagens ausentes ou orçamento esgotado.
- Retomada explícita de revisões pendentes usando capturas salvas, sem navegador/JEV ou repetição dos testes. Cancelamento independente da exploração concluída.
- Novas execuções preservam entrada semântica compacta e redigida. Retomadas históricas sem DOM/texto original são identificadas como contexto limitado.

## Execução real

Execução: `a9a12390-9421-4c1a-8f19-7eaaa67d86fe`.
Modelo auxiliar: `google/gemini-2.5-flash-lite` via OpenRouter.
Lote: `7f0b9117-02cc-4217-b01b-f5fa1f558ba7`.

| Medida | Antes | Depois |
| --- | ---: | ---: |
| CTs | 150 | 150 |
| Capturas com revisão visual válida | 92 | 150 |
| Revisões indisponíveis | 58 | 0 |
| Limite local esgotado | 57 | 0 |
| Resposta inválida | 1 | 0 |
| Custo JEV (USD) | 0.00146118 | 0.00146118 |
| Custo LLM (USD) | 0.03638750 | 0.04758390 |
| Custo total (USD) | 0.03784868 | 0.04904508 |

Foram usadas 58 das 120 chamadas autorizadas, sem necessidade de retentativas neste lote real. O custo adicional informado pelo provedor foi **USD 0.01119640**.

As 58 revisões retomadas são históricas e têm contexto textual limitado. As imagens foram analisadas, mas isso **não equivale a uma revisão com o DOM/texto original completo nem a cobertura de todos os cenários possíveis**.

O SHA-256 dos documentos, verificações locais, capturas e entradas anteriores da linha do tempo dos CTs permaneceu igual: `642f798e77fc65e7d8e04ae0f2ed72206cc836d8695b6927b6b5cc9410989131`.
O `finished_at` original e a duração de 341 segundos foram preservados. O relatório HTML original permaneceu intacto (SHA-256 `4d5da8c043441afcb4c8f506e13406c0bb8acf0d11ef19d115d854844bc266f3`); a apresentação atualizada é um artefato separado.

## Verificações

- Suíte completa de backend, excluindo a suíte separada de integração: 892 testes aprovados.
- Após dois ajustes finais de elegibilidade/numeração, regressão direcionada: 58 testes aprovados.
- Frontend: 32 testes, build e lint aprovados.
- Playwright MCP: confirmação de custo, orçamento inválido bloqueado, progresso/cancelamento independentes (API simulada sem cobrança), retomada real, desaparecimento do controle quando não há pendências e relatório atualizado com custos separados.
- Desktop e mobile de 390 px sem overflow; console sem erros.

Trabalho mantido no localhost, sem publicação ou envio ao Git.
