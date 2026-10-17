# Contraste da etapa aguardando print — 17/10/2026

## Ajuste

O estado `waiting` agora usa fundo âmbar, borda amarela de 2 px, selo **SUA VEZ**, número preenchido e ícone de pausa. O destaque permanece mesmo ao selecionar outro card. O aviso acima do roteiro e os detalhes da etapa seguem o mesmo padrão.

Concluídos mantêm check e fundo verde mais perceptível; execução usa azul; pendências permanecem neutras. O selo depende do estado atual e desaparece quando o print é concluído.

Nenhuma regra de execução, pausa, confirmação, upload ou projeção de evidências foi alterada. Nesta tentativa, os seis registros concluídos realmente precedem a pausa: duas capturas, uma ação confirmada e três conferências locais. O print continua pendente; a ordem planejada e o aviso sobre conferências em lote permanecem preservados.

## Conferência

- Build de produção e 163 testes frontend aprovados; TypeScript e ESLint focados aprovados. As regressões verificam selo, transição de estado, seleção independente e contraste mínimo de 4,5:1 nas combinações de texto examinadas.
- Habilidade de navegador seguida; integrado indisponível após descoberta e recuperação, então conferência em Playwright isolado.
- Página real da reexecução aguardando print: seis cards verdes e um âmbar, contador 6/7, selo único, borda amarela de 2 px e `aria-current="step"` no print.
- Selecionar um card concluído não remove o destaque da pausa nem duplica o controle de upload.
- Viewport de 390 px: uma coluna de 316 px, selo dentro do card, sem rolagem horizontal. Desktop de 1600 px sem overflow.
- Nenhum erro de console. Nenhuma reexecução iniciada, retomada ou cancelada e nenhuma imagem enviada durante a validação. Sem reinício do backend.

Captura: `data/mcp-validation/rerun-waiting-contrast-desktop-2026-10-17.png`.
