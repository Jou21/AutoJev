# Última reexecução e histórico do CT

Validação local em 18/10/2026.

## Comportamento

- O relatório de origem mantém um único item por CT e apresenta o último resultado registrado de uma tentativa encerrada, inclusive reprovação ou bloqueio.
- A execução original e as tentativas anteriores ficam no botão **Histórico** do resultado, com datas, resultados e evidências próprias. Tentativas sem resultado ficam registradas, mas não aprovam nem substituem o resultado anterior.
- Reexecuções de reexecuções mantêm a identidade do CT original. O botão principal **Abrir relatório** abre o relatório de origem e seleciona esse CT; o acompanhamento ao vivo continua representando apenas sua própria tentativa.
- Imagens, ações e durações são resolvidas pelo proprietário da evidência, mesmo quando várias execuções usam o mesmo nome de arquivo ou número de ação.
- Os registros persistidos e o HTML original não são sobrescritos. A apresentação atualizada usa um artefato separado, com invalidação por conteúdo das versões.
- Relatórios abertos pela aplicação verificam atualizações ao recuperar foco e a cada dez segundos. Histórico aberto e rascunhos bloqueiam a substituição automática; descartar um rascunho exige confirmação explícita.
- O arquivo baixado contém as evidências históricas e funciona sem conexão. É uma fotografia daquele momento, sem conexão com a aplicação.
- Abas abertas antes desta atualização precisam ser reabertas uma vez pela aplicação para receber a conexão de atualização.

## Verificações

- 110 testes direcionados passaram: projeção, ancestralidade, API, cache, relatório, reexecução e navegador.
- Regressão adicional de relatórios e execução: 515 testes passaram.
- Suíte Node completa passou; TypeScript, build do frontend e `git diff --check` passaram.
- Sete testes reais com Chromium verificaram cliques na linha, seleção de versões, imagens de proprietários diferentes, exportação e reabertura offline, atualização no mesmo Blob/hash, preservação de rascunhos e de histórico aberto durante uma requisição.
- Layout do histórico conferido em 1440×900 e 390×844, sem overflow horizontal.
- A API de consulta foi testada com chamadas ao navegador e ao modelo proibidas; consultar versões não inicia nem reexecuta testes.

O navegador integrado não estava disponível nesta sessão. A validação visual utilizou Chromium isolado com respostas de teste interceptadas, sem acessar sessões de login do usuário.

## Ativação e conferência dos dados existentes

Backend e frontend iniciados com a atualização; ambos responderam HTTP 200. Não havia execução ativa, portanto nenhuma reexecução foi cancelada.

A consulta do relatório de origem existente confirmou 151 CTs, sem duplicação, e 25 versões para CT001, com o proprietário atual igual à última reexecução. O HTML atualizado foi aberto em Chromium isolado e somente leitura: lista, histórico, execução original e imagem preservada funcionaram sem erros de JavaScript (4,2 segundos nesta máquina). Nenhum teste ou login foi iniciado por essa conferência.
