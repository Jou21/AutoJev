# Jornada observada em lista — 14/10/2026

Etapa histórica: uma nova referência enviada pelo usuário substituiu esta lista
pelo [percurso dourado compacto](journey-gold-validation-2026-10-14.md).

O percurso em S foi substituído por lista vertical numerada, mantendo a ordem
da primeira visita. Cada linha mostra página/variação de URL, testes, reprovados,
visitas e acesso aos CTs. Removidos posicionamento serpenteado, conexões SVG,
observadores de tamanho e linguagem de tabuleiro. HTML usa `ol`/`li` nativos.

Resultados, sequência completa de navegação, busca, editor de passos, rascunhos
visuais e confirmação de reexecução foram preservados.

## Conferência

Browser integrado indisponível; habilidade consultada e conferência feita pelo
MCP do Playwright, sem executar testes da aplicação externa ou chamar modelos.

- Execução `43155ed3-f3b7-4dab-b24e-d3d08b33e747`: 14 linhas na mesma ordem,
  151 CTs preservados, nenhuma conexão/desenho em S.
- Desktop 1440 × 1000: linhas de aproximadamente 84–89 px, contagens e botões
  alinhados. Celular 390 × 844: conteúdo adaptado, sem rolagem horizontal.
- Primeira página abre os 29 CTs; busca por CT001 retorna um caso; editor mantém
  os seis registros originais e o card visual de banco arrastável.
- Tema claro, densidade compacta e impressão conferidos; impressão mantém lista.
- Download HTML conferido offline: 14 páginas e 151 CTs, sem requisições HTTP,
  botão de baixar novamente ou conexões em S.
- Relatório original manteve SHA-256
  `1f9f07e3b5f9a5a31804412a0b157fe5af0d6cf50321760eb063e063926b470d`.

Regressão: 378 testes backend relacionados e 105 frontend aprovados; build
concluído; `git diff --check` limpo. Avisos conhecidos de Starlette e vinext.

Capturas em `data/mcp-validation/`: `journey-list-desktop.png`,
`journey-list-mobile.png`, `journey-list-mobile-rows.png` e
`journey-list-light-compact.png`. Exportação: `journey-list-offline.html`.

Para ver a mudança em relatórios existentes, reabra o relatório pela aplicação.
Não é necessário rodar outra exploração; HTMLs já baixados não são alterados.
