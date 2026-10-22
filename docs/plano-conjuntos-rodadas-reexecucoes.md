# Conjuntos de testes, planejamento e rodadas

## Objetivo

Uma exploração inicial passa a ser a origem de um conjunto. Suas reexecuções pertencem ao mesmo conjunto, sem alterar registros históricos. O usuário pode planejar vários CTs, criar novos casos e consultar tanto o resultado de uma rodada quanto o consolidado.

## Regras do produto

- Agrupar pela origem persistida, nunca apenas pelo domínio da aplicação.
- Manter as identidades dos CTs e a propriedade das evidências. Uma reexecução não renumera o CT original.
- Execução inicial, rodada e consolidado são contextos diferentes. Resultados anteriores não contam como executados na rodada atual.
- Rascunhos não executam nada. Iniciar exige revisão e confirmação explícita.
- Rodadas são sequenciais, com preparação/consentimento próprios de cada CT. Não compartilhar implicitamente o estado do navegador.
- Iniciar uma rodada é idempotente: repetir a requisição não duplica ações.
- Parar uma rodada impede iniciar próximos CTs; resultados já registrados permanecem.
- Casos novos podem ser manuais. Texto livre nunca vira uma ação automática sem receita validada.
- Alterações de casos/plano usam versões e detecção de conflito; uma rodada iniciada preserva seu planejamento.
- Prints anexados não aprovam testes. Credenciais e receitas privadas não entram nas exportações.
- Reinício do servidor não retoma ações automaticamente nem repete CTs já despachados.

## Entregas

### 1. Fundação e agrupamento

- [x] API de conjuntos que reconhece reexecuções antigas sem migração destrutiva.
- [x] Listagem agrupada, com busca, resumo e rodadas expansíveis.
- [x] Página do conjunto com catálogo, resultado consolidado e histórico.

### 2. Planejamento persistente

- [x] Rascunhos nomeados com vários CTs, ordem e pausas para prints por CT.
- [x] Seleção por busca/status, seleção em lote e duplicação de planejamento.
- [x] Catálogo de novos CTs manuais, com pré-condições, passos e resultado esperado.
- [x] Validação de disponibilidade dos CTs automáticos antes da confirmação.

### 3. Rodadas reais

- [x] Orquestração sequencial reutilizando o executor individual seguro.
- [x] Acompanhamento por CT e acesso à preparação/prints já existentes.
- [x] Registro explícito dos resultados dos CTs manuais.
- [x] Cancelamento, persistência e recuperação segura após interrupção.

### 3.1 Assistente e descoberta de novos CTs

- [x] Chat de planejamento com seletor de LLMs ativos, busca e agrupamento por provedor; inicia no modelo auxiliar do conjunto, sem alterar os modelos da exploração/execução.
- [x] Chat redesenhado com histórico rolável, compositor fixo, atalhos de teclado, respostas identificadas pelo modelo, cópia e estados de carregamento/erro acessíveis.
- [x] IA sugere seleção de CTs e novos rascunhos; nenhuma resposta executa ações ou altera um plano sem aceite.
- [x] Respostas estruturadas e validadas, IDs existentes conferidos e limites de contexto/chamadas/tokens; custo informado quando o provedor o disponibiliza.
- [x] Modo de descoberta automática aproveitando a exploração existente, com confirmação e vínculo ao conjunto.
- [x] Modo de gravação: o usuário navega em um navegador de teste e transforma interações em rascunhos revisáveis.
- [x] Não persistir senhas, tokens, cookies ou valores de campos de entrada da gravação; referências são descritivas.
- [x] Passos gravados ou sugeridos não recebem aprovação nem automação fictícia. O usuário define o resultado esperado e revisa o CT antes de usá-lo.

### 4. Relatórios e exportação

- [x] Relatório consolidado com origem/data de cada resultado e aviso de resultados anteriores.
- [x] Relatório de rodada apenas com seus CTs e suas evidências.
- [x] Execução inicial preservada e exportável separadamente.
- [x] Comparação básica de rodadas por identidade do CT, incluindo CTs não executados em uma delas.
- [x] Exportações autossuficientes com contexto e data de geração explícitos.

### 5. Validação

- [x] Testes de linhagem, isolamento entre conjuntos e propriedade das imagens.
- [x] Testes de planos, versões, consentimento, idempotência e cancelamento.
- [x] Testes de resultados manuais e consolidação sem aprovações fabricadas.
- [x] Verificação de tipos, regressões existentes e QA de interface em desktop e celular.

## Implementação e limites

As sessões técnicas existentes continuam independentes e imutáveis. O agrupamento e os planos são uma camada adicional. Novos CTs manuais são executados e avaliados pelo usuário; a automação continua limitada às receitas que o executor atual consegue validar. Integrações SWS/SQL continuam indisponíveis. Histórico completo em ZIP com deduplicação de imagens é uma evolução posterior; a entrega inicial reutiliza os relatórios HTML autossuficientes.

- Uma rodada admite até 500 CTs, executados sequencialmente. Só uma rodada pode estar ativa por vez para limitar o consumo de navegadores/memória.
- Cada CT automático continua exigindo preparação no seu navegador independente. Cancelar/falhar uma sessão interrompe o restante da rodada; falhas de asserção registradas normalmente permanecem resultados válidos do histórico.
- Pedidos diretos com IDs, como “adicione o CT004 também na reexecução”, marcam os CTs no rascunho e atualizam a ordem sem chamar um modelo. Não duplicam a seleção, não salvam nem executam a rodada; IDs inexistentes/ambíguos impedem a inclusão parcial. Solicitações ambíguas continuam como sugestões revisáveis.
- Consultas à IA usam uma chamada por mensagem, até 12 mensagens de contexto e até 3.500 tokens de saída. Não recebem screenshots, receitas privadas ou credenciais. Custos não informados pelo provedor não são tratados como gratuitos. O botão de aceite conta somente os CTs sugeridos que ainda não estão selecionados.
- O seletor do chat usa apenas o catálogo configurado, sem descobrir modelos nem enviar mensagens ao trocar a opção. A API confere o par provedor/modelo, a ativação e as modalidades de texto antes de chamar o provedor. A escolha permanece na conversa ao fechar/reabrir o painel; não muda a configuração global nem o modelo de descoberta. Cada resposta e evento de consumo identificam o modelo/provedor realmente usados.
- A descoberta usa até 10 ações / 5 minutos de navegação na interface (a API permite até 30 ações), com documentação limitada e confirmação própria. O usuário revisa e importa os casos; eles recebem IDs do conjunto sem colisão com os CTs originais.
- A gravação requer backend com navegador visível (`AUTOJEV_HEADLESS=false`), abre um contexto novo e aceita até 100 interações em 15 minutos. Não anexa o navegador pessoal do usuário nem cria uma automação pronta.
- Roteiros manuais têm versões. Aprovar uma versão antiga não aprova uma nova definição do mesmo CT. Planos já salvos mantêm suas definições e referências.
- A geração de HTML é serializada para não codificar vários relatórios grandes simultaneamente; os arquivos temporários são removidos após o download.

## Como usar

1. Reinicie o backend para carregar as novas rotas e criar as tabelas adicionais. Não reinicie durante uma execução ativa.
2. Abra **Explorações**, agora organizada por conjuntos, e escolha **Abrir conjunto**.
3. Entre em **Planejamento**, dê nome à rodada na barra superior e use **Selecionar CTs** para abrir o catálogo. Marque os casos e clique em **Ver roteiro**. **Novo CT** oferece escrita manual, ajuda do chat, gravação e descoberta com IA. Em áreas estreitas, as abas **Roteiro** e **Assistente** alternam o painel visível sem apagar a conversa ou o rascunho.
4. Salve o planejamento, revise a disponibilidade dos CTs e confirme a rodada real.
   Os rascunhos salvos também ficam no menu **Execuções planejadas**, abaixo de **Explorações**. Busque por nome/aplicação/conjunto, filtre o conjunto e use **Continuar planejamento** para retomar a edição. A listagem mostra apenas rascunhos ainda não executados, pela última alteração (mais recentes primeiro); não agenda nem inicia ações automaticamente.
5. Acompanhe os CTs automáticos pelo botão **Acompanhar CT**; retorne pelo botão **Voltar à rodada**. Para CTs manuais, registre o que observou e escolha explicitamente a avaliação.
6. Exporte o consolidado, a execução inicial ou apenas a rodada desejada. As versões anteriores continuam disponíveis no histórico.

### Fluxos funcionais

A separação anterior entre principais/complementares foi substituída em 20/10/2026 pelo [plano de fluxos funcionais](plano-fluxos-funcionais.md), conforme a nova orientação do usuário.

- O catálogo usa um seletor compacto com resumo expansível, e a aba **Testes** do relatório apresenta cards com objetivos concretos por entidade, como **Cadastrar boleto** ou **Consultar produto**, não agrupamentos genéricos de importância.
- A seleção de fluxo filtra a tabela original; o resumo mostra contagens reais por resultado e distingue associações identificadas de sugeridas. **Todos os testes** e **Não classificados** mantêm acesso a todos os registros.
- Os contadores respeitam os demais filtros. O percentual considera apenas PASS/FAIL, com denominador explícito; não representa cobertura, prioridade ou uma jornada comprovadamente completa. Um CT pode pertencer a mais de um fluxo.
- O seletor do planejamento usa os mesmos fluxos, sem apagar os CTs já selecionados. No catálogo, marcar CTs revela uma barra persistente com quantidade/IDs e **Planejar reexecução dos N CTs**. Esse botão abre o planejamento apenas dos marcados, sem iniciar ações. A seleção de todos os filtrados é uma ação separada; CTs marcados fora dos filtros são indicados explicitamente.
- A identificação é local, compartilhada entre catálogo e exportação: não consome tokens, inicia navegação ou altera registros. O CSV inclui fluxo, confiança e motivo. O HTML preserva todos os CTs e evidências, independentemente dos filtros aplicados antes do download.
- Reabra a exportação pela aplicação para receber a nova apresentação; arquivos HTML e abas blob já gerados permanecem intactos.

Todos os testes devem usar fixtures, limites de memória e tempo, sem iniciar execuções reais contra a aplicação do usuário.

### Planejamento ajustado à área disponível — 21/10/2026

- O planejamento ocupa a altura restante da aplicação, incluindo a presença do cabeçalho móvel e de avisos de conexão. Nome da rodada, estado do rascunho e ações ficam em uma barra curta; orientações completas ficam em uma seção recolhida. A área não depende mais de uma altura mínima de 560 px para o chat nem empilha a conversa depois de todo o conteúdo em notebooks.
- Em áreas amplas, roteiro e chat ficam lado a lado; o chat pode usar até 420 px de largura. O catálogo é recolhível e não ocupa uma terceira coluna permanentemente. Quando não cabe catálogo e roteiro ao mesmo tempo, **Ver roteiro** alterna a área principal; o assistente permanece ao lado. Em áreas ainda mais estreitas, **Roteiro** e **Assistente** alternam o painel inteiro.
- Mensagens, lista de CTs e conteúdo do roteiro têm rolagem independente. O compositor e o seletor de modelos ficam fora da área de mensagens, no rodapé do chat. A introdução é menor e o menu de modelos se limita à altura do próprio painel. As ações de salvar/revisar ficam na base da área de trabalho, sem sobrepor o chat nem o editor.
- Novos CTs, editor, gravação e revisão ficam dentro da área rolável do roteiro, sem deslocar a conversa. Alternar painéis preserva texto em edição, mensagem não enviada, modelo, conversa, CTs, ordem e pausas para print. **Revisar rodada** traz a revisão para foco; executar continua dependendo de consentimento explícito.
- Validação com eventos React isolados, renderização estática de conversas longas e contratos CSS: catálogo recolhível, troca de painéis, foco, revisão, preservação do rascunho e ausência de envio/execução automática. Sem navegação real, chamada de LLM, exportação volumosa ou alteração de backend. Os limites responsivos foram verificados por código, não por inspeção visual automatizada.
- Resultado: 100 testes Node focados, TypeScript, ESLint e build de produção aprovados com limites de tempo/memória. A aplicação local continua respondendo HTTP 200, sem reiniciar o backend.

## Registro de progresso

- Plano criado antes da implementação. Base de trabalho limpa e versão anterior preservada no Git.
- Escopo ampliado a pedido do usuário: assistente de IA, descoberta automática e gravação assistida de testes.
- Implementação entregue com migração aditiva, preservação de fontes, preparação/consentimento por CT e bloqueio de reenvio de rodadas.
- Verificações realizadas com dados isolados e modelos simulados: tipos, ESLint, build de produção, testes de API/serviço, privacidade da gravação e interface em 1440px/390px.
- Backend reiniciado em 19/10/2026 com autorização do usuário e após confirmar ausência de execuções ativas. Saúde e novas rotas verificadas; as 60 sessões existentes mantiveram seus estados e passaram a aparecer em 36 conjuntos. Frontend e VS Code preservados; nenhuma execução/modelo real do usuário foi disparado durante o desenvolvimento ou na ativação.
- Corrigida a inclusão direta de CTs pelo chat: confirmação baseada na alteração real do rascunho, proteção contra duplicatas/IDs desconhecidos, preservação das pausas já planejadas e sugestões sem alegações de aplicação. Regressões verificadas em desktop/celular com API e modelos simulados, sem executar rodadas reais.
- Entregue seletor de modelos e visual premium do chat. Validados troca de provedor, rejeição de modelos indisponíveis, preservação da configuração original, atalhos/teclado e ausência de sobreposição do compositor pela barra de planejamento (1440px/390px).
- Pausas “Aguardar print” passaram a usar o mesmo formato e dimensões dos cards de referência, com identidade roxa. Os pontos de inclusão são amarelos e aparecem antes/depois de cada pausa. Cliques e arrastes respeitam a posição escolhida, mantendo IDs e a ordem do planejamento salvo; inserção/remoção verificadas em desktop e celular.
- Removido o cabeçalho do chat e transferido o seletor de LLMs para dentro do compositor, no lugar de “Só planejamento”. O menu compacto abre para cima, mantém busca por modelo/provedor e não envia a mensagem ao pressionar Enter na busca. Posição, teclado, envio e seleção verificados em desktop e celular.
- Histórico expandido de cada conjunto unificado e ordenado pela data/hora exibida, da mais recente à mais antiga, sem fixar a execução inicial no topo nem separar rodadas das execuções vinculadas. Links, filtros, atualização e ordenação com segundos/fusos diferentes verificados sem alterar registros de execução.
- Removido o card de contexto do topo dos relatórios. Escopo, origem e data de geração permanecem como metadados embutidos no HTML, sem ocupar espaço visual. Testes cobrem exportação inicial, consolidada e de rodada, preservando resultados e dados originais. Arquivos/abas já gerados precisam ser reabertos pela aplicação para receber o novo HTML.
- Adicionado o menu **Execuções planejadas** na seção Workspace, logo após **Explorações**, com uma página global dos rascunhos salvos. Inclui busca sem distinção de acentos, filtro por conjunto, data da última alteração, quantidade de CTs, atualização manual e links para continuar o planejamento ou abrir sua origem. Reutiliza a API existente sem alterar registros, agendar horários, chamar modelos ou executar testes. Ordenação, isolamento entre conjuntos, rotas e renderização cobertos por testes focados.
- Corrigido o fluxo das três opções de criação de CTs: seleção explícita com destaque e foco/rolagem para o editor, compositor do chat ou confirmação de gravação. Cliques repetidos não apagam o rascunho nem escondem a confirmação; alternar entre modos preserva os campos preenchidos. A barra de planejamento deixa de sobrepor o editor. Gravações ativas permanecem visíveis ao fechar as opções, e finalizar abre um rascunho revisável. Testes isolados de eventos/foco cobrem os três caminhos, sugestões da IA, duplicação, salvamento manual e a ausência de execução automática, usando somente dados e chamadas simulados.
- Separação de CTs principais e complementares entregue no catálogo, no planejamento e nas exportações, com filtros CRUD e uma regra compartilhada de classificação somente de apresentação. Verificadas classificação em português/inglês, casos sem metadados, preservação de status/evidências, igualdade entre catálogo e relatório, seleção em lote e filtros/CSV/HTML offline com dados isolados. A apresentação inclui uma versão de classificação para invalidar apenas o cache do relatório, sem alterar exportações históricas ou registros originais.
- Atualização ativada em 20/10/2026: os servidores locais estavam desligados; a aplicação foi iniciada após consulta somente de leitura confirmar 62 sessões encerradas e uma rodada concluída, sem trabalho ativo. As 36 origens e os estados das 62 sessões foram preservados. O catálogo do conjunto de 151 CTs apresenta 92 principais e 59 complementares com os critérios locais desta versão. A verificação de ativação consultou apenas resumos/catálogo, sem gerar relatórios volumosos, iniciar testes, abrir navegadores ou chamar modelos.
- Revisão posterior em 20/10/2026, conforme plano anexado pelo usuário: substituída a classificação binária por cards de **Fluxos funcionais**, compartilhados por catálogo, planejamento e HTML. No conjunto de 151 CTs, nove fluxos e 42 CTs não classificados, preservando 150 PASS e um FAIL. Classificação frágil explicitamente sugerida; sem alegações de cobertura ou CRUD completo. Tipos, lint, build, 104 testes Python focados e 168 testes Node aprovados; backend ocioso reiniciado com autorização anterior, sem alterar as 62 sessões ou os 3.485 CTs históricos.
