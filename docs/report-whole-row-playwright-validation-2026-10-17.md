# Clique na linha — verificação pelo fluxo real

## Reproduzido em 17/10/2026

Playwright abriu a aplicação em `http://localhost:3000/#/exploracoes/43155ed3-f3b7-4dab-b24e-d3d08b33e747`, acionou o botão **Abrir relatório**, aguardou a nova aba `blob:http://localhost:3000/...` e entrou em **Testes**. Não foi usado `setContent` nessa verificação.

Esse é o relatório de 151 CTs, com o mesmo título do CT001 mostrado pelo usuário. A tabela teve 151 linhas e uma única instância de `#case-rows`. O runtime aplicou o comportamento de linha inteira e o cursor pointer. Nenhum erro JavaScript foi observado no popup.

- Cliques nativos no espaço de padding de cada uma das oito colunas da primeira linha abriram `#view/results/CT001`.
- Cliques nativos na coluna Tempo da ação das linhas CT001, CT002, CT003, CT008 e CT015 abriram os respectivos resultados.
- A seleção de uma imagem, reexecução ou qualquer ação do site testado não fez parte desta validação.

O problema relatado não ocorreu no popup recém-aberto dessa sessão isolada. A aba já aberta no navegador do usuário não é acessível por esta conexão; portanto não foi possível comprovar ali a causa da divergência. Uma repetição monitorada retornou HTTP 200 sem disk cache nessa sessão, não uma evidência de cache na sessão do usuário.

## Atualização de carregamento

A abertura da aplicação agora usa `fetch(..., { cache: "no-store" })`. O endpoint responde com `Cache-Control: private, no-store` e `Pragma: no-cache`. Isso impede reutilizar uma cópia HTTP antiga ao abrir um novo Blob; um Blob já aberto permanece um documento independente.

O backend foi atualizado após verificar as 51 execuções: nenhuma estava ativa nem tinha revisão pendente. Nenhuma execução foi cancelada. A abertura real posterior confirmou os novos cabeçalhos e repetiu os cliques com sucesso.

Regressões novas executam o handler real de Abrir relatório duas vezes e verificam HTML novo, Blob e opção no-store; testes do endpoint verificam os cabeçalhos e preservação do original e dos registros.

Capturas da sessão Playwright:

- `data/mcp-validation/whole-row-real-blob-151-tests-2026-10-17.png`
- `data/mcp-validation/whole-row-real-blob-CT002-opened-2026-10-17.png`
