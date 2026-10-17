# Painel flutuante da reexecução — 17/10/2026

## Entrega

- O painel do navegador mostra o mesmo roteiro de Passos e evidências, com estados da reexecução atual: pendente, em execução, aguardando o usuário, concluída ou falha.
- Aguardar print aceita Ctrl+V com foco no painel, arrastar/soltar ou escolher PNG, JPEG e WebP de até 10 MB. A imagem pode ser de qualquer tela/aplicativo.
- Escolher uma imagem apenas prepara a prévia. O envio exige o clique em **Anexar print e continuar**.
- O envio usa a mesma transação já existente: checkpoint e versão atuais, posição planejada, identificação de imagem fornecida pelo usuário e preservação do relatório original. Não há nova captura automática nem repetição da ação do CT.
- A preparação/login usa confirmação explícita e valida a versão atual diretamente no serviço, evitando confirmações antigas e perda do sinal de retomada.

## Isolamento

O conteúdo é servido localmente em um iframe de origem separada, sandboxed sem acesso à origem do site testado. A ponte valida identidade da página/frame, URL, nonce privado, fase, checkpoint e versão. O site testado não recebe o token no DOM, em storage ou em mensagens. O frame não tem conexões externas nem persistência. A política CSP do site não é desativada; sites que proíbem frames continuam usando o envio na aplicação AutoJev.

Os controles não entram nas observações ou capturas automáticas do site. As imagens do usuário não são enviadas a modelos.

## Verificação

- Testes unitários do helper, da integração no serviço e da UI: autorização, versões, cancelamento, duplicação, retry, prévia, consentimento, validação de imagens e ciclo de vida.
- Chromium local descartável: seleção de arquivo, paste/drop, clique sintético versus nativo, origem isolada, restrições de rede/storage, reload da página e do próprio iframe, login e upload na posição escolhida, sem repetir a ação ou alterar a execução original.
- Teste visual isolado em 472 × 684 px: sete etapas, prévia e confirmação visíveis juntos; botão termina em y=569 px. Sem necessidade de rolar o painel nesse roteiro.
- Capturas: `data/mcp-validation/rerun-floating-map-empty-2026-10-17.png` e `data/mcp-validation/rerun-floating-map-preview-2026-10-17.png`.
- Build de produção e suíte completa do frontend: 181 testes aprovados. Regressão backend selecionada: 305 aprovados; após o gate final de retomada, 49 testes críticos de serviço/helper/Chromium aprovados, além dos testes adicionais de diagnóstico do painel.

Nenhuma reexecução real do usuário foi iniciada/cancelada e nenhum backend de produção foi reiniciado nesta validação. O novo painel é configurado no início das novas reexecuções, com o backend atualizado.
