# Envio de print visível e confirmação didática — 17/10/2026

## Experiência

- O painel de envio aparece antes do roteiro, no lugar do atalho que exigia procurar o formulário mais abaixo.
- Prévia compacta, nome do arquivo, mensagem **Print selecionado. Falta confirmar o envio.** e botão **Anexar print e continuar** ficam juntos. Selecionar não equivale a salvar.
- Colar, arrastar ou escolher uma imagem traz o painel para a vista e foca seu título. Erros de validação e de envio também ficam visíveis. Atualizações automáticas não repetem foco ou rolagem.
- O componente de envio conserva a mesma montagem/chave ao alternar entre acompanhamento e visão geral, selecionar cards ou receber novos objetos do polling. Uma pausa realmente nova limpa a seleção anterior.
- Apenas a confirmação explícita envia a imagem; a proteção contra duplicidade, as validações de formato/tamanho e o vínculo com o checkpoint foram mantidos. Nenhuma alteração no executor ou backend.

## Validação

Build de produção, 166 testes frontend, TypeScript, ESLint focado e `git diff --check` aprovados. As regressões cobrem ordem dos controles, uploader único, seleção local sem POST, polling sem rolagem, imagem preservada após erro, confirmação explícita, duplicidade e nova versão de checkpoint.

A habilidade de navegador foi seguida. O navegador integrado permaneceu indisponível após recuperação; a conferência utilizou Playwright isolado com uma reexecução sintética e rotas de API interceptadas. Não houve envio, início, retomada, cancelamento ou reinício em execuções reais do usuário.

Conferências de interface:

- Colar a partir do roteiro trouxe painel, prévia e CTA para a mesma viewport. Nenhuma requisição de escrita na seleção.
- No desktop de 1600 × 1100, painel de aproximadamente 366 px de altura. No celular de 390 × 844, painel de aproximadamente 588 px: prévia, mensagem e CTA visíveis juntos e sem overflow horizontal.
- Viewports de 800, 768, 760 e 390 px sem overflow horizontal.
- Colar, arrastar e escolher arquivo funcionam; arquivo de assinatura inválida exibe alerta e desabilita envio.
- Polling preserva imagem/rolagem; alternar visão geral/acompanhamento mantém a prévia e um único input/CTA.
- Duplo clique na confirmação produziu somente um POST interceptado para a execução sintética. Erro simulado preservou a imagem e permitiu tentar novamente, sem transformar a etapa em concluída.

Capturas de referência:

- `data/mcp-validation/rerun-print-ready-desktop-2026-10-17.png`
- `data/mcp-validation/rerun-print-ready-mobile-2026-10-17.png`
