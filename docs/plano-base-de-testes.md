# Plano de evolução — uma exploração, uma base de testes contínua

Data: 21/10/2026. Atualizado em 22/10/2026. Status: implementação local e validação técnica realizadas. Separação dos destinos de salvamento aprovada pelo usuário; demais avaliações de usabilidade continuam pendentes.

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

Foram mostradas capturas intermediárias e feita uma pergunta específica sobre **Salvar na base** versus **Salvar e incluir na execução**. Confirmação registrada em 22/10/2026: o usuário respondeu **“Sim, ficou clara”**. Essa aprovação refere-se à separação dos destinos, não ao aceite de todas as telas. O teste de usabilidade com uma pessoa iniciante, sem ajuda verbal, continua pendente e não é substituído por QA automatizado.

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

## 16. Continuação e revisão após feedback — 22/10/2026

### Feedback registrado

A separação **Salvar na base / Salvar e incluir na execução** continua aprovada. A primeira versão da revisão separada **não foi aprovada**: o usuário considerou o cabeçalho grande e rejeitou os dois botões no rodapé. Pediu mais testes visíveis e planejamento por objetivo no chat, como **“Meu teste consiste em testar o CRUD da tela X”**. A versão descrita abaixo responde a esse feedback; sua aprovação visual continua pendente.

### Revisão focada na lista

- Na confirmação, o cabeçalho da base, as abas, o campo de nome, as ferramentas de edição e o chat dão lugar a uma única barra: **Ajustar · Revisar execução · quantidade · Executar N testes**.
- Retirados os dois botões grandes do rodapé nessa etapa. A ação de executar fica no topo; o aceite obrigatório permanece visível junto à lista. Sem aceite, disponibilidade e revisão válida, o botão continua bloqueado.
- Lista ocupa a largura disponível, com ID, título e disponibilidade. Somente a lista rola; nome completo disponível no título e títulos quebráveis em telas estreitas. Avisos adicionais ficam em **Cuidados da execução**, sem retirar a informação explícita sobre modificação de dados e preparação independente do aceite.
- **Ajustar** conserva IDs, ordem, referências e pausas para print. Retornar à revisão exige novo aceite; não duplica o salvamento nem inicia testes.

| Janela | Altura da barra | Testes inteiros visíveis na revisão de 151 itens |
| --- | --- | --- |
| 1920 × 1080 | 48 px | 22 |
| 1366 × 768 | 48 px | 15 |
| 1024 × 700 | 48 px | 13 |
| 390 × 844 | aproximadamente 64 px | 13 |

Medições via Playwright com respostas simuladas de salvamento e validação. Não significam que os 151 casos estejam disponíveis no executor real. Nenhum deles foi executado. Não houve transbordamento horizontal; o último item pôde ser alcançado pela rolagem interna. Em 390 px com textos ampliados a 200%, aceite e ação continuaram dentro da janela, sem transbordamento horizontal.

### Assistente por objetivo e análise de CRUD

- Convite editável no chat: **“Quero testar o CRUD da tela [nome]. O que já temos e o que falta?”**. Clicar apenas preenche a mensagem; não chama o modelo automaticamente.
- A consulta compara a solicitação com testes ativos da mesma base: IDs, títulos, descrições curtas, URLs sem query/fragmento, modo e resultado esperado. Não envia imagens, receitas do executor, credenciais ou registros completos de navegação.
- Catálogo limitado a 500 itens, com quantidade ativa, quantidade incluída e indicação explícita de completude. Arquivados ficam de fora. Um recorte incompleto não autoriza afirmar que um objetivo não existe.
- Resposta estruturada por **Criar / Consultar / Editar / Excluir**: **Já existe**, **Não encontrado**, **Precisa conferir** ou **Não se aplica**, com justificativa e referências. Teste existente não significa CRUD completo nem aprovação da aplicação inteira.
- O modelo é orientado a pedir esclarecimento se a tela não estiver identificada, reutilizar os testes existentes e propor casos apenas para objetivos faltantes, sem assumir que toda tela permite CRUD. Sem observar a aplicação, não pode afirmar que descobriu campos, validou resultados ou constatou ausência de funcionalidade.
- A API rejeita IDs inventados/arquivados, operações repetidas, referências inexistentes a propostas e classificações inconsistentes. Propostas continuam manuais e revisáveis; nem a análise nem a escolha de um modelo adicionam ou executam testes sozinhas.
- Ações explícitas permitem incluir um existente ou abrir uma proposta no editor. Após salvar uma proposta, a comparação anterior é retirada para não apresentar índices obsoletos como se ainda descrevessem o catálogo atualizado.

### Outros ajustes desta continuação

- Opções de criação compactadas no preparador quando o formulário está aberto: aproximadamente **355 px → 36 px** em 1366 × 768. Formulário essencial ocupa cerca de 408 px; textos longos e opções adicionais continuam acessíveis por rolagem.
- Após salvar, **Ver rascunhos** deixa explícito onde retomar. Falha de salvamento não mostra confirmação falsa nem elimina seleção/texto. Retomada conserva a seleção exata e as pausas para imagens.
- Mantidos os destinos de salvamento aprovados, o arquivamento recuperável e a proteção de alterações não salvas.

### Conferências e limites

- **309 testes do frontend aprovados**, incluindo revisão de 50 itens sem cabeçalhos duplicados, consentimento, retorno ao roteiro e aceite explícito das propostas de CRUD.
- **36 testes focados do backend aprovados** nos módulos de assistente e conjuntos: resposta estruturada, referências inválidas, catálogo parcial, URLs privadas e ausência de salvamento/execução implícitos. Tudo em fixtures isoladas, sem chamadas externas de IA.
- TypeScript, ESLint, Ruff dos arquivos envolvidos, build de produção e `git diff --check` conferidos. Os avisos conhecidos do Vinext e AnyIO permanecem.
- Playwright: catálogo, seleção, salvamento/retomada simulados, revisão, retorno, análise de CRUD simulada e compositor em telas estreitas. Capturas `ux-compact-review-{1920,1366,1024,390}.png` e `ux-crud-assistant-{1366,1024,390}.png` em `data/mcp-validation/`, ignorado pelo Git.
- O comportamento de interpretação de um modelo real ainda depende da qualidade do modelo escolhido; os contratos e a interface foram validados com respostas simuladas, não com consumo pago. Não é uma certificação de cobertura nem substitui revisão humana.
- Backend reiniciado de modo gracioso somente após confirmar ociosidade: 62 explorações consultadas, nenhuma ativa, nenhuma base ativa e nenhum processo-filho de navegador. Usada a autorização já fornecida; frontend e abas do usuário não foram reiniciados.
- Conferência da API real após retirar as fixtures: **151 testes, 0 arquivados, 0 rascunhos e 0 execuções ativas**. Nenhuma execução, gravação, exploração ou chamada paga foi iniciada pelo QA. Nenhuma chave do `.env` foi lida; relatórios blob não foram modificados nem baixados.

## 17. Preparação em duas faixas horizontais — 22/10/2026

### Pedido e resultado local

O usuário pediu para substituir a lista vertical de CTs e o bloco extenso do roteiro por **duas listas horizontais com cards quadrados**, visíveis juntas em monitores pequenos.

- **Faixa superior — Testes:** ID, posição e título; o card ativo é destacado em amarelo. Clicar troca as etapas da faixa inferior. Setas no card mudam a ordem; retirar um CT não o arquiva e conserva as referências/pausas dos demais.
- **Faixa inferior — Etapas:** cards quadrados, botões amarelos **+** nos intervalos e pausas roxas para print com o mesmo tamanho dos cards originais. Existe inserção antes e depois de cada pausa, inclusive no início e no fim do roteiro. Uma pausa nova é trazida para a área visível.
- Título completo, resultado esperado, explicação da automação e textos integrais das etapas continuam disponíveis em **Detalhes do teste**, recolhidos por padrão. O painel cabe dentro da área de trabalho e admite rolagem própria. Não há título grande, parágrafo extenso ou paleta de print ocupando permanentemente a tela.
- Cada faixa possui rolagem horizontal independente, setas visíveis e navegação por teclado. A roda vertical do mouse desloca apenas a faixa sob o cursor; não move a outra faixa nem o conteúdo para baixo. Gestos horizontais nativos e atalhos de zoom do navegador são preservados. Toque usa a rolagem nativa do navegador; não foi simulada uma sessão física de aparelho móvel.
- Cards manuais usam a mesma apresentação, sem oferecer pausas automáticas não suportadas pelo executor. Salvar, revisar e confirmar continuam separados; não foi alterado o fluxo de execução nem o formato dos planos persistidos.

### Espaço e telas pequenas

O tamanho dos quadrados acompanha a altura disponível. Compactados o campo de nome e o rodapé nas áreas estreitas, preservando os nomes acessíveis e as ações. Em janelas baixas, a orientação repetida das três etapas é ocultada; os controles reais continuam disponíveis.

| Janela | Lado do card na medição | Duas faixas visíveis, sem rolagem vertical do editor |
| --- | --- | --- |
| 1920 × 1080 | 160 px | Sim |
| 1366 × 768 | 160 px | Sim |
| 1280 × 720 | aproximadamente 160 px | Sim |
| 1024 × 700 | aproximadamente 137 px | Sim |
| 1024 × 600 | aproximadamente 101 px | Sim, sem corte dos controles |
| 390 × 844 | aproximadamente 153 px | Sim; chat em painel próprio |
| 390 × 700 | aproximadamente 94 px | Sim, sem corte dos controles |

Em alturas excepcionais de até 560 px, há uma alternativa deliberada de rolagem vertical do editor para manter todos os controles alcançáveis, em vez de cortá-los. Não se promete que qualquer quantidade de conteúdo caiba sem rolagem em toda janela possível. Títulos extensos permanecem disponíveis integralmente nos detalhes e nos nomes acessíveis dos cards.

### Validação

- **318 testes do frontend aprovados**, incluindo o adaptador de roda/teclado, limites de rolagem, limpeza de eventos, ordem dos CTs, seleção ativa, planos de captura e cards manuais.
- TypeScript, ESLint dos arquivos desta etapa, build de produção e `git diff --check` aprovados, sob limites de memória/tempo. Backend não foi alterado nem reiniciado nesta etapa.
- Playwright: duas faixas em sete tamanhos de janela; setas em desktop e celular; Home/End; abertura dos detalhes com Enter; troca roteiro/chat conservando 3 CTs e uma pausa; entrada por link direto de CT após carregamento inicial.
- Com **151 CTs selecionados somente na interface**, um giro moveu a faixa superior em 450 px, mantendo a inferior em 0. Um giro na inferior a moveu em 400 px, mantendo a superior em 450. A página e o editor continuaram em posição vertical 0. End alcançou o fim da faixa superior.
- Inclusão de duas pausas, presença de **+** após ambas, remoção e alternância entre CTs conferidas sem escrita real. Salvamento da ordem e das posições foi validado em fixtures isoladas de componente.
- Capturas finais: `data/mcp-validation/horizontal-rails-{1920,1366,1280,1024,390}.png`, com três CTs e uma pausa apenas na interface. Pasta ignorada pelo Git.
- API real ao encerrar: **151 testes, 0 arquivados, 1 rascunho existente e 0 execuções ativas**. O rascunho real foi preservado; o QA não criou nem alterou rascunhos persistidos. POSTs reais do navegador de validação ficaram bloqueados. Nenhuma execução, gravação ou chamada paga foi iniciada; nenhum relatório blob foi alterado.
- Captura da versão de 1024 × 700 apresentada ao usuário. Aprovação registrada em 22/10/2026: **“Sim, ficou mais claro”**, em resposta à pergunta sobre as duas faixas horizontais para telas pequenas. Essa aprovação refere-se à organização dos CTs e etapas nesta área, não ao aceite de todas as telas do app.

## 18. Dicas nos botões de ação — 22/10/2026

- **Duplicar:** explica que abre uma cópia como novo teste manual para revisar e salvar; não cria nem executa um teste no clique inicial.
- **Arquivar:** explica que abre uma confirmação para retirar da base ativa, preservando resultados e prints. O estado arquivado também tem uma dica para **Restaurar**.
- Dicas visíveis ao passar o mouse ou focar pelo teclado, associadas ao botão por `aria-describedby`. Escape fecha; a dica permanece quando o mouse vai sobre seu texto. Renderização fora da área rolável evita cortes na primeira e na última linha, sem modificar o tamanho ou a posição dos botões.
- Validação por Playwright em 1366 × 768 e 390 × 844: mouse, foco, Escape, bordas da janela, última linha e fechamento ao rolar. Sem clicar em ações reais de duplicação/arquivamento; escritas reais bloqueadas. Capturas `catalog-copy-tooltip-1366.png`, `catalog-archive-tooltip-1366.png` e `catalog-tooltip-390.png` em `data/mcp-validation/`.
- Regressões de componente/SSR verificam os textos, nomes acessíveis, descrições prévias e estado desabilitado. TypeScript, lint e build conferidos; nenhuma dependência adicionada e nenhum backend reiniciado.

## 19. Editor de etapas com paleta e área ampliada — 22/10/2026

### Organização e interação

- O pedido mais recente substitui a ideia de uma prévia com modal separado: a edição fica na própria tela, próxima ao editor dos relatórios, no tema dark da aplicação.
- Divisão normal **30% Testes / 70% Etapas**, com mínimo para os controles superiores em janelas muito baixas. CTs ficam em cards horizontais compactos; ações de ordem e retirada não consomem uma linha adicional. As etapas ganham paleta, canvas pontilhado e rolagem horizontal independente.
- **Aguardar print, Executar SWS e Executar SQL** podem ser arrastados aos intervalos **+**, inclusive antes da primeira etapa, depois da última e entre outros cards extras. Cards adicionados podem ser reposicionados por arraste ou pelas setas. Selecionar um tipo e clicar no intervalo oferece alternativa sem arraste; botões funcionam com teclado.
- Originais permanecem protegidos, sem edição/remoção de automação nem alteração do resultado histórico. O limite é de 100 extras por teste. Mover preserva ID e tipo, sem duplicar; pausas reais mantêm o contrato `capture_plan` existente.
- SWS/SQL são **somente planejamento**, sem conexão, consulta, execução ou configuração de segredos. São persistidos em `planned_integrations` por CT, retomados com posições exatas e sinalizados na paleta, cards, rodapé e revisão. Frontend **e backend** impedem executar um plano com essas integrações indisponíveis; não são ignoradas silenciosamente. Removê-las não apaga prints. CT manual continua sem etapas de automação extras.
- Em áreas estreitas, a paleta vira uma faixa horizontal de três opções. Em áreas baixas, rótulos auxiliares são recolhidos; títulos, inserção, movimento, remoção, detalhes, salvar e revisar permanecem acessíveis. Não foram adicionados textos extensos nem um modal obrigatório ao fluxo.

### Validação e limites

- **326 testes do frontend** aprovados, incluindo tipos mistos, inserção, movimentos no mesmo intervalo/entre originais, IDs únicos, salvamento, retomada, remoção, arrastes inválidos e bloqueio da execução mesmo diante de uma disponibilidade incorreta do servidor.
- **90 testes isolados** de conjuntos, reexecução e arquivamento do backend aprovados: persistência e recusa de executar integrações, payloads/posições inválidos, manutenção do contrato de prints e ausência de alterações históricas ou início implícito. Nenhuma chamada paga ou integração real foi usada.
- TypeScript, ESLint dos componentes/helpers alterados, build de produção e `git diff --check` conferidos. Imports Python organizados; permanecem avisos preexistentes de lint em código legado/fixtures e a depreciação de AnyIO nos testes.
- Playwright em janela isolada: arrastar os três tipos, mover um card existente sem duplicação, teclado, inserção no fim, salvamento/retomada **simulados**, bloqueio da revisão e layout de 1920 × 1080 até 390 × 700. Nas medições estabilizadas, os três tipos ficam visíveis, cards cabem na faixa e salvar/revisar ficam na janela, sem rolagem vertical do editor nem largura extra da página. Não foi simulado um aparelho físico de toque.
- Capturas finais em `data/mcp-validation/steps-editor-final-{largura}x{altura}.png`, ignoradas pelo Git. Em alturas curtas, a proporção adapta-se ao mínimo do seletor em vez de cortar controles (aproximadamente 66% para etapas em 1024 × 600).
- Backend recarregado de modo gracioso após checar **62 explorações, 36 bases e nenhuma ativa**, usando a autorização anterior do usuário. O único rascunho real existente foi preservado. Escritas reais do navegador de QA ficaram bloqueadas; nenhum CT foi executado, nenhum segredo foi lido e relatórios blob não foram alterados.
- Implementação local apresentada para avaliação; ainda sem aprovação do usuário para esta nova organização.

## 20. Arrastar CTs para definir a ordem de execução — 22/10/2026

- O corpo dos cards da faixa **Testes** pode ser arrastado para antes/depois de outro CT ou para a área vazia após o último. Indicador amarelo mostra a posição de destino; numeração e ícone de arraste deixam a ordem explícita.
- A lista rola horizontalmente ao arrastar nas bordas. As setas anteriores continuam disponíveis pelo teclado. Escape/cancelamento, posição equivalente, payload inválido ou arraste de outro conjunto não mudam a ordem.
- Reordenar conserva o CT selecionado e os objetos completos de cada seleção: referência de execução, pausas para print e integrações planejadas. Não altera originais nem inicia uma execução. **Salvar rascunho** persiste a nova sequência; salvar/retomar foi conferido sem escrita real, por resposta de API simulada.
- **331 testes frontend aprovados**, incluindo inserção nas extremidades, no-op, referências imutáveis, indicadores, cancelamento, dados inválidos e serialização da ordem exata. TypeScript, ESLint dos arquivos alterados, build e `git diff --check` conferidos.
- Playwright em janela isolada: arraste nos dois sentidos, marcador antes/depois, Escape, setas com Enter, 151 CTs com rolagem nas bordas sem mover a faixa de etapas, retomada simulada e janela de 390 × 700 usando mouse. Sem teste de toque físico. Capturas `test-cards-drag-{1366,390}.png` e `test-cards-drag-indicator.png` em `data/mcp-validation/`.
- Sem alterações ou reinício de backend nesta etapa; nenhuma execução real, escrita de rascunho real, chamada de IA ou leitura de segredos.

## 21. Prévia em tamanho real ao arrastar etapas — 22/10/2026

- Aguardar print, Executar SWS e Executar SQL mostram um card flutuante do mesmo tamanho das etapas, acompanhando o cursor. A imagem pequena nativa do botão da paleta é substituída; a prévia não intercepta cliques nem destinos de soltura.
- Sobre um intervalo **+**, surge também o espaço de inserção em tamanho real. O + permanece ancorado para evitar que a abertura do espaço retire o alvo debaixo do cursor. Pode-se soltar sobre a própria prévia expandida.
- Prévia é estado visual transitório, sem ID de etapa, escrita ou alteração do rascunho. Soltar confirma uma única inclusão; sair do intervalo remove o espaço. Escape, fim do arraste ou perda de foco limpam a prévia. Ao mover uma etapa existente, a origem fica atenuada e mantém sua identidade até a confirmação.
- Roxo para print, amarelo para SWS/SQL; o rótulo de planejamento continua visível. Cards flutuantes são limitados às bordas da janela e mostram menos texto quando pequenos. A paleta permanece centralizada verticalmente, com disposição horizontal nas telas estreitas.
- Testes isolados conferem os três tipos, tamanho, cancelamento sem marcar um rascunho salvo como alterado, inclusão única na posição exata, limites da janela e renderização de servidor sem portal. TypeScript, lint, build e regressões do frontend conferidos.
- Playwright isolado em 1366 × 768 e 390 × 700: seguir o cursor, abrir espaço, soltar sobre a prévia, cancelar sem adicionar, medir prévia/card original (192 px e 98 px, respectivamente). Capturas `step-preview-{print,sws,sql}-held-1366.png` e `step-preview-print-held-390.png` em `data/mcp-validation/`, ignorado pelo Git. Sem teste de toque físico, gravação, IA paga ou escrita real; backend e relatórios não alterados.

## 22. Correção da área de inserção durante o arraste — 22/10/2026

- O retorno do usuário mostrou uma limitação da etapa anterior: o espaço só abria ao acertar o pequeno **+**. Agora todo o canvas reconhece o arraste, inclusive sobre as laterais dos cards e nas áreas vazias acima/abaixo deles. O intervalo mais próximo é escolhido pela posição horizontal.
- A prévia ocupa espaço real entre as etapas, afastando os cards seguintes. Enquanto o cursor estiver na prévia expandida, o destino permanece estável. Eventos de entrada/saída entre filhos do canvas não apagam o destaque. O **+** continua como alternativa de inserção por clique/teclado, não como requisito para arrastar.
- Dentro do canvas há apenas a prévia inserida na sequência: o card flutuante fica oculto para não encobrir o destino. Fora do canvas o card flutuante volta e o espaço é retirado; Escape/fim do arraste limpam ambos. Soltar confirma exatamente o intervalo destacado, mesmo que a abertura do espaço tenha deslocado o elemento sob o cursor.
- **341 testes frontend aprovados**, incluindo novos casos de posição fora do +, estabilidade após expansão, posições com rolagem, intervalos entre extras, soltura no destino exibido, saída/cancelamento e rascunho salvo intacto antes de soltar. TypeScript, lint e build de produção conferidos.
- Playwright isolado, com arraste nativo de mouse: print inserido sem passar no + em **1366 × 768** e **390 × 700**; SWS sobre área vazia acima dos cards e entre extras; SQL após a última etapa; movimento de SQL existente para antes/depois do primeiro original preservando ID; Escape e retorno do card flutuante ao sair do canvas. Não houve teste de toque físico.
- Capturas `data/mcp-validation/step-canvas-preview-off-plus-{1366,390}.png` mostram a prévia durante o arraste, antes da inclusão. O backend local estava indisponível; o QA visual usou respostas de API simuladas em janela isolada, com escritas reais bloqueadas. Nenhuma execução, gravação, IA paga, alteração histórica ou reinício de backend foi feito.

## 23. Prévia substitui o + durante o arraste — 22/10/2026

- O intervalo ativo mostra apenas o card de prévia, sem **+** à esquerda nem espaço vazio reservado ao botão. A âncora de detecção permanece mensurável, mas invisível, fora do fluxo visual, desabilitada e fora da navegação por teclado/leitor de tela, preservando a estabilidade do arraste.
- Depois de soltar, a prévia vira uma única etapa e reaparecem **um + antes e outro depois**. Cancelar devolve o botão original sem inserir nada. Comportamento compartilhado por print, SWS e SQL.
- Conferido por Playwright em 1366 × 768 e 390 × 700: largura da área de prévia exatamente igual à do card (192/98 px), ausência visual/acessível do + ativo, estabilidade do destino e dois botões visíveis e habilitados depois da inserção. Capturas `step-preview-no-plus-{1366,390}.png` e `step-dropped-plus-both-{1366,390}.png` em `data/mcp-validation/`.
- Regressões de componente verificam ocultação, desabilitação, cancelamento e restauração dos intervalos; TypeScript, lint, build e 341 testes frontend conferidos. QA visual usou GETs da base real com escritas bloqueadas e descartou apenas suas alterações temporárias. Sem salvar rascunhos, iniciar testes ou alterar backend/relatórios.

## 24. Busca compacta na escolha de testes — 22/10/2026

- Removido o **X** do cabeçalho do menu lateral. O controle de recolher no rodapé permanece; no celular o menu fecha pelo fundo externo, pela navegação ou por Escape, com retorno do foco ao botão de abrir.
- No topo do seletor ficam apenas **buscar testes + ícone de filtro integrado**, em uma linha de 42 px. Resultado/tipo, funcionalidade, limpeza e inclusão dos filtrados ficam no painel do ícone; a contagem destaca filtros ativos. O catálogo principal continua com seus controles existentes.
- O painel abre com foco no primeiro filtro e fecha por Escape, clique fora ou pelo próprio ícone, sem apagar busca/seleção. Limpar filtros mantém o texto pesquisado. **Ver roteiro** foi para o rodapé da lista, preservando uma ação clara nas telas em que o seletor ocupa o espaço do editor.
- Playwright em 1920 × 1080, 1366 × 768 e 390 × 700: topo compacto, painel limitado à largura disponível, busca combinada com resultado/funcionalidade, seleção preservada, Escape/clique fora e fechamento do menu móvel. Capturas `compact-selector-{1920,1366,390}.png` e `compact-selector-filters-{1920,1366,390}.png` em `data/mcp-validation/`. Sem teste físico de toque.
- **343 testes frontend aprovados**, incluindo novas regressões de filtros, inclusão em lote e teclado, além do shell sem o X. TypeScript, lint, build e diff conferidos. Escritas reais bloqueadas no navegador de QA; alterações temporárias de seleção descartadas. Sem salvar rascunhos, executar testes, chamar IA, alterar backend ou relatórios.

## 25. Remoção dos controles auxiliares marcados — 22/10/2026

- Retirado o estado de salvamento ao lado do nome; a informação permanece apenas no rodapé. Cabeçalhos das faixas mostram somente **Testes + quantidade** e **Etapas + CT**, sem instrução de arraste, contador de originais/extras, ícone de detalhes ou setas de navegação.
- Removidas as duas linhas estáticas abaixo do chat. Campo de mensagem, modelo, envio, alertas e confirmação real da execução continuam disponíveis. A orientação dos atalhos fica somente na descrição acessível do campo, sem ocupar espaço visual.
- Rolagem horizontal por mouse/trackpad e teclado, arraste e ações dentro dos cards preservados. Playwright verificou ambas as faixas, com 151 CTs selecionados só na interface; End alcançou o fim da faixa de testes e Home voltou ao início. Em 390 × 700 não houve largura extra da página e o compositor continuou acessível. Shift + Enter manteve a quebra de linha.
- Capturas `clean-planner-1920.png`, `clean-planner-390.png` e `clean-planner-chat-390.png` em `data/mcp-validation/`. Regressões conferem os cabeçalhos simplificados, o estado de rascunho no rodapé e a descrição acessível do chat; TypeScript, lint, build e testes frontend conferidos. Escritas reais bloqueadas no navegador; seleção de QA descartada sem salvar, executar, chamar IA ou alterar resultados/relatórios.

## 26. Card da base inteiro clicável — 22/10/2026

- A ação **Ver testes** cobre toda a linha da base: nome, ícone, estatísticas, data e espaços vazios. Há apenas uma ação nativa de abertura por card, com indicação visual de hover/foco e uso por teclado; o nome deixou de duplicar o mesmo destino como outro botão.
- A setinha de expandir permanece independente e acima da área de abertura. As execuções expandidas continuam com seus destinos próprios. Pressionar o card não muda a área clicável nem inicia qualquer execução.
- Playwright em 1366 × 768: cliques em cinco regiões da linha abriram a base correta; Enter abriu a mesma base e Tab mostrou foco na linha inteira. Expansão exibiu o histórico sem sair da lista. Em 390 × 700, clique na área inferior do card e expansão por teclado funcionaram sem largura extra da página.
- Capturas `base-card-focus-1366.png` e `base-card-clickable-390.png` em `data/mcp-validation/`. Regressões conferem abertura, ação nativa única, expansão independente, histórico e área clicável estável ao pressionar. TypeScript, lint, build e testes frontend conferidos; apenas navegação/GETs no QA, com escritas reais bloqueadas. Sem backend, relatórios ou dados alterados.

## 27. Nome da execução somente na revisão — 22/10/2026

- O campo **Nome da execução** saiu da preparação e passou ao cabeçalho compacto da revisão. Preparar/salvar um rascunho novo continua possível com o nome padrão; nomes existentes e os editados na revisão são mantidos ao voltar para ajustar os testes ou salvar.
- Digitar não salva nem inicia testes, não fecha a revisão e invalida o aceite anterior. Ao confirmar a execução com um nome alterado, o rascunho é salvo e revalidado antes de iniciar, usando a revisão atual. Mudanças nas definições/condições exigem nova conferência e aceite; erros conservam o nome e impedem início sem revalidação. Um nome vazio bloqueia a execução e, ao voltar para ajustar, restaura o último nome válido para não deixar um campo oculto bloqueando o editor.
- Playwright isolado em **1366 × 768** e **390 × 700**: campo ausente na preparação, edição somente na revisão, aceite reiniciado, seleção CT001/CT003 preservada e nome mantido após ajustar/salvar/revisar. Janela pequena sem largura extra nem rolagem vertical da página; cabeçalho da revisão medido em 56/81 px. Capturas `name-only-review-{1366,390}.png` e `name-only-review-prep-{1366,390}.png` em `data/mcp-validation/`.
- **353 testes frontend aprovados**, incluindo salvamento do nome/revisão correta, definições atualizadas, indisponibilidade, falha de salvamento/revalidação, retomada e nome vazio. TypeScript, lint, build e diff conferidos. QA usou GETs da base real e respostas simuladas para salvar/validar; escritas reais permaneceram bloqueadas, sem executar CTs, chamar IA, alterar rascunhos reais, backend ou relatórios.

## 28. Remoção da faixa numerada da preparação — 22/10/2026

- Retirada a linha **1. Escolher testes / 2. Ajustar roteiro / 3. Revisar e confirmar**, junto de seus estilos exclusivos. A área antes ocupada pela faixa fica disponível para o editor; os botões de escolher testes, salvar e revisar continuam em seus locais existentes.
- **72 testes do componente aprovados**, incluindo uma regressão que confere ausência da faixa, seleção e pausas preservadas, abertura do seletor e revisão com confirmação obrigatória. TypeScript, lint e diff conferidos. Sem alterações em backend, relatórios ou dados reais.

## 29. Preparação sem faixa de ações ou títulos das faixas — 22/10/2026

- Removida a faixa com Escolher testes, Adicionar teste duplicado, Assistente e ajuda. Um único botão de ícone, de 34 × 34 px, fica à direita das abas da preparação, no lugar do atualizar, e abre/fecha o seletor. Usa nome acessível, estado expandido e o componente de dica existente. Continua acessível quando o seletor ocupa o editor nas telas pequenas.
- Retirados os títulos visuais **Testes + quantidade** e **Etapas + CT**. Os nomes acessíveis das regiões, a identificação/seleção nos cards, a ordem e as pausas continuam preservados. Adicionar teste permanece no cabeçalho principal; o assistente continua no painel e na alternância móvel. O link Ver rascunhos passou ao rodapé de um rascunho salvo.
- Conferência visual e por cliques no navegador integrado em 1366 × 768 e 390 × 700: abertura/fechamento do seletor, busca e dois testes selecionados preservados, sem largura extra ou rolagem vertical da página. A automação desta sessão não confirmou Enter/hover; não foi feito teste físico de teclado/toque. A dica usa o componente existente e a ação é um botão HTML nativo.

## 30. Nova exploração direta e paginação de bases — 22/10/2026

- O botão de Meus testes passou de **Nova base de testes** para **Nova exploração** e navega diretamente para `#/nova-exploracao`, sem pergunta intermediária nem iniciar a exploração.
- Lista paginada em **20 bases por página**, após busca/filtro e ordenação por atividade mais recente. Anterior/Próxima, página atual e faixa de resultados ficam fora da lista rolável. Busca e filtro pesquisam a lista completa e voltam à primeira página; atualização limita a página ao novo total e não deixa uma última página vazia. Listas de até 20 itens não mostram controles desnecessários.
- A troca de página usa os dados já carregados e volta a lista ao topo. A página é conservada na memória da sessão ao abrir uma base ou Nova exploração e retornar, sem armazenar texto de formulários ou credenciais.
- Navegador integrado com dados reais: 36 bases distribuídas em 20 + 16, retorno à página 2, busca vazia/limpeza e destino correto de Nova exploração. Em 390 × 700 a paginação ficou visível no rodapé, sem largura extra da página. Foram descartadas apenas as duas seleções temporárias do QA; nenhuma exploração, execução, gravação ou chamada de IA foi iniciada, nenhum rascunho real foi salvo e backend/relatórios não foram alterados.
- **359 testes frontend aprovados**, com novos casos de 45 bases, limites/páginas parciais, ordenação, busca global, filtros, atualização reduzindo o total, lista vazia, navegação direta e botão de seleção compacto. TypeScript, lint e build conferidos; diff sem erros.

## 31. Etapas aproveitam o espaço abaixo dos testes — 22/10/2026

- A faixa superior deixou de reservar 30% da altura: agora se ajusta aos cards, com margem para a barra horizontal. O painel de etapas ocupa todo o restante da área, preservando o intervalo de 8 px entre os painéis. Tamanho dos cards de testes, arraste, chat e rodapé mantidos.
- Conferido no navegador integrado em **1920 × 1080**, **1366 × 768** e **390 × 700**, com CT001/CT003/CT004 selecionados apenas para QA. No monitor grande, a faixa superior ficou com 166 px e as etapas com 716 px; no celular, a página permaneceu em 390 × 700 e a lista superior sem corte vertical (altura interna e conteúdo de 79 px).
- Atualizadas as regressões que exigiam a antiga proporção rígida para conferir a altura ajustada aos cards e o preenchimento restante pelas etapas. Build e testes isolados de componente conferidos. Apenas seleção local descartada ao terminar: nenhum rascunho real salvo, teste executado ou alteração em backend/relatórios.

## 32. Adicionar teste começa no assistente — 23/10/2026

- **Adicionar teste** abre o chat e pergunta “Como você gostaria de realizar esse teste?”, com apenas dois cards dentro da própria mensagem: **Gravar minha navegação** e **Planejar com IA**. Retirados o seletor de três modos, o acesso direto ao formulário vazio e o bloco de descoberta desse fluxo. Repetir o clique revela a pergunta atual sem duplicá-la; conversa, texto ainda não enviado, modelo, ordem dos testes e pausas são preservados.
- Planejar com IA pede a tela e o objetivo, incluindo a comparação do CRUD com a base, sem enviar nada automaticamente. Gravar mostra os cuidados e exige confirmação explícita antes de abrir um navegador; confirmação, andamento e finalização ficam no chat. O roteiro gravado e as propostas ainda passam pelo editor de revisão, com as opções de salvar apenas na base ou também incluir na execução. Edições não salvas e gravações em andamento continuam protegidas.
- Na preparação, os dois painéis de cards permanecem livres do formulário de criação. Na manutenção da base, enquanto não houver um roteiro para revisar, aparece somente o assistente, sem coluna de editor vazia. Em telas pequenas, o clique abre diretamente o painel do chat. Histórico de descobertas e relatórios não foram alterados.
- Conferência visual e por cliques no navegador integrado em **1366 × 768** e **390 × 700**: pergunta, dois cards, continuação da IA e confirmação de gravação acessíveis, sem largura extra ou rolagem vertical da página. A seleção temporária CT001/CT003 foi limpa ao final; nenhum rascunho real foi salvo e nenhuma gravação, execução ou chamada paga de IA foi iniciada.
- **362 testes frontend aprovados**, cobrindo escolha dentro da última mensagem, ausência do bloco antigo, ausência de envios automáticos, confirmação/finalização isolada da gravação, preservação da conversa/modelo/seleção/prints e proteção do editor não salvo. TypeScript, lint, build e diff conferidos.

## 33. Destaque temporário das escolhas no chat — 23/10/2026

- Clicar em **Adicionar teste** inicia um brilho dourado nos dois cards do assistente: três pulsações suaves de 1,4 segundo, totalizando 4,2 segundos. Ao terminar, os cards voltam ao estilo normal. Repetir o clique reinicia apenas o destaque, sem duplicar a pergunta nem apagar a mensagem em edição; digitar não reinicia o efeito.
- O efeito altera somente borda, fundo e sombra, sem mover ou redimensionar os cards. Com `prefers-reduced-motion`, o destaque fica estático pelo mesmo período, sem pulsações. Não foram adicionados timers nem chamadas à API.
- Conferido no navegador integrado em **1366 × 768** e **390 × 700**: ambos os cards receberam a animação finita; após o período, a sombra voltou a `none`; novo clique voltou a produzir brilho. Página sem largura extra ou rolagem vertical. Movimento reduzido coberto por contrato CSS nos testes. Nenhuma gravação, execução, salvamento ou chamada de IA realizada no QA.
- **363 testes frontend aprovados**, incluindo reinício do destaque apenas pelo clique explícito, preservação do texto e estilo alternativo de movimento reduzido. TypeScript, lint, build e diff conferidos.

## 34. Captura da etapa no próprio editor — 23/10/2026

- Os cards originais de etapas agora são botões completos. Selecionar uma etapa destaca o card, compacta as duas faixas e abre sua imagem abaixo, mantendo chat e rodapé acessíveis. Fechar restaura o espaço das etapas e devolve o foco ao card. Trocar de CT fecha a imagem; nenhuma seleção, ordem, pausa ou resultado é alterado por essa visualização.
- A captura usa a largura do painel, com rolagem interna, zoom de 50% a 200%, ajuste a 100% e abertura do original em outra aba. Carregamento, ausência de imagem e falha de leitura têm estados próprios. Uma captura ausente nunca é substituída pela de outra etapa; isso inclui um arquivo antigo indisponível encontrado no QA.
- Catálogo e contexto histórico passam a incluir somente o nome seguro da captura e sua execução proprietária nas etapas já existentes. A prévia de uma ação usa apenas sua referência explícita; imagens enviadas anteriormente pelo usuário seguem a mesma ordem preservada. Sem imagens embutidas, geração de relatório blob, gravação de evidência ou alteração de histórico. Referências históricas explícitas continuam usando o dono antigo, não o resultado mais recente.
- Conferido por cliques e inspeção visual no navegador integrado em **1920 × 1080**, **1366 × 768** e **390 × 700**, com capturas reais de CT001. A imagem ocupou 501 px de altura no monitor grande; no celular, as faixas e o cabeçalho foram compactados para reservar mais espaço à captura. Página sem largura extra ou rolagem vertical nos três tamanhos; somente a imagem ampliada/conteúdo maior usa rolagem interna.
- **371 testes frontend e 135 testes backend aprovados**: vínculo da captura à execução correta, contexto histórico, rejeição de URLs/caminhos inválidos, ausência/falha/tentativa de recarregar, zoom, troca de CT, fechamento e inserção por arraste com imagem aberta. Soltar uma ferramenta sobre a imagem não insere uma etapa. TypeScript, lint, build e diff conferidos.
- API local atualizada após conferir que não havia execuções ativas; segue na porta 8000. Os três CTs selecionados temporariamente para QA foram desmarcados, sem salvar rascunho, executar teste, gravar navegação ou solicitar geração à IA. Relatórios blob não foram modificados.

## 35. Imagem usa também a largura abaixo das ferramentas — 23/10/2026

- A prévia saiu da coluna dos cards e passou a ser uma segunda linha do editor, abrangendo as duas colunas. Ferramentas e etapas ficam somente acima dela, sem coluna vazia ao lado da imagem. As ferramentas são compactadas apenas enquanto a prévia está aberta; ao fechar, o layout anterior é restaurado.
- Na tela pequena, as linhas são ferramentas, etapas e imagem, preservando a altura restante para a captura. Zoom, troca/fechamento da imagem, chat, rodapé e inserção de etapas continuam disponíveis.
- Navegador integrado em **1366 × 768**, **1920 × 1080** e **390 × 700**: a imagem teve exatamente a largura e o alinhamento esquerdo do editor (720, 1178 e 342 px, respectivamente), abaixo das ferramentas; sem largura extra ou rolagem vertical da página. Apenas dois CTs foram selecionados temporariamente e desmarcados ao terminar, sem salvar ou executar.
- Regressões conferem que a prévia é irmã da paleta e da faixa de etapas, não filha da coluna direita, e ocupa todas as colunas no desktop, com três linhas no celular. **371 testes frontend aprovados**; TypeScript, lint, build e diff conferidos. Sem mudanças no backend ou nos relatórios.

## 36. Arrastar a imagem para movimentá-la — 23/10/2026

- A captura carregada pode ser movimentada segurando e arrastando com o botão principal do mouse ou caneta, nos dois eixos. O cursor passa de mão aberta para fechada durante o movimento. A captura do ponteiro mantém o arraste ao sair da imagem; soltar, cancelar ou perder a captura encerra o movimento. A imagem não inicia um arraste de arquivo nem interfere na ordem dos testes ou etapas.
- O movimento usa a rolagem interna e seus limites, sem mover a página. Zoom, ajuste, fechamento, barras de rolagem e região focável continuam disponíveis. Toque permanece com rolagem e gesto de zoom nativos; botões secundários, barras, imagens indisponíveis/em carregamento e imagens sem conteúdo excedente não são interceptados.
- Conferido com arraste real no navegador integrado em **1366 × 768** e **390 × 700**, com zoom de 125%: deslocamentos internos de 140 × 90 px e 70 × 47 px, respectivamente, e retorno ao início pelo movimento inverso. A página permaneceu sem deslocamento ou dimensões excedentes. Não foi realizado teste físico de toque/caneta. Apenas a seleção temporária CT001/CT003 foi limpa, sem salvar ou executar.
- **374 testes frontend aprovados**, incluindo movimento, limites, captura/liberação/cancelamento e gestos ignorados. TypeScript, lint, build e diff conferidos. Sem mudanças no backend, nos relatórios ou nas imagens preservadas.

## 37. Modo compacto sem instruções extras — 23/10/2026

- Ao abrir a imagem da etapa, os textos **Adicionar etapa** e **Arraste entre as etapas. Use + para inserir por clique.** deixam de ocupar espaço. Fechar a imagem restaura as orientações do modo normal; ferramentas, inserção e arraste continuam disponíveis. O aviso de integração SWS/SQL apenas em rascunho é preservado quando necessário.
- Conferido no navegador integrado em **1366 × 768** e **390 × 700**, sem largura extra ou rolagem vertical da página. A seleção temporária de CT001 foi descartada sem salvar ou executar. **100 testes focados aprovados**, incluindo ocultação/restauração das instruções e preservação dos avisos. TypeScript, lint, build e diff conferidos.

## 38. Navegar pelas etapas segurando e arrastando — 23/10/2026

- Segurar e arrastar horizontalmente o fundo da área de etapas ou um card original movimenta a faixa, com cursor de mão. Um limiar de 5 px distingue o arraste de um clique simples: arrastar não abre/fecha imagens nem altera a ordem das etapas; clicar continua abrindo a captura. O movimento fica limitado à faixa e não desloca a página.
- Ferramentas, etapas extras arrastáveis, botões de inserção/remoção e a imagem não são capturados por essa rolagem. Toque, teclado, barras e gestos nativos continuam disponíveis. Liberação, cancelamento, perda de captura e desmontagem limpam os eventos e o estado do cursor; o clique gerado ao terminar um arraste é consumido sem bloquear o próximo clique intencional.
- Navegador integrado em **1366 × 768**: arrastar sobre um card moveu a faixa em 140 px sem abrir imagem; clicar abriu a captura; arrastar o fundo no modo compacto moveu mais 200 px mantendo a mesma captura. Em **390 × 700**, arrastar moveu 70 px, sem largura extra ou rolagem da página. Inserção e remoção por clique também conferidas; não foi confirmado arraste HTML de ferramentas pela automação nem teste físico de toque/caneta.
- **381 testes frontend aprovados**, incluindo limites, limiar, clique/teclado preservados, controles ignorados, cancelamento e limpeza. TypeScript, lint, build e diff conferidos. A pausa e a seleção temporárias de CT001/CT003 foram descartadas; nenhum rascunho salvo, CT executado ou chamada de IA realizada. Backend e relatórios não alterados.

## 39. Card de teste em construção — 23/10/2026

- Na preparação, **Adicionar teste** revela um único card roxo com borda tracejada e selo **Em construção**, separado dos CTs reais. Repetir o clique não duplica nem apaga a proposta. Título, quantidade de etapas e prévia acompanham as respostas da IA ou o roteiro gravado; propostas múltiplas exigem escolha no chat. A definição atual segue como contexto limitado para o próximo ajuste, sem ser tratada como teste salvo.
- Revisão e salvamento continuam explícitos. O card não recebe ID fictício, não aumenta a seleção nem entra em planos/execuções. Falhas e perguntas de esclarecimento preservam a última proposta. Descarte de conteúdo exige confirmação; gravação exige aceite e finalização. Navegação protege conteúdo não salvo.
- Conferência visual anterior no navegador integrado em **1366 × 768** e **390 × 700**: card distinto, captura existente preservada e página sem dimensões excedentes. Respostas/refinamentos, escolha de propostas, gravação e falha de salvamento foram verificados com fixtures, sem chamada paga de IA ou execução real. O conjunto backend passou com **160 testes**, incluindo contexto do rascunho e rejeição de entradas inválidas antes do modelo. As regressões finais frontend estão contabilizadas abaixo.

## 40. Fechar Sobre o conjunto ao clicar fora — 23/10/2026

- O painel fecha ao pressionar fora dele, inclusive em outro botão, sem cancelar a ação desse botão ou roubar seu foco. Cliques dentro permanecem livres para consultar/selecionar as informações. O ícone mantém a alternância nativa; **Esc** fecha e devolve o foco ao ícone. Os eventos são removidos ao desmontar o componente.
- **391 testes frontend aprovados**, incluindo os novos casos do card em construção, clique externo/interno e Escape; TypeScript, lint, build e diff conferidos. Não havia navegador conectado para repetir a conferência visual do fechamento nesta sessão. Nenhum CT, rascunho ou relatório foi alterado pelo QA.

## 41. Painel J durante a gravação da navegação — 23/10/2026

- O gravador passa a instalar o painel flutuante **J**, com visual dark próprio da gravação, contador e passos ao vivo, recolhimento e **Finalizar e revisar roteiro**. O painel reaparece após navegação e acompanha novas janelas da mesma origem. É uma interface injetada pelo AutoJev, não uma extensão instalada no navegador. A confirmação prévia para abrir a gravação permanece obrigatória.
- A finalização pelo J encerra somente o navegador dessa gravação. A aplicação detecta o término e abre o roteiro para revisão, atualizando o card em construção sem salvar CTs nem iniciar execução. Um resultado terminal temporário, limitado a 20 gravações e 15 minutos, permite essa consulta e torna a finalização repetida idempotente. Encerramento aguarda chamadas de atualização em andamento antes de fechar o navegador.
- Controles de finalização ficam em iframe isolado com origem opaca, nonce por documento e CSP sem conexões externas. Comandos vindos da página testada, frames antigos ou cliques sintéticos são rejeitados. Não há captura de senhas, valores digitados, cookies ou screenshots; cliques no próprio painel não entram no roteiro. CSP da aplicação testada não é enfraquecida: se impedir o iframe, a finalização continua disponível no AutoJev.
- **120 testes backend focados aprovados**, incluindo gravação e painel de reexecução, navegação, janela adicional, privacidade, finalização real em navegador descartável e janela de **390 × 700**. **393 testes frontend aprovados**, com consulta de andamento e transição automática para revisão; TypeScript, lint, build e diff conferidos. Captura do painel inspecionada em fixture local, sem controlar o navegador ou a gravação do usuário, chamar IA ou executar CTs reais.
- A API local estava parada e não havia processo de navegador de gravação ativo; foi iniciada com o código atualizado e respondeu `status: ok`. A interface também foi iniciada e respondeu HTTP 200 em `localhost:3000`. O painel estará disponível nas novas gravações.
