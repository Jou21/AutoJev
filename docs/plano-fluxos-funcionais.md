# Fluxos funcionais no AutoJev

Base: plano anexado pelo usuário em 20/10/2026, substituindo a separação anterior entre CTs principais e complementares.

## Resultado esperado

Apresentar funcionalidades concretas — por exemplo, Cadastrar boleto, Consultar boleto, Editar boleto e Excluir boleto — sem afastar a listagem de CTs. No catálogo da aplicação, um seletor compacto filtra os testes; os resumos dos fluxos ficam em uma seção expansível, conforme o refinamento de 21/10/2026 para telas pequenas. A exportação mantém sua apresentação por cards. Selecionar um fluxo filtra a listagem existente, sem criar páginas isoladas ou perder detalhes, evidências e histórico.

## Regras

- Fluxo é uma organização funcional; CT continua sendo a unidade de resultado preservada. Não fabricar uma camada de cenários formais se ela não existir nos registros.
- Identificação local e determinística a partir de objetivos, ações, verificações e contexto de rota. Palavra isolada ou mesma página não comprovam uma jornada.
- Distinguir associações identificadas, sugeridas e CTs não classificados, expondo o motivo.
- Um CT pode ter relação explícita com mais de um fluxo. Contagens de CTs da tabela são únicas; somar cards não equivale a cobertura.
- Contar PASS, FAIL, BLOCKED e SKIPPED separadamente. Percentual de aprovação usa apenas CTs com resultado PASS/FAIL, com denominador explícito. Não calcular cobertura/completude sem requisitos e relações comprovados.
- Não iniciar navegação, execuções ou chamadas de IA para organizar registros. Não alterar banco, definições, versões ou resultados históricos.

## Entrega

- [x] Ler o plano completo e inspecionar os formatos de CT, evidência, projeção de revisões e exportação.
- [x] Substituir a classificação binária por um classificador de fluxos com identidade estável, entidade/operação, confiança e justificativa.
- [x] Usar a mesma classificação no catálogo e no relatório, inclusive em CTs manuais e reexecuções.
- [x] Cards responsivos com descrição, contagens por status, indício de falhas e seleção acessível.
- [x] Filtro ativo removível, incluindo Todos os testes e Não classificados; composição com busca, status, categoria e página.
- [x] Contadores dos cards respeitam os demais filtros; a seleção de fluxo não esconde os outros cards.
- [x] Preservar seleção em lote, detalhes, evidências, histórico e exportação offline. CSV inclui fluxo/confiança; HTML mantém todos os registros.
- [x] Testar classificação genérica, distinção de entidades, sugestões, consistência de contagens, filtros, navegação e isolamento de resultados.
- [x] Verificar tipos, lint dos componentes, build e testes com limites de memória/tempo; ativar somente se o backend estiver ocioso.

## Limites explícitos

Os metadados atuais permitem associar CTs a objetivos funcionais, não comprovar automaticamente que uma jornada está completa. Relações frágeis são sugestões; dados insuficientes permanecem em Não classificados. Os CTs não são reordenados ou renumerados para montar uma sequência fictícia de execução.

## Verificação e ativação

- Testes de classificação/API/exportação e testes isolados de eventos React/DOM, com fixtures e sem navegador, exploração ou LLM reais. Verificados múltiplos fluxos por CT, baixa confiança, resultado não avaliado, filtros combinados, remoção de filtro, CSV filtrado, HTML completo e preservação da seleção no planejamento.
- Resultado: 104 testes Python focados e 168 testes Node aprovados. A renovação do cache também foi verificada sem o marcador de fluxos; o HTML original e o histórico continuam imutáveis.
- TypeScript, ESLint dos componentes/testes novos e build de produção passaram sob limites de memória e tempo. Não foi feita inspeção visual automatizada no navegador.
- Backend reiniciado somente após confirmar zero execuções ativas e nenhum navegador filho. Saúde e frontend retornam HTTP 200. Mantidos 62 registros de sessões, com os mesmos estados, e 3.485 CTs históricos no banco.
- Consulta somente de leitura ao catálogo do conjunto original de 151 CTs: nove fluxos, 42 CTs não classificados, 150 PASS e um FAIL preservados. “Informar dados da compra” aparece como sugerido, não como compra concluída. Não houve reexecução, chamada de IA ou regeneração de um relatório volumoso para essa conferência.
- Para ver a apresentação nova, atualizar a aplicação e reabrir o relatório por ela. Downloads anteriores permanecem imutáveis.

## Acesso direto às evidências pelo catálogo

- As linhas com resultado registrado abrem uma nova aba diretamente no CT correspondente do relatório consolidado (`#view/results/CT...`), com prints, detalhes e histórico. A identidade utilizada é a do catálogo, não a numeração local de uma reexecução.
- Botão nativo acessível por teclado, área clicável estendida pela linha e destaque de foco/hover. Checkbox, duplicação e edição continuam independentes. CTs sem resultado mostram “Ainda não executado”, sem destino fictício.
- A aba é aberta no clique, antes de carregar o relatório, com acesso à aba original removido. Bloqueio de pop-up ou falha apresenta mensagem na aplicação; abas vazias são fechadas em caso de erro.
- Verificados eventos de abertura, preservação da seleção, edição/duplicação, falhas e destino do CT com dados simulados: 152 testes Node focados, TypeScript, ESLint e build aprovados. Sem gerar relatórios reais, iniciar execuções, chamar modelos ou reiniciar o backend.

### Otimização da abertura individual

- O clique agora pede `case_id=...&preview=true`: filtra o CT antes da projeção de revisões e da renderização, em vez de preparar os 151 CTs para depois mostrar um. O histórico do CT selecionado e os proprietários reais das evidências são preservados.
- Prévia leve com URLs de capturas registradas no backend. Nenhum arquivo de imagem é lido/codificado como base64 para abrir a página; prints são carregados sob demanda. O caminho de visualização não aguarda o bloqueio de uma exportação offline pesada.
- Uma página local escura com estado de carregamento aparece imediatamente no lugar de `about:blank`. Downloads do relatório continuam gerando HTML autocontido; downloads de imagens, zoom e tela cheia aceitam somente capturas listadas e URLs do backend conectado. Um arquivo offline não recebe permissão para buscar URLs remotas arbitrárias.
- Testes isolados cobrem a ausência de leitura de bytes, seleção de CT, identificação do pai, histórico de reexecuções, CTs manuais, exportação por rodada, rejeição de IDs inválidos sem fallback, download offline, erros e validação das URLs. Passaram as regressões focadas de Python/Node, tipos, lint e build.
- Ativação com zero execuções ativas, mantendo as 62 sessões e seus estados. Medição local via API: CT050 em 252 ms / 383.072 bytes, primeiro print em 84 ms; CT001 em 375 ms / 797.855 bytes, com 26 versões e 58 referências de imagem. Esses tempos são da resposta local, não uma garantia de renderização em qualquer dispositivo. Nenhuma exploração, modelo ou relatório completo do conjunto foi disparado para medir.

## Seleção com próximo passo explícito

- Checkboxes do catálogo agora têm uma instrução clara: marcar CTs não executa testes. A primeira marcação revela uma barra persistente com quantidade, até quatro IDs e indicação de selecionados fora dos filtros. A barra adapta o layout em telas estreitas e anuncia alterações de seleção para leitores de tela.
- **Planejar reexecução dos N CTs** abre o planejamento somente com a seleção atual, sem acrescentar automaticamente os demais CTs filtrados. Ordem, pausas de captura, nome e demais dados do rascunho são preservados. A tela recebe foco/rolagem e explica: ajustar roteiros, revisar e só então confirmar a execução.
- **Selecionar todos os N filtrados** é separado de planejar: apenas marca os casos visíveis, respeitando o limite de 500, sem mudar de tela. Com parte já marcada, o botão informa quantos outros CTs podem ser incluídos. **Limpar seleção** desmarca os CTs sem apagar a busca nem salvar/iniciar nada no servidor.
- Verificados com eventos isolados: dois marcados dentre sete levam exatamente dois ao planejamento, contagem singular/plural, filtros que ocultam marcados, seleção em lote, limpeza/desmarcação final, foco e preservação das pausas. Tipos, lint e build aprovados; nenhuma execução real, exportação ou chamada de IA foi iniciada para essa alteração.

## Cabeçalho compacto do conjunto

- Título da aplicação, contagem de CTs e ações ficam na mesma linha em telas maiores. A navegação foi encurtada para **Testes**, **Planejamento** e **Histórico**, com atualização acessível por botão de ícone. Em telas estreitas, as ações se ajustam sem cortar o nome completo disponível no título.
- Removidos do topo o identificador em destaque, a descrição repetida e o aviso permanente. O ícone **Sobre o conjunto** abre os dados de origem, contagens e o esclarecimento de que resultados consolidados podem vir de datas diferentes; isso não comprova uma nova execução dos demais CTs.
- Espaçamentos reduzidos apenas na página do conjunto, mantendo o padrão visual existente. Trocar de aba ou voltar ao planejamento preserva seleção, filtros, ordem, pausas para print, nome da rodada e editor ainda não salvo. Nenhuma ação do cabeçalho salva ou executa testes automaticamente.
- Verificação: 87 testes Node focados aprovados, incluindo contexto recolhido, navegação, foco, rascunho preservado, relatório, atualização e estilos responsivos. TypeScript, ESLint e build de produção aprovados com limites de tempo/memória. Sem inspeção automatizada de navegador, exportação real, LLM ou reinício do backend.

## Fluxos acessíveis em telas pequenas — 21/10/2026

- A grade permanente do catálogo foi substituída por um seletor de fluxos, atalho para **Não classificados**, limpeza do filtro e contagem curta dos CTs exibidos. Reprovações permanecem destacadas mesmo com o resumo fechado; opções sugeridas continuam identificadas como sugestões.
- **Explorar fluxos** inicia recolhido e reúne todos os fluxos em linhas compactas, sem a divisão entre seis cards iniciais e “mais fluxos”. As contagens PASS/FAIL/BLOCKED/SKIPPED e o denominador de aprovação foram preservados. A descrição repetitiva saiu do conteúdo visual e continua acessível no nome/descrição da linha.
- O detalhamento tem rolagem própria, com altura máxima proporcional à área disponível. Campos flexíveis e quebra de linha acomodam telas estreitas e nomes longos. Seletor, limpeza e seção expansível funcionam com teclado e toque, sem depender de hover. Não há preferência de tela nem expansão automática persistida.
- O filtro continua combinado com busca e resultado, sem apagar a seleção de CTs nem alterar a ordem/pausas do planejamento. O modo compacto do planejador, a classificação do backend e a apresentação do relatório exportado não foram substituídos.
- Verificação com 91 testes Node focados, TypeScript, lint e build sob limites de tempo/memória. Os testes cobrem detalhes recolhidos por padrão, 25 fluxos disponíveis sem uma grade inicial, contagens/falhas, sugestões, nomes longos, filtros vazios e planejamento preservado. Estilos responsivos e limite de altura foram conferidos por contrato de código; não houve teste visual automatizado de navegador, execução real ou chamada de modelo.
