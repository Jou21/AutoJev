# Cabeçalho estável entre acompanhamento e visão geral

- As duas telas da mesma reexecução usam o título `Reexecução de CT…`, status, URL, ações e seletor compactos, com o mesmo espaçamento e largura.
- A origem da execução fica no mesmo disclosure recolhido **Sobre esta reexecução**, em ambas as telas. Não há mais um bloco aberto empurrando o seletor ao entrar na visão geral.
- Só o conteúdo abaixo do seletor muda; a visão geral mantém seus resultados, abas e controles. Explorações comuns mantêm o cabeçalho anterior.
- A alteração é apenas de interface. Não altera as autorizações, o processamento do teste ou as evidências e não exige reiniciar o backend.

## Validação

- Seis testes Playwright/Chromium com o frontend real e API totalmente simulada: 1440×900, 1920×1080 e 390×844, para reexecuções concluídas e aguardando print.
- Medidas de posição e tamanho do cabeçalho, seletor e disclosure permanecem iguais ao alternar para a visão geral e retornar, com tolerância de 1 px.
- Resultados e painel de anexo da visão geral continuam em largura completa; nenhum comando de execução, cancelamento ou anexo é enviado ao trocar de tela.
- Capturas revisadas em desktop e celular; sem transbordamento horizontal da página.
- Vinte testes de integração da tela passaram, incluindo paridade do cabeçalho em preparação, pausa para print, conclusão e falha, além da preservação do cabeçalho de explorações comuns. As regressões de prévia e consentimento também passaram.
- Build, TypeScript e `git diff --check` aprovados.
