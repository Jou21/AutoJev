# Resultado do teste: reexecução e passos extras — 14/10/2026

A tela de resultado agora oferece «Reexecutar teste» no cabeçalho e
«Adicionar aqui» sempre disponível entre as etapas, sem botão de ativação
nem paleta no topo da coluna. A edição direta permite inserir,
arrastar, mover pelo teclado/botões e remover cards de «Consulta em banco de
dados». Eles são rascunhos visuais, não executados: não há conexão, SQL,
credenciais ou alteração dos resultados e das evidências originais.

As alterações diretas ficam guardadas na aba e compartilham o mesmo rascunho
com «Editar passos». O HTML baixado conserva esses cards em um JSON validado
e limitado a 100 por CT, sem duplicar evidências nem exportar o DOM transitório.
O editor modal mantém seu comportamento de salvar explicitamente. O arquivo
offline não é sobrescrito ao editar; alterações adicionais ficam apenas na aba.

O botão de reexecução usa a mesma confirmação já existente na aplicação.
O relatório não inicia ações nem envia rascunhos: abre somente o CT selecionado
na tela de consentimento. Arquivos offline explicam que é necessário abrir o
relatório pela aplicação para reexecutar.

## Validação

Habilidade Browser utilizada para a tentativa de validação integrada; sem
navegador disponível nessa sessão. Validação visual/interativa realizada pelo
MCP do Playwright, sem iniciar reexecuções reais ou chamadas a modelos.

- Execução 43155ed3-f3b7-4dab-b24e-d3d08b33e747, CT147: inserção nativa por
  arrastar/soltar entre etapas 2 e 3; card selecionado e exibido no painel direito.
- Movimento por botão e Alt+seta; rascunho atualizado de gap 2 para 3 e de volta.
  O editor modal reconheceu o mesmo card e sua gravação atualizou a lista direta.
- As cinco etapas originais, suas numerações, evidências e PASS permaneceram
  intactos. Testes automatizados também cobrem isolamento por CT, filtros,
  âncoras, remoção, limite de 100 cards e descarte de campos executáveis importados.
- «Reexecutar teste» abriu a confirmação para CT147; consentimento inicialmente
  desmarcado e botão de execução desabilitado. Nenhuma confirmação foi enviada.
- Desktop 1701 × 1100 sem rolagem externa nem vazamento horizontal. Celular
  390 × 844 sem vazamento horizontal, botões em duas colunas.
- HTML baixado reaberto offline: card preservado, cinco registros originais,
  mesmo resultado, zero requisições HTTP, sem runtime da aplicação e sem botão
  de baixar o relatório novamente. Feedback offline não sugere download ausente.
- 137 testes backend relacionados e 115 frontend aprovados; build concluído.
  `git diff --check` limpo. Relatório original manteve SHA-256
  `1f9f07e3b5f9a5a31804412a0b157fe5af0d6cf50321760eb063e063926b470d`.

Capturas: `data/mcp-validation/results-inline-desktop.png` e
`results-inline-mobile.png`. Arquivo testado: `results-inline-offline.html`.
Reabra o relatório pela aplicação para receber a interface atualizada;
não é necessário executar outra exploração.

## Ajuste: inserção sempre visível

O botão «Adicionar passo» e a paleta do cabeçalho foram removidos. Os espaços
de inserção, movimentação e remoção ficam disponíveis desde a abertura do CT.
No CT001, o Playwright confirmou sete espaços para seis registros originais,
inserção sem ativar edição, arrastar um card existente e remoção. Contagem e
PASS preservados; nenhuma rolagem externa ou vazamento horizontal no desktop.
73 testes JavaScript e 22 testes backend relacionados passaram após o ajuste.
Captura: `data/mcp-validation/results-insertion-always-visible.png`.
