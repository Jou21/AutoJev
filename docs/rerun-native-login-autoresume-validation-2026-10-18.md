# Retomada automática após login feito no próprio site

## Comportamento

- Durante uma preparação que identificou a tela de login, o executor observa a página a cada 750 ms, sem capturar imagens nem enviar o formulário.
- Com a autorização compartilhada marcada, sair do login e chegar à página e ao estado originais permite retomar automaticamente, sem outro clique em **Continuar reexecução**.
- Antes da ação, a observação passiva é encerrada e uma nova observação valida novamente a origem, a URL, os dados iniciais e o controle original. Se o login reaparecer nesse intervalo, o app volta a aguardar; não tenta reenviar as credenciais automaticamente.
- Sem autorização, o app reconhece o login e informa **Login realizado. Autorize para continuar**. Desmarcar a autorização impede a retomada.
- O botão de continuação continua disponível para quem prefere que o AutoJev tente entrar usando os campos preenchidos. Marcar a autorização enquanto ainda está no login não envia esse formulário.
- As pausas para anexar prints continuam independentes e exigem a imagem e a confirmação do usuário. O registro inicial é concluído antes de uma pausa posicionada depois dele.
- A leitura passiva não consome os erros técnicos que serão usados pela observação normal, não cria evidências e não acessa LLMs.

## Verificação

- 309 testes backend direcionados passaram, incluindo 15 casos do observador de login: login com redirecionamento e SPA sem mudar URL, ausência/revogação de autorização, origem/URL/estado/controle divergentes, erro de login, correção posterior, confirmação concorrente, cancelamento, fechamento e preservação das pausas de print.
- Dois testes Chromium integram o serviço real, o botão Login de uma página descartável e o painel isolado. Ambos retomam sem clicar em Continuar; um autoriza antes do login e outro depois. A ação original ocorre uma única vez, após o anexo confirmado, e a execução de origem fica intacta.
- Dois testes Chromium adicionais conferem a leitura passiva sem captura, submissão ou consumo de sinais técnicos, com nova vinculação do controle após o login.
- Dois testes com a aplicação React e API isolada confirmam as duas telas: o primeiro card concluído antes do print e a prévia compartilhada sem anexo automático.
- Mais 176 regressões do executor, login, painel e sincronização de prints passaram. A revisão independente também confirmou 17 testes do observador, incluindo dois novos casos de prazo absoluto e limpeza após erros transitórios de navegação/leitura.
- 106 testes de interface passaram; os textos canônicos e de fallback são comparados entre Python, React e painel, incluindo login reconhecido.
- Build, TypeScript, ESLint direcionado e `git diff --check` passaram.

Todos os testes usam páginas e bancos temporários. Nenhum CT do usuário foi iniciado por esta validação. A alteração foi ativada somente no aplicativo local; não houve publicação externa. Não havia execução ativa no momento da ativação.
