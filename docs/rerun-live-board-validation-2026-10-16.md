# Acompanhamento visual da reexecução — 16/10/2026

## Tela e acesso

Nova tela `#/exploracoes/{id}/acompanhar`, aberta automaticamente após criar uma reexecução. Em reexecuções existentes, usar **Acompanhar reexecução**. **Visão geral e resultados** mantém a tela anterior.

Os cards reproduzem a ordem de “Passos e evidências”, incluindo os intervalos dos prints planejados. O percurso conectado se adapta a quatro, três ou duas colunas; no celular mantém uma coluna na ordem do roteiro. Cada card tem texto de estado, ícone e cor, além de seleção para detalhes.

Preparação do navegador, consentimento, login e envio de imagens reutilizam os mesmos controles existentes, sem duplicar confirmação nem iniciar ações ao abrir a tela. Há um atalho para alcançar o formulário de print no celular sem alterar o hash da rota. A prévia da imagem permanece selecionada ao atualizar os dados ou selecionar outro card.

## Progresso real

`rerun.step_progress` é uma projeção de leitura da nova execução. O roteiro persistido contém somente estrutura, rótulos seguros e identidades/digests necessários para associação; não copia resultados aprovados, valores esperados/obtidos ou imagens da execução original.

- Ação só fica concluída com sucesso registrado; despacho sozinho não é conclusão.
- Conferência usa seu próprio resultado novo. Reprovadas ficam vermelhas, análises não repetidas ficam identificadas sem check verde.
- Print só fica concluído com evidência confirmada do anexo; contador ou aceitação em andamento não bastam.
- Cancelamento, falha e prazo encerrado não concluem etapas que não tiveram evidência.
- Reexecuções antigas recuperam o roteiro da origem somente para leitura.

A ordem visual planejada não altera horários nem simula execução sequencial. As conferências locais são registradas em lote após a ação e podem estar concluídas antes de um print visualmente posicionado entre elas. A tela explica essa distinção.

## Validação

- `npm test`: build de produção e 157 testes frontend aprovados, incluindo seis do componente e nove da integração da tela.
- TypeScript, ESLint dos arquivos alterados e `git diff --check` aprovados.
- 184 testes backend focados aprovados, incluindo 27 de progresso/auditoria e regressões de serviço/API.
- Uma execução ampla adicional teve 191 de 192 testes aprovados; um teste existente com janela de 40 ms falhou sob carga concorrente de Chromium. O mesmo teste passou isoladamente e na revalidação focada.

A habilidade de navegador foi seguida. O navegador integrado não estava disponível após recuperação; a conferência visual usou Playwright isolado. Não houve início, retomada, cancelamento ou envio de imagens em CTs reais do usuário.

Conferências da interface:

- Execução real histórica `69f20943-9c32-43a9-b321-b5ed7eab00fb`: sete cards, print na terceira posição, sete conclusões confirmadas, sem rolagem horizontal no desktop.
- Execução histórica `5edc3b41-6608-40ed-b2fb-703919d731b9`: seis etapas com evidência preservada e print não concluído após prazo, sem transformar essa etapa em verde.
- Fixture de interface com GETs simulados: preparação com zero cards verdes; pausa com seis concluídos e um aguardando; atualização para sete concluídos remove o formulário de pausa. Zero requisições de escrita.
- Seleção de outro card e atualização da pausa mantiveram a imagem escolhida e um único botão **Anexar print e continuar**.
- Viewport de 390 px: uma coluna, sem overflow horizontal. Atalho do print alcança o formulário sem mudar a rota nem enviar requisição de escrita.

Captura desktop: `data/mcp-validation/rerun-live-board-desktop-2026-10-16.png`.

Backend e frontend locais foram iniciados/atualizados após verificar que não havia execuções ativas. Relatórios e evidências originais permanecem preservados.
