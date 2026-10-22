# Plano de evolução — uma exploração, uma base de testes contínua

Data: 21/10/2026. Status: implementação local e validação técnica realizadas. Aprovação de usabilidade pelo usuário ainda pendente; consulte a seção 15.

## 1. Direção do produto

O usuário explora uma parte relevante ou toda a aplicação para criar sua base inicial. Depois, continua trabalhando nessa mesma base: ajusta testes, adiciona novos, arquiva os que não fazem mais sentido e executa novamente apenas o necessário.

**Manter a base e executar testes são atividades diferentes.** Adicionar um teste não deve obrigar o usuário a preparar uma execução. Selecionar testes não deve disparar ações na aplicação.

Jornada principal:

Explorar → manter a base → escolher testes → preparar e revisar → executar → consultar o histórico → continuar mantendo a mesma base.

Não unir bases apenas porque possuem a mesma URL: ambientes, configurações e explorações independentes podem compartilhar um domínio. O vínculo deve continuar baseado na origem persistida.

## 2. Escopo desta evolução

- Menus **Meus testes** e **Rascunhos**, incluindo catálogo, criação/edição, preparação e histórico.
- Interface simples para pessoas sem experiência com ferramentas de QA.
- Aproveitamento da arquitetura, das rotas e dos dados existentes.
- Preservação de IDs, versões, resultados, prints e referências às execuções.
- Relatórios HTML/blob: manter os acessos e seus escopos corretos; redesenhar seu conteúdo fica fora desta etapa.

Não estão incluídos: agendamento por horário, integrações SWS/SQL, compartilhamento implícito de sessão entre testes, transformação de texto livre em automação ou execução automática a partir de uma mensagem da IA.

## 3. Fundação que já existe

Conferida nos componentes e serviços atuais, além do registro de validação anterior em [plano-simplificacao-ux.md](plano-simplificacao-ux.md):

- [x] Menus Meus testes e Rascunhos e navegação pelas rotas existentes.
- [x] Bases vinculadas à exploração inicial, com histórico de execuções antigas e novas.
- [x] Seleção de vários testes, ordenação, pausas para imagens e revisão com consentimento.
- [x] Criação e versionamento de testes manuais; sugestões por IA e gravação revisável.
- [x] Chat com escolha de modelo e comandos locais para inclusão explícita de IDs.
- [x] Arquivamento/restauração persistentes, separados dos resultados históricos.
- [x] Bloqueio de testes arquivados em novos planos e na validação de rascunhos anteriores.
- [x] Painéis ajustados à janela, com rolagem independente e alternância roteiro/chat em áreas estreitas.

Os itens abaixo são evoluções propostas sobre essa fundação. Os resultados de testes registrados no documento anterior não são uma nova execução de QA nesta etapa de planejamento.

## 4. Conceitos que a interface deve deixar claros

| Conceito | O que significa para o usuário |
| --- | --- |
| Base de testes | Os testes que ele mantém para aquela origem da aplicação. |
| Teste | Um caso com objetivo, passos e resultado esperado; seu ID continua sendo CT001, CT002 etc. |
| Rascunho de execução | Uma seleção salva de testes, sua ordem e ajustes; ainda não é uma execução. |
| Execução | Uma tentativa real, com data, escopo e resultados próprios. |
| Resultado na base | O resultado registrado para aquela versão, com indicação de quando e onde foi obtido. |
| Arquivado | Fora da base ativa, mas recuperável e com histórico preservado. |

Evitar usar “rodada”, “conjunto”, “receita” ou IDs internos como explicação principal. Manter termos técnicos necessários em detalhes opcionais, sem alterar as identidades internas ou os links antigos.

## 5. Entrega 1 — tornar a base o ponto de partida

### Meus testes

- [x] Apresentar cada origem como uma base que pode continuar sendo mantida, não como um resultado descartável.
- [x] Manter aplicação, data de criação, quantidade de testes ativos e última atividade suficientes para distinguir bases do mesmo domínio.
- [x] Diferenciar criação de uma exploração independente de continuidade de uma base existente; não criar nem mesclar bases sem escolha explícita.
- [x] Ao abrir uma base, entrar diretamente na lista de testes, preservando busca e contexto ao voltar de seus detalhes.

### Dentro da base

- [x] Consolidar a linguagem em três áreas: **Testes**, **Preparar execução** e **Execuções**.
- [x] Destacar **Adicionar teste** como ação de manutenção; usar **Preparar execução (N)** para os marcados.
- [x] Tornar “retirar desta execução” diferente de “arquivar na base”, inclusive nos títulos e confirmações.
- [x] Revisar estados vazios: sem testes, sem resultados, sem correspondência nos filtros e base com todos os testes arquivados.

**Aceite:** alguém que nunca utilizou o app consegue apontar onde adicionar um teste e onde preparar uma execução, sem consultar documentação nem interpretar a palavra “CT”.

## 6. Entrega 2 — adicionar e ajustar sem obrigar uma execução

Hoje, a criação manual está dentro do preparador, e salvar também seleciona o teste para aquela execução. Separar os destinos:

- [x] Ao criar pelo catálogo, abrir um editor de manutenção com ação principal **Salvar na base**.
- [x] Oferecer **Salvar e incluir na execução** como escolha explícita, sem selecionar testes adicionais silenciosamente.
- [x] Ao criar pelo preparador, manter o destino **Salvar e adicionar**, indicando que o teste também fará parte da base.
- [x] Manter inicialmente título, passos e resultado esperado; endereço, descrição e pré-condições permanecem em opções adicionais.
- [x] Ao editar um teste manual, mostrar que será criada uma nova versão, sem reescrever resultados anteriores.
- [x] Não atribuir a uma definição alterada a aprovação de uma versão antiga. Mostrar **Esta versão ainda não foi executada** quando aplicável.
- [x] Para automáticos, explicar as limitações do executor: editar texto não modifica a automação. Oferecer a duplicação como manual ou a criação de outro roteiro revisável.

Não incluir um editor livre de automação nesta entrega. Uma futura edição de passos automáticos depende de validação específica das ações, pré-condições e conferências suportadas.

### Arquivamento

- [x] Refinar a apresentação já implementada: **Gerenciar → Arquivar**, com prévia e confirmação; filtro **Arquivados → Restaurar**.
- [x] Indicar quantidade ativa e arquivada sem fazer o usuário pensar que os resultados foram apagados.
- [x] Explicar claramente o bloqueio de um rascunho que contenha testes arquivados, oferecendo remover da seleção ou voltar à base para restaurar.
- [x] Preservar o impedimento de arquivar durante uma execução da base e a detecção de alterações feitas em outra aba.

**Aceite:** adicionar ou editar na base não cria uma execução; arquivar e restaurar não apagam prints nem alteram resultados ou planos históricos.

## 7. Entrega 3 — uma preparação curta e previsível

- [x] Orientar em três etapas curtas: **Escolher testes → Ajustar roteiro → Revisar e confirmar**.
- [x] Seleção em lote e seleção individual devem mostrar exatamente o que será incluído, inclusive marcados fora dos filtros.
- [x] Manter contagem, nomes/IDs e próxima ação visíveis sem depender de rolar até o fim da lista.
- [x] Manter a ação de remover um teste limitada ao rascunho; ela não o arquiva na base.
- [x] Explicar o nome, o estado salvo/não salvo e o destino **Rascunhos** com o mesmo vocabulário em toda a tela.
- [x] Proteger a saída com alterações não salvas, incluindo texto de novo teste, oferecendo salvar quando possível, continuar editando ou descartar explicitamente.
- [x] Tratar conflitos de versão sem sobrescrever alterações de outra aba nem descartar o texto que o usuário estava editando.
- [x] Na revisão, destacar quantidade, testes incluídos, impedimentos e efeitos das ações. Detalhes técnicos ficam recolhidos, mas avisos necessários ao consentimento continuam visíveis.
- [x] Manter a separação entre confirmar a execução e preparar/continuar cada teste automático no executor atual.

**Aceite:** ao marcar 2 de 151 testes, o próximo passo indica 2, o rascunho contém somente os 2 e a revisão não permite iniciar sem autorização explícita.

## 8. Entrega 4 — chat como ajuda contextual, não como outra aplicação

- [x] Sugerir pedidos ligados à manutenção da base: escolher testes existentes, propor novos casos ou explicar um resultado.
- [x] Mostrar uma prévia das sugestões com ações explícitas: **Adicionar à base** ou **Incluir na execução**, conforme o contexto.
- [x] Respostas só podem dizer que algo foi adicionado depois que a alteração correspondente realmente aconteceu.
- [x] Preservar comandos diretos com IDs sem uso de modelo; desconhecidos, arquivados ou ambíguos devem gerar orientação, não inclusões erradas.
- [x] Manter o modelo escolhido junto ao campo de mensagem e deixar claro quando há consumo de tokens. Trocar o modelo não envia mensagens nem modifica configurações globais.
- [x] Distinguir **Pedir sugestões à IA** de **Deixar a IA navegar**. Navegação real e gravação exigem seus próprios avisos e confirmação.
- [x] Preservar mensagem não enviada, conversa e escolha de modelo ao trocar de painel durante a edição.
- [x] Não persistir conversas ou rascunhos de campos em armazenamento local com credenciais ou dados sensíveis como solução automática de recuperação.

**Aceite:** um pedido de sugestão não executa ações; um caso proposto não vira teste aprovado; o usuário entende a diferença entre planejar com IA e autorizar navegação real.

## 9. Entrega 5 — rascunhos e histórico sem perda de contexto

### Rascunhos

- [x] Retomar pelo mesmo nome, base, IDs, ordem e referências salvas; avisar quando alguma referência se tornar indisponível.
- [x] Exibir data de edição e **Continuar**, mantendo a ordenação pela última alteração.
- [x] Orientar o estado vazio com um caminho direto para escolher a base e os testes.
- [x] Não apresentar rascunhos como execuções agendadas. Datas são de edição, não horários de disparo.

### Execuções e resultados

- [x] Manter tentativas antigas e novas juntas, da mais recente à mais antiga, com data e escopo legíveis.
- [x] Diferenciar **Ver execução** de **Acompanhar**, conforme o trabalho esteja encerrado ou em andamento.
- [x] Preservar a lista e seus filtros ao voltar de um teste, evitando recomeçar a navegação.
- [x] Expor detalhes extensos em **O que aconteceu** e comparação somente quando existir alternativa válida.
- [x] Diferenciar a primeira execução, uma execução selecionada e o resultado mais recente de cada teste da base.
- [ ] Garantir feedback imediato ao abrir um resultado e medir o carregamento antes de otimizar; nunca trocar a referência correta por uma imagem antiga só para parecer rápido.

**Aceite:** uma execução parcial não parece uma validação da aplicação inteira. Abrir um resultado leva ao teste, tentativa e evidências corretos.

## 10. Layout e orientação para iniciantes

Aplicar em todas as entregas:

- [x] Cabeçalhos curtos; busca e ação principal acessíveis enquanto a lista rola.
- [x] Nada de blocos grandes repetindo o que já aparece no título ou na lista.
- [x] Priorizar informação útil, não reduzir indiscriminadamente fonte e alvos de clique para caber mais.
- [x] Em telas amplas, lista, roteiro e chat podem coexistir; quando não couberem, alternar painéis sem perder conteúdo.
- [x] Campo de mensagem e ações de salvar/revisar fora das áreas roláveis dos respectivos conteúdos.
- [x] Ajuda opcional **Como funciona**, com três passos, que pode ser fechada; não bloquear todo acesso com um tutorial obrigatório.
- [x] Mensagens de sucesso descrevem o que mudou; erros explicam uma próxima ação concreta.
- [x] Navegação por teclado, foco visível, rótulos completos e estados que não dependem somente de cor.

### Metas propostas de avaliação

- 1366 × 768: buscar comportar pelo menos 8 linhas de testes com seleção ativa, sem esconder o próximo passo nem sacrificar legibilidade.
- 1024 × 700: filtros, pelo menos 5 linhas e ação de continuar disponíveis; chat utilizável em painel próprio.
- 1920 × 1080: aproveitar a largura para edição e conversa simultâneas, sem fazer o chat ultrapassar a altura da janela.
- 390 × 844 e ampliação de texto: permitir refluência e acesso a todos os controles sem rolagem horizontal da página.
- Um iniciante deve conseguir encontrar um teste, selecionar dois casos e chegar à revisão sem orientação verbal do desenvolvedor.

As metas são critérios para a próxima avaliação, não medições já obtidas. Se houver conflito, legibilidade e segurança prevalecem sobre quantidade de linhas.

## 11. Sequência de implementação

1. **Base e linguagem:** ajustar entrada, ações e estados vazios; mostrar a primeira tela para avaliação do usuário.
2. **Manutenção independente:** separar salvar na base de incluir em uma execução; validar versões e arquivamento.
3. **Preparação e rascunhos:** revisar próximos passos, persistência explícita, proteção contra perda de edição e conflitos.
4. **Assistente contextual:** alinhar sugestões e destinos com base versus execução, preservando os limites atuais.
5. **Histórico e retorno:** manter contexto ao navegar e esclarecer tentativas, datas e escopos.
6. **QA de ponta a ponta:** percorrer os dois menus com Playwright nas quatro larguras, revisar capturas e recolher feedback.

Cada incremento deve terminar com uma captura da tela principal e uma pergunta específica ao usuário sobre clareza, não apenas sobre aparência. Não avançar com uma interpretação de produto rejeitada pelo usuário.

## 12. Arquivos e cuidados técnicos

Pontos principais para a implementação:

- `app/AutoJevApp.tsx`: menus, rotas e retorno entre telas.
- `app/components/TestSuitesWorkspace.tsx` e `.css`: base, preparação, editor e histórico. Separar componentes por responsabilidade se isso ajudar a implementar com segurança, preservando estado e contratos.
- `app/components/PlannedExecutions.tsx` e `.css`: listagem e retomada de rascunhos.
- `app/components/PlanningChat.tsx`, `.css` e `suite-planning-chat.ts`: contexto, sugestões, comandos locais e compositor.
- `backend/autojev/services/test_suites.py`, `api/test_suites.py` e `adapters/execution_plans.py`: identidade, versões, planos e estado do catálogo; reutilizar os contratos existentes antes de propor novos.
- Testes de componentes, rotas, planos, assistente e arquivamento: ampliar regressões nas entregas que alterarem comportamento.

Não reescrever o app, trocar o framework ou introduzir uma dependência grande só para redesenhar essas telas. Preservar as alterações existentes no repositório. Alterações de esquema, se necessárias, devem ser aditivas e preservar os dados históricos.

## 13. Verificação e conclusão

Checklist obrigatório para encerrar a implementação deste plano:

- [x] Adicionar teste somente à base, sem criar execução.
- [x] Adicionar outro teste à base e à seleção explicitamente.
- [x] Editar um manual sem alterar evidências da versão anterior nem herdar aprovação.
- [x] Selecionar 2 de 151; filtrar a lista; chegar à revisão ainda com os mesmos 2.
- [x] Arquivar e restaurar, mantendo acesso às execuções e prints anteriores.
- [x] Retomar rascunho com um arquivado e resolver a indisponibilidade sem execução acidental.
- [x] Trocar roteiro/chat e retornar de detalhes sem perder o estado esperado.
- [x] Sair com edição não salva e receber opções claras de preservação ou descarte.
- [x] Verificar mensagens para falha de rede, conflito entre abas, base vazia e filtros sem resultados.
- [x] Validar por Playwright em 1920 × 1080, 1366 × 768, 1024 × 700 e 390 × 844, além de teclado e ampliação de texto.
- [x] Rodar tipos, lint, build e testes proporcionais ao risco, sob limites de tempo/memória.
- [x] Registrar quais telas foram aprovadas pelo usuário e quais ajustes restam.

Usar fixtures e respostas simuladas nas operações de escrita do QA. Não consumir modelos pagos, iniciar explorações, gravar navegação ou executar testes na aplicação real apenas para validar o layout. Não ler ou expor chaves do `.env` sem necessidade concreta. Reiniciar o backend somente se necessário, com a autorização existente e após confirmar ociosidade.

## 14. Exemplo de ponta a ponta

Uma exploração inicial encontrou 151 testes. O usuário arquiva 1 que deixou de fazer sentido e adiciona 3 novos à mesma base: passam a existir **153 ativos e 1 arquivado**.

Ele seleciona 2 testes para conferir uma correção. O rascunho e a revisão mostram apenas esses 2. Depois da execução, somente eles têm novos resultados; os outros 151 ativos não foram executados nessa tentativa. Os casos novos ainda não executados continuam sem resultado, e resultados antigos mantêm suas datas e versões.

No dia seguinte, o usuário volta à mesma base para continuar a manutenção. Não precisa explorar tudo novamente nem localizar reexecuções soltas em outra listagem.

## 15. Implementação e conferência — 21/10/2026

### Entregue no app local

- **Meus testes** apresenta bases duradouras, com criação, última atividade e quantidade ativa. **Nova base de testes** diferencia continuar uma base de explorar e criar outra; não agrupa por domínio.
- **Testes → Adicionar teste** abre manutenção independente. A ação principal é **Salvar na base**; **Salvar e incluir na execução** é uma escolha separada. No preparador, **Salvar e adicionar** mantém o destino esperado. Nenhuma dessas ações executa testes.
- Editor com título, passos e resultado esperado; detalhes adicionais recolhidos. Edição manual cria versão nova, sem herdar aprovação nem apagar evidências anteriores. A resposta de salvamento é reconciliada antes de qualquer atualização de listagem, evitando repetir a criação se uma leitura posterior falhar.
- Estado de arquivamento continua independente da versão da definição. A revisão do catálogo é preservada ao editar um teste já restaurado, evitando um falso conflito no próximo arquivamento.
- **Escolher testes → Ajustar roteiro → Revisar e confirmar** orienta a preparação. A seleção fora dos filtros continua explícita. Revisão mostra IDs e títulos; iniciar exige consentimento. Um novo caso não salvo ou uma gravação em andamento precisa ser resolvido antes da revisão.
- Saída com edição não salva oferece continuar editando, salvar sem executar ou descartar explicitamente. Funciona também para navegação pelos menus e mudança de hash; recarregar/fechar usa o aviso nativo. O diálogo prende o foco, aceita Escape e devolve o foco ao fechar. Gravação ativa precisa ser finalizada antes de sair.
- Conflitos entre abas e falhas preservam o texto. A interface não atualiza a revisão às cegas; na edição manual é possível escolher salvar o texto como outro teste. Falha ao abrir a base oferece **Tentar novamente** e retorno à lista.
- Chat contextual para base ou execução, com modelo junto ao compositor. Sugestões continuam revisáveis e só há confirmação de inclusão depois da alteração real. Conversa, modelo e mensagem não enviada permanecem ao alternar painéis dentro da tela; não foram persistidos campos ou conversas em armazenamento do navegador.
- Rascunhos retomam nome, IDs, ordem e referências. A abertura aguarda os dados do rascunho para não trocar a aba depois de um clique rápido do usuário. Respostas de uma navegação abandonada não redirecionam o usuário de volta.
- Arquivados e referências indisponíveis bloqueiam a revisão e podem ser retirados da execução. Estados vazios distinguem base vazia, todos arquivados e filtros sem correspondência.
- Histórico preserva tentativas antigas e novas, datas e escopo. Busca, filtro de funcionalidade e posição do catálogo são recuperados em memória, por origem, ao retornar. Textos extensos continuam em **O que aconteceu**; nenhuma evidência foi substituída por uma imagem diferente.

### Playwright e limites da validação

Janela independente, sem controlar as abas do usuário. Navegação pelos dois menus, catálogo, criação, edição, arquivamento/restauração, preparação, revisão, rascunhos e histórico. Operações de escrita foram respondidas por fixtures no navegador; os demais POSTs reais ficaram bloqueados.

| Janela | Catálogo com seleção ativa | Editor/chat |
| --- | --- | --- |
| 1920 × 1080 | 13 linhas completas na medição | Painéis simultâneos; compositor dentro da janela. |
| 1366 × 768 | 8 linhas completas, primeira em y=233 | Editor e chat lado a lado, sem esconder o campo de mensagem. |
| 1024 × 700 | 6 linhas completas | Alternância de painéis, preservando conteúdo e ações. |
| 390 × 844 | 3 linhas completas na lista com títulos longos | Editor/assistente em painéis próprios; sem transbordamento horizontal. |

Também conferidos: ampliação dos textos a 200% em 390 px sem rolagem horizontal da página; filtros dentro da largura e lista ainda utilizável; Tab/Shift+Tab e Escape no diálogo de alterações não salvas. Listas longas continuam roláveis dentro de suas áreas; não há promessa de eliminar toda rolagem.

Cenários de escrita simulados: salvar somente na base sem marcar o teste, salvar e incluir explicitamente, nova versão manual sem aprovação herdada, conflito HTTP 409 com texto preservado, arquivar/restaurar, selecionar dois fora dos filtros, revisar exatamente esses dois sem iniciar e retomar o mesmo rascunho. Erros de rede, base vazia e todos arquivados também foram simulados.

Não foram iniciadas execuções, explorações ou gravações reais, nem chamadas pagas de IA. Nenhuma chave do `.env` foi lida. Não houve necessidade de reiniciar o backend, migrar dados, instalar dependências ou publicar o app.

### Relatórios e ponto de avaliação pendente

O conteúdo dos relatórios HTML/blob não foi alterado nesta entrega. Os testes existentes continuam verificando abertura imediata da página local de carregamento, escopo/ID corretos e ausência de substituição silenciosa por outro teste. O item de benchmark real dos relatórios permanece desmarcado: não foram gerados nem baixados relatórios grandes para medir tempos nesta etapa, restrita às telas anteriores a eles.

Foram mostradas capturas intermediárias e feita uma pergunta específica sobre **Salvar na base** versus **Salvar e incluir na execução**. Ainda não houve resposta: nenhuma tela desta entrega foi registrada como aprovada pelo usuário. O aceite com uma pessoa iniciante, sem ajuda verbal, também permanece como avaliação humana, não como resultado de teste automatizado.

### Verificações automatizadas e estado real

- **301 testes do frontend aprovados**, incluindo destinos de salvamento, seleção exata, revisão/consentimento, perda de edição, conflitos, estado vazio e recuperação de rede.
- **36 testes focados do backend aprovados**, em fixtures isoladas: planos, versões, assistente e arquivamento/restauração com preservação histórica.
- TypeScript, ESLint dos arquivos desta entrega, build de produção e `git diff --check` aprovados, com limites de memória e tempo.
- Avisos preexistentes: classificação estática de rotas do Vinext e depreciação de AnyIO/TestClient. No navegador, falhas de recurso esperadas decorreram do bloqueio de POSTs reais e dos erros HTTP 409/503 deliberadamente simulados; não foram observados erros de execução do React.
- Conferência final diretamente na API real: **151 testes, 0 arquivados, 0 rascunhos de demonstração e 0 execuções ativas**. Mocks removidos ao terminar a navegação. Não houve alteração dos dados reais por este QA.
- Captura final do catálogo real com dois marcados apenas na interface: `base-catalog-final-1366.png`, com 8 linhas completas. Estado vazio real de rascunhos: `base-drafts-empty-final-1366.png`.

### Arquivos de captura

Em `data/mcp-validation/`, ignorado pelo Git. Versões demonstrativas de rascunhos/novos casos usam fixtures, não dados persistidos:

- `base-home-{1920,1366,1024,390}.png`
- `base-catalog-{1920,1366,1024,390}.png`
- `base-editor-{1920,1366,1024,390}.png`
- `base-planning-{1920,1366,1024,390}.png`
- `base-drafts-{1920,1366,1024,390}.png`
- `base-history-{1920,1366,1024,390}.png`
- `base-review-1366.png` e `base-unsaved-dialog-1366.png`
