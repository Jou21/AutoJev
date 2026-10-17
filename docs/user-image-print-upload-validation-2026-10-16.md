# Aguardar print — imagem escolhida pelo usuário

## Mudança de significado

“Aguardar print” agora aguarda uma imagem enviada pelo usuário, de qualquer tela, aplicativo ou conteúdo. Não chama a captura automática do navegador. O painel fica no topo da reexecução na aplicação AutoJev e permite:

- Colar uma imagem com Ctrl+V, sem leitura automática da área de transferência ou solicitação de permissão.
- Arrastar e soltar um arquivo na área destacada.
- Escolher um arquivo, conferir a prévia, trocar ou remover.
- Confirmar **Anexar print e continuar**. Colar, soltar ou selecionar não envia a imagem nem avança o teste.

Uma imagem PNG, JPEG ou WebP por pausa, até 10 MiB. O servidor verifica o conteúdo real, limita 30 megapixels e 16.384 pixels por lado e rejeita arquivos inválidos, SVG, animações e MIME incompatível. A imagem é preservada byte a byte; o anexo guarda SHA-256, dimensões, tamanho e horário de anexação, não um horário inventado de captura da página.

## Segurança e evidência

O POST existente `/api/explorations/{id}/capture-print` exige `confirm: true`, o identificador e a versão atuais do checkpoint, mais `image_data_url`. A validação de imagem ocorre em uma thread; a aceitação/consumo de fase ocorre no event loop. Arquivos recebem nomes exclusivos gerados pelo servidor, sem caminho ou nome fornecido pelo usuário. O payload da imagem não é copiado aos eventos ou retornado em erros.

Entradas inválidas e falhas ao gravar mantêm a pausa, permitindo outra tentativa. Cliques duplicados, antigos ou de origem não autorizada não avançam o checkpoint. O navegador testado continua com orientação passiva; não possui binding para confirmar o anexo. O botão comum de continuar não libera essa pausa.

O anexo é identificado como **Imagem anexada pelo usuário**, `image_source=user_upload`, sem atribuir a ele a URL da página testada. Ele aparece na linha do tempo, galeria e HTML exportado, mas não vira aprovação/reprovação nem substitui a última captura real da navegação. A ação original e suas conferências permanecem inalteradas, executadas uma só vez. Não há chamadas a modelos, SWS ou SQL para anexar imagens.

Uma migração aditiva inclui `screenshots.image_source`. Registros antigos conservam a classificação de captura do navegador. Cópia de segurança do banco antes da migração: `data/autojev.pre-user-image-20261016-cc57cbe3.sqlite3`.

## Validação

- 217 testes backend relacionados aprovados: repository, reexecução, API, Chromium local e relatório. Incluem formatos, integridade dos bytes, limites, falhas de armazenamento, múltiplos checkpoints, cancelamento simultâneo ao envio, proveniência, ação única e relatório original intacto.
- 136 testes de interface/relatório aprovados. Build de produção e TypeScript aprovados.
- Interface conferida com uma execução simulada e imagem de exemplo: evento de colagem com imagem, drag-and-drop e seleção de arquivo geram prévia sem POST; confirmação envia apenas consentimento/checkpoint/imagem; erro simulado mantém a imagem para nova tentativa; remover ou escolher arquivo inválido desabilita continuar.
- Imagem WebP incorporada ao HTML com MIME correto, origem didática e sem afirmar que ela mostra a página do CT.
- A reexecução `7810781a-fa41-44e9-8e31-f0ec20c6514c` foi cancelada somente após autorização explícita do usuário. A ação e o CT já salvos foram preservados. O backend foi reiniciado após confirmar ausência de execuções ativas.

Não foram iniciados testes no SauceDemo ou chamadas a provedores nesta validação. Para usar a atualização, atualize a aplicação e reexecute apenas o CT desejado com a pausa de print.
