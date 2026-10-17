# Correção do painel de preparação na coluna lateral — 17/10/2026

## Causa e ajuste

O painel de preparação continuava herdando `.intervention-wide`, cuja grade horizontal possui três colunas. Na lateral de 320 px, rótulo e botão consumiam a largura e deixavam o conteúdo comprimido, com quebras excessivas e sobreposições.

O estilo específico de `.rerun-live-controls .rerun-intervention` agora usa fluxo vertical, filhos com largura limitada e botão estático no final. A comparação de páginas fica empilhada; URLs longas usam reticências visuais, preservando texto completo no DOM e no atributo `title`. Nenhum outro texto é cortado.

A preparação compacta mostra orientação curta e conserva a instrução original em **Ver instruções completas**, fechado por padrão. O painel original da visão geral mantém a apresentação anterior. Consentimento, confirmação, versões da preparação, login e executor não foram alterados.

## Validação

- Build de produção e 174 testes frontend aprovados; TypeScript, ESLint focado e `git diff --check` aprovados.
- Habilidade de navegador seguida. Navegador integrado indisponível após recuperação; conferência em Playwright isolado, com rotas de API e estados simulados. Nenhuma execução real foi iniciada, retomada, cancelada ou modificada, e não houve reinício do backend.
- Em 1280 × 720, preparação pendente, login rejeitado, URL de 549 caracteres, validação em andamento e print ficaram sem overflow horizontal. Botão permaneceu dentro do painel e abaixo da autorização em todos os estados.
- Painel pendente: 320 px de largura e aproximadamente 465 px de altura; título utiliza 290 px, sem quebra palavra por palavra. Painel com login rejeitado: aproximadamente 526 px, ainda dentro da viewport.
- Ao abrir instruções completas, botão e autorização continuam no fluxo abaixo do texto, sem sobreposição. O texto original permanece disponível.
- Conferência adicional em 1366 × 768 e 390 × 844 sem overflow horizontal ou botão fora do painel.
- Regressões verificam CSS vertical restrito à lateral, instrução recolhida, URLs completas preservadas, conteúdo escapado e consentimento não reaproveitado ao mudar a versão da preparação.

Captura: `data/mcp-validation/rerun-preparation-fixed-1280-2026-10-17.png`.
