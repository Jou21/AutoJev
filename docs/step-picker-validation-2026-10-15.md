# Seletor de passos extras — 15/10/2026

«Adicionar aqui» agora abre um seletor com três cards: Executar SWS, Executar
SQL e Aguardar print. O seletor informa o CT e o ponto de inserção; cancelar
não modifica o roteiro. As três opções também estão disponíveis no editor modal.

SWS e SQL possuem a preferência editável «Aguardar print» e uma ação bloqueada
(«Escolher SWS» ou «Executar SQL»), sinalizada como em breve. O passo separado
«Aguardar print» reserva uma captura no roteiro. Tudo continua sendo rascunho
visual: não há execução de integrações, consultas, espera real ou nova captura.
Nenhum card extra recebe PASS/FAIL nem altera a evidência histórica.

O rascunho aceita somente os tipos conhecidos e, em SWS/SQL, um booleano literal
`wait_for_print`. Os antigos cards `database` continuam compatíveis. O limite
de 100 cards por CT é compartilhado; parâmetros de execução, URLs, SQL,
credenciais e capturas forjadas são descartados na importação. O editor trabalha
com cópias e aplica alterações somente ao salvar; a edição direta mantém seu
salvamento na aba e a exportação conserva os campos permitidos.

## Validação

Habilidade Browser consultada e tentativa de conexão sem navegador disponível;
validação visual e interativa realizada pelo MCP do Playwright.

- CT001 da execução 43155ed3-f3b7-4dab-b24e-d3d08b33e747: três escolhas exatas,
  abertura sem inserção e criação dos três tipos entre as etapas 2 e 3.
- SWS/SQL: checkbox de print salva `true`; ações de integração desabilitadas.
  Seis registros originais e PASS preservados.
- Editor + seletor aninhados: Esc fecha apenas o seletor e retorna o foco ao
  intervalo; fechar sem salvar descarta a mudança de checkbox, salvar a aplica.
- Desktop 1701 × 1100 e celular 390 × 844: seletor sem vazamento horizontal;
  três cards lado a lado no desktop e empilhados no celular.
- HTML exportado reaberto offline: três cards, preferência de print, seletor e
  bloqueios preservados; zero requisições HTTP, sem runtime da aplicação nem
  botão de baixar o relatório novamente.
- 124 testes frontend (82 do explorador) e 138 testes backend relacionados
  passaram; build concluído e `git diff --check` limpo.
- Relatório original preservado com SHA-256
  `1f9f07e3b5f9a5a31804412a0b157fe5af0d6cf50321760eb063e063926b470d`.

Capturas em `data/mcp-validation/`: `step-picker-desktop.png`,
`step-picker-mobile.png`, `step-sws-config-desktop.png`. Exportação testada:
`step-picker-offline.html`. Nenhuma reexecução ou chamada a modelos foi iniciada.

Para receber a interface atualizada, reabra o relatório pela aplicação. Não é
necessário rodar outra exploração. Arquivos baixados anteriormente não são
reescritos; novos rascunhos feitos offline permanecem somente na aba.
