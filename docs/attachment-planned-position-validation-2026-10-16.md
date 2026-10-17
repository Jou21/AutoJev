# Posição dos prints anexados — 16/10/2026

## Causa e correção

O relatório usava exclusivamente `occurred_at` para apresentar as etapas. A imagem enviada pelo usuário tem um horário real posterior às conferências locais e acabava no fim, apesar de possuir `planned_gap: 2`.

- `scenario_step_order.presentation_scenario_steps` é uma projeção separada e sem mutações. O roteiro visual respeita o intervalo escolhido; `evidence.timeline` continua cronológico.
- API, relatório HTML, editor e preflight de reexecução usam a mesma ordem dos passos.
- Novos anexos incluem âncoras estruturais dos passos vizinhos. Em uma reexecução de reexecução, índices numéricos não são aplicados cegamente a uma lista diferente. Vínculos ausentes ou ambíguos ficam explicitamente sem posição atribuída.
- O anexo é identificado como “Imagem do usuário”, com “Anexado em” e seu horário real. Nenhuma aprovação ou reprovação é acrescentada pelo print.
- O cache de apresentação considera o novo helper. O relatório original e as evidências persistidas não são regravados.

Essa correção organiza a apresentação do roteiro; não afirma que as conferências foram executadas novamente ou que o horário de envio corresponde ao intervalo visual escolhido. As pausas operacionais continuam antes da ação única ou depois do lote de conferências locais, conforme o consentimento existente.

## Validação

- 191 testes: helper, serviço de reexecução, API de reexecução e Chromium local. Incluem todos os intervalos, múltiplos anexos, âncoras, reexecução de reexecução e preservação da ação única.
- 93 testes: relatório, projeção HTML, jornada/editor, cronologia, repositório e painel de preparação.
- 87 testes de API (grupo parcialmente sobreposto ao primeiro).
- 141 testes frontend completos; exportação/reabertura preservam a ordem sem duplicar imagens.
- Build de produção, TypeScript e `git diff --check` aprovados.

O Browser integrado estava indisponível após a recuperação prevista pela habilidade de navegador. A conferência visual foi realizada com Playwright isolado, sem reexecutar CTs reais.

Execução existente `69f20943-9c32-43a9-b321-b5ed7eab00fb`, CT001, aberta pela aplicação:

1. Antes da ação
2. Selecionar opção
3. Imagem anexada pelo usuário
4. Depois da ação
5. Opção selecionada
6. Ordenação dos produtos
7. Erros durante a ação

A posição salva é `gap: 2`; o horário exibido permanece `16/10/2026 18:30:45 UTC`. Uma única imagem canônica, sem imagem quebrada. Captura de validação: `data/mcp-validation/attachment-planned-position-2026-10-16.png`.

Hashes dos registros de cenários e dos arquivos `report.html` da execução e de sua origem `43155ed3-f3b7-4dab-b24e-d3d08b33e747` ficaram idênticos antes/depois. O backend foi atualizado após verificar que não havia execuções ativas. Nenhuma execução foi iniciada, retomada ou cancelada nesta correção.

## Como visualizar

Fechar a aba antiga do relatório e clicar novamente em **Abrir relatório** na aplicação. Não é necessário reexecutar o CT para corrigir a apresentação desse anexo existente. HTML já baixado não se atualiza sozinho; baixar novamente após reabrir.
