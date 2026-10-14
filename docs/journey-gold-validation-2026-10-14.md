# Jornada dourada compacta — 14/10/2026

A nova referência substitui a lista anterior por cards compactos ligados em S,
com bordas/brilho dourados, curvas com marcadores, numeração e INÍCIO/FIM.
O título destaca “observada”. Contagens, variantes de URL, CTs e histórico não
foram alterados. FIM indica somente o fim das páginas descobertas, não cobertura
completa da aplicação. Falhas reais continuam identificadas em vermelho.

O caminho decorativo é desenhado localmente em canvas, sem imagem de fundo,
requisição externa ou novos arquivos remotos. Os cards são botões nativos dentro
de uma lista ordenada; a ordem no DOM continua cronológica. Em telas menores,
o percurso usa três, duas ou uma coluna. Impressão mantém uma lista legível.

## Verificação

Habilidades Sites consultadas para preservar a estrutura existente; alteração
restrita ao relatório local, sem publicação. Browser integrado indisponível;
validação visual/interativa pelo MCP do Playwright.

- Execução 43155ed3-f3b7-4dab-b24e-d3d08b33e747: 14 cards e 151 CTs preservados.
- Desktop 1701 × 1100: quatro colunas em S, última linha parcial com FIM junto
  ao último card. Tablet 930px: duas colunas sem sobreposição dos rótulos.
- Celular 390 × 844: percurso vertical com INÍCIO/FIM e sem vazamento horizontal.
- Primeira página abre 29 CTs; busca CT001 e editor mantêm os seis passos.
- HTML baixado reaberto offline: 14 cards, 151 CTs, canvas redesenhado em quatro
  colunas, zero requisições HTTP e sem botão de baixar relatório novamente.
- Impressão mantém as 14 páginas na ordem; relatório original mantém SHA-256
  1f9f07e3b5f9a5a31804412a0b157fe5af0d6cf50321760eb063e063926b470d.
- 104 testes backend relacionados e 107 frontend aprovados; build concluído;
  git diff --check limpo. Testes de geometria cobrem 1/5/12/14 cards, todas as
  larguras, posições finitas, limite de densidade de pixels e exportação.

Capturas em data/mcp-validation/: journey-gold-desktop.png,
journey-gold-tablet.png e journey-gold-mobile.png. Exportação testada:
journey-gold-offline.html. Nenhuma reexecução ou chamada a modelos nesta etapa.

Reabra o relatório pela aplicação para receber o layout atualizado. Não é
necessária uma nova exploração; arquivos já baixados continuam preservados.
