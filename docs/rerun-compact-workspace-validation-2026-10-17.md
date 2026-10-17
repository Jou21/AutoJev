# Reexecução compacta: mapa e print na mesma tela — 17/10/2026

## Layout

- Cabeçalho reduzido a identificação, estado, URL e ações essenciais. Navegação compacta e informações de origem em **Sobre esta reexecução**, recolhidas por padrão.
- No desktop a partir de 1100 px, mapa à esquerda e envio de print à direita. O roteiro utiliza cards compactos de 108 px, três ou quatro colunas conforme a largura útil; números, nomes, estados, ícones e selo **SUA VEZ** permanecem legíveis.
- Descrições completas continuam associadas aos cards por `aria-describedby` e disponíveis ao inspecionar uma etapa. O aviso sobre conferências em bloco fica em um detalhe expansível com resumo visível.
- Em larguras menores, o painel se reorganiza em uma coluna e o roteiro em duas ou uma coluna, mantendo legibilidade e sem overflow horizontal. Não se força todo o conteúdo a caber em uma única viewport de celular.
- O uploader mantém sua posição/chave na árvore React ao alternar entre acompanhamento e visão geral. Preview, confirmação explícita, validação e proteção contra duplicidade permanecem intactos. Nenhuma mudança no backend/executor.

## Validação visual

Habilidade de navegador seguida. O navegador integrado ficou indisponível após recuperação; foi utilizado Playwright isolado com dados sintéticos e todas as rotas de API interceptadas. O backend local estava desligado e não foi iniciado ou alterado. Nenhuma execução real foi retomada, cancelada ou modificada.

Para o roteiro de sete cards com um print pendente:

| Viewport | Mapa | Painel de print | Página sem rolagem |
| --- | --- | --- | --- |
| 1280 × 720 | 159–651 px, todos os cards visíveis | 159–602 px | Sim |
| 1366 × 768 | 159–651 px, todos os cards visíveis | 159–602 px | Sim |
| 1600 × 900 | 159–527 px, todos os cards visíveis | 159–602 px | Sim |

Em 1280 × 720, selecionar uma imagem mantém mapa, prévia, status e botão de confirmação dentro da viewport, com `scrollY=0`. O polling e a alternância para visão geral/acompanhamento preservam a imagem. Nenhuma requisição de escrita foi realizada na seleção.

Viewports de 1024, 820 e 390 px também conferidos sem overflow horizontal. O celular de 390 px estabiliza em duas colunas de cards de 158 px, sem cortar nomes ou estados.

Build de produção, testes frontend, TypeScript, ESLint focado e `git diff --check` verificados. Regressões cobrem modo compacto sem alterar o board normal, ordem/estados, acessibilidade, origem recolhida e uploader estável.

Captura principal: `data/mcp-validation/rerun-compact-1280-ready-2026-10-17.png`.
