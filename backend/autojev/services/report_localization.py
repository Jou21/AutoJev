"""Portuguese presentation of AutoJev's owned narration, without model calls.

Only complete, known templates are translated. Application labels, URLs,
selectors, input/output values and raw browser diagnostics are not prose to
translate. The projection never rewrites persisted evidence or internal enums.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


REPORT_LOCALE_VERSION = "pt-BR-v1"

ACTION_LABELS = {
    "click": "Clicar", "fill": "Preencher", "select": "Selecionar opção",
    "navigate": "Navegar", "go_back": "Voltar", "reload": "Recarregar",
    "press": "Pressionar tecla", "hover": "Posicionar o cursor",
    "scroll": "Rolar a página", "wait": "Aguardar", "screenshot": "Capturar a tela",
    "finish": "Finalizar",
}

QA_FOCUS_LABELS = {
    "functional_behavior": "Comportamento funcional",
    "navigation_information_architecture": "Navegação e arquitetura da informação",
    "validation_error_handling": "Validação e tratamento de erros",
    "state_consistency": "Consistência de estado",
    "access_permissions": "Acesso e permissões",
    "resilience_observability": "Resiliência e observabilidade",
}

CHECK_LABELS = {
    "url_contains": "URL contém o trecho esperado", "url_equals": "URL esperada",
    "text_contains": "Texto esperado na página", "title_contains": "Título esperado",
    "element_visible": "Elemento visível", "observable_state_change": "Mudança observável na página",
    "no_new_browser_errors": "Ausência de novas falhas técnicas da aplicação",
    "no_new_application_errors": "Ausência de novas falhas técnicas da aplicação",
    "field_value_matches": "Retenção do valor preenchido",
    "field_retains_value": "Retenção do valor preenchido",
    "other_fields_unchanged": "Preservação dos demais campos",
    "selection_matches": "Opção selecionada", "product_sort_order": "Ordenação dos produtos",
    "product_detail_identity": "Identidade do produto nos detalhes",
    "product_detail_price": "Preço do produto nos detalhes",
    "product_image_loaded": "Carregamento da imagem do produto",
    "cart_count": "Quantidade de itens no carrinho", "cart_control_state": "Estado do controle do carrinho",
    "cart_membership": "Presença do produto no carrinho", "cart_item_removed": "Remoção do produto do carrinho",
    "cart_contents_match": "Conteúdo do carrinho", "cart_persistence": "Persistência do carrinho",
    "cart_item_prices": "Preços dos produtos no carrinho", "cart_reset": "Limpeza do carrinho",
    "checkout_items_match_cart": "Produtos do carrinho na revisão da compra",
    "checkout_item_prices": "Preços dos produtos na revisão da compra",
    "checkout_subtotal": "Subtotal da compra", "checkout_total": "Total da compra",
    "checkout_cancel_cart_count": "Quantidade de itens após cancelar a compra",
    "checkout_cancel_selected_products": "Seleção de produtos após cancelar a compra",
    "checkout_cancel_progress": "Cancelamento da revisão da compra",
    "checkout_form_cancel_progress": "Cancelamento do formulário de compra",
    "order_confirmation": "Confirmação do pedido",
    "form_required_validation": "Validação de campo obrigatório",
    "form_validation_probe_precondition": "Pré-condições da validação de campo obrigatório",
    "form_progress": "Avanço do formulário",
    "form_validation_feedback_dismissed": "Fechamento da mensagem de validação",
    "form_fields_preserved_on_dismiss": "Preservação dos campos ao fechar a validação",
    "visual_model": "Indício visual da LLM",
}

# Complete messages, not word substitutions: a control named "Remove" or a
# diagnostic containing "select" must retain its original spelling.
TEXT = {
    "Observed": "Condição observada",
    "An application technical check failed during this action; a business defect is not automatically confirmed":
        "Uma verificação técnica da aplicação falhou durante esta ação; um defeito de negócio não é confirmado automaticamente",
    "0 attributable application runtime, HTTP or network failures": "Nenhuma falha de execução, HTTP ou rede atribuível à aplicação",
    "Expected evidence was not observed": "A evidência esperada não foi observada",
    "Expected evidence is observed": "A evidência esperada deve ser observada",
    "The declared assertion should be observed.": "A condição declarada deve ser observada.",
    "No rationale supplied": "Justificativa não informada",
    "The displayed items and quantities must match the cart established by prior product actions.": "Os itens e as quantidades exibidos devem corresponder ao carrinho formado pelas ações anteriores nos produtos.",
    "Advertised unit prices must be preserved for the same product in the cart/review.": "Os preços unitários anunciados devem ser preservados para o mesmo produto no carrinho e na revisão da compra.",
    "The previously requested cart action must be reflected when the cart is opened.": "A ação solicitada anteriormente deve estar refletida no conteúdo ao abrir o carrinho.",
    "Cancelling checkout must preserve the observed cart count.": "Cancelar a compra deve preservar a quantidade de itens observada no carrinho.",
    "The catalog's observed product selection must be retained after Cancel; this is not an opened-cart contents check.": "A seleção de produtos observada no catálogo deve ser preservada após cancelar; esta verificação não equivale à conferência do conteúdo com o carrinho aberto.",
    "Cancel must leave the review without placing the order; a dispatched click alone is not approval.": "Cancelar deve sair da revisão sem concluir o pedido; apenas executar o clique não significa aprovação.",
    "Cancel must leave the observed checkout information form without confirming an order.": "Cancelar deve sair do formulário de informações da compra sem confirmar um pedido.",
    "The declared item subtotal must equal the sum of observed unit prices multiplied by quantities.": "O subtotal informado deve corresponder à soma dos preços unitários observados multiplicados pelas quantidades.",
    "The declared total must equal subtotal + displayed tax + shipping - discount; no tax-rate correctness is inferred.": "O total informado deve corresponder ao subtotal mais os impostos e o frete exibidos, menos o desconto; esta verificação não confirma se a alíquota está correta.",
    "The finish action must expose an explicit successful order confirmation, not merely a changed URL.": "Finalizar deve exibir uma confirmação explícita de sucesso do pedido, não apenas mudar a URL.",
    "The filled field must retain the requested value.": "O campo preenchido deve manter o valor solicitado.",
    "The field must retain its supplied value.": "O campo deve manter o valor fornecido.",
    "Filling one field must preserve the values of other fields in the same form.": "Preencher um campo deve preservar os valores dos demais campos do mesmo formulário.",
    "The selected option must match the requested value.": "A opção selecionada deve corresponder ao valor solicitado.",
    "The visible products must preserve membership and follow the declared sort order.": "A lista deve preservar os mesmos produtos e seguir a ordenação indicada.",
    "The opened product detail must belong to the selected product.": "Os detalhes abertos devem pertencer ao produto selecionado.",
    "The selected product must preserve its displayed price in the detail.": "O produto selecionado deve manter o preço exibido na página de detalhes.",
    "The displayed product image must finish loading successfully.": "A imagem exibida do produto deve terminar de carregar com sucesso.",
    "The cart count must reflect the selected product action.": "A quantidade de itens no carrinho deve refletir a ação realizada no produto selecionado.",
    "The selected product control must reflect its cart membership.": "O controle do produto selecionado deve indicar corretamente sua presença ou ausência no carrinho.",
    "The selected product must have the expected cart membership.": "A presença ou ausência do produto selecionado no carrinho deve corresponder ao esperado.",
    "A required-field probe needs an empty target and populated peers, or feedback attributable to that specific field.": "A validação exige o campo-alvo vazio e os demais campos preenchidos, ou uma mensagem atribuível especificamente ao campo-alvo.",
    "The application must reject this empty required field with attributable validation, without advancing the form.": "A aplicação deve rejeitar o campo obrigatório vazio com uma validação atribuível a ele, sem avançar o formulário.",
    "Submitting missing required fields must retain the form and expose validation.": "Enviar o formulário com campos obrigatórios vazios deve manter o formulário aberto e exibir a validação.",
    "A completed required step must advance when its continue control is used.": "Uma etapa com os campos obrigatórios preenchidos deve avançar ao acionar o controle de continuação.",
    "Dismissing inline validation feedback must clear the observed error without leaving the form.": "Fechar a mensagem de validação deve remover o erro observado sem sair do formulário.",
    "Closing a validation message must not clear or overwrite the form's input values.": "Fechar uma mensagem de validação não deve apagar nem sobrescrever os valores dos campos do formulário.",
    "An explicitly requested cart reset must expose an empty cart; its new lifecycle replaces prior membership expectations.": "A limpeza explícita do carrinho deve deixá-lo vazio; o novo estado substitui as expectativas anteriores sobre seus produtos.",
    "The selected control responds consistently, remains inside the allowed scope, and introduces no attributable application failure. External browser signals are recorded separately from functional checks.": "O controle selecionado deve responder de forma consistente, permanecer no escopo permitido e não introduzir falhas atribuíveis à aplicação. Sinais externos do navegador são registrados separadamente das verificações funcionais.",
    "The control responds consistently and introduces no attributable application failure. External browser signals are recorded separately from functional checks.": "O controle deve responder de forma consistente e não introduzir falhas atribuíveis à aplicação. Sinais externos do navegador são registrados separadamente das verificações funcionais.",
    "Reachable coverage exhausted; blocked controls were documented as restrictions": "A cobertura alcançável foi esgotada; os controles bloqueados foram documentados como restrições.",
    "Exploration planning limit reached before the coverage frontier was exhausted": "O limite de planejamento foi atingido antes de esgotar as opções de cobertura.",
    "Maximum exploration duration reached; coverage is partial": "A duração máxima da exploração foi atingida; a cobertura é parcial.",
    "Maximum number of actions reached; coverage is partial": "O número máximo de ações foi atingido; a cobertura é parcial.",
    "The requested screen/menu scope was not found among reachable safe routes": "A tela ou o menu solicitado não foi encontrado nas rotas seguras alcançáveis.",
    "JEV kept repeating tried actions; coverage is partial": "O JEV continuou repetindo ações já testadas; a cobertura é parcial.",
    "Backend shutdown interrupted this exploration": "O encerramento do servidor interrompeu esta exploração.",
    "Cancellation requested": "Cancelamento solicitado.",
    "All safe candidates inside the selected scope are exhausted.": "Todas as opções seguras do escopo selecionado foram esgotadas.",
    "Dismiss the observed harmless overlay before testing obstructed background controls.": "Fechar a sobreposição observada antes de testar os controles encobertos.",
    "Verify the observed inline validation dismissal before a form submission removes its transient control.": "Verificar o fechamento da mensagem de validação antes que o envio do formulário remova esse controle temporário.",
    "Prepare another required form field before a bounded negative validation probe.": "Preparar outro campo obrigatório antes de realizar uma tentativa limitada de validação negativa.",
    "Clear this required field for a bounded negative test without bypassing the application's input behavior.": "Limpar este campo obrigatório para um teste negativo limitado, respeitando o comportamento de entrada da aplicação.",
    "Submit this form once and verify feedback attributable to the empty target; retain any masking peer failure as a dependency restriction.": "Enviar o formulário uma vez e verificar a resposta atribuível ao campo vazio; registrar como restrição qualquer falha de outro campo que impeça essa validação.",
    "Submit this conventional form with only one required field empty and verify attributable rejection.": "Enviar este formulário com apenas um campo obrigatório vazio e verificar se a rejeição é atribuível a esse campo.",
    "Verify checkout cancellation preserves the actual basket before order confirmation consumes its prerequisites.": "Verificar se cancelar a compra preserva o carrinho antes que a confirmação do pedido altere suas pré-condições.",
    "Verify this route's actual untried menu opener before departing the live surface; keep revealed submenu controls as separate work.": "Verificar a abertura do menu ainda não testada nesta página antes de sair; testar separadamente os controles do submenu revelado.",
    "Verify checkout form cancellation after its required-field negatives, then restore the observed form to test its pending positive submission.": "Verificar o cancelamento do formulário de compra após os testes negativos de campos obrigatórios; depois retomar o formulário para testar o envio com os dados preenchidos.",
    "Verify the observed checkout-form cart link before the successful submission consumes this lane; resume its still-untried submission through observed navigation.": "Verificar o acesso ao carrinho pelo formulário de compra antes do envio com sucesso; depois retomar pela navegação observada para testar o envio pendente.",
    "Verify one populated-cart purchase lane before reversible removals or resets erase its business prerequisites.": "Verificar um fluxo de compra com itens no carrinho antes que remoções ou limpezas alterem suas pré-condições de negócio.",
    "Restore a known DOM-only frontier by replaying a safe semantic transition with a freshly resolved element reference.": "Retomar um estado conhecido do DOM reproduzindo uma transição segura com a referência atualizada do elemento.",
    "Recreate the observed synthetic form prerequisites before replaying its safe submit.": "Recompor as pré-condições observadas do formulário com dados de teste antes de repetir seu envio seguro.",
    "Reach a pending route using a fresh safe navigation control; verify its actual live state before resuming uncovered work.": "Acessar uma rota pendente usando um controle de navegação seguro e atualizado; conferir o estado real antes de retomar os testes pendentes.",
    "Close the observed shared drawer to expose this route's still-untested opener; verify the fresh state before resuming coverage.": "Fechar o menu lateral para revelar o controle de abertura ainda não testado nesta rota; conferir o estado atualizado antes de retomar a cobertura.",
    "Restore the nearest known route ancestor before replaying its selector-free semantic transition recipe.": "Retomar a rota anterior conhecida mais próxima antes de reproduzir sua sequência de transições sem depender de seletores antigos.",
    "Coverage of this state is exhausted; return to the parent state and resume its untried alternatives.": "A cobertura deste estado foi esgotada; voltar ao estado anterior e retomar as opções ainda não testadas.",
    "Browser history did not restore the parent state; return to its known same-origin URL.": "O histórico do navegador não recuperou o estado anterior; voltar à URL conhecida do mesmo domínio.",
    "Return immediately to the requested focused exploration root.": "Voltar ao ponto inicial do recorte de exploração solicitado.",
    "Use a safe observed interaction instead of a known unsupported deep-link GET.": "Usar uma interação segura observada, pois a navegação direta conhecida não é suportada.",
    "The requested deep-link GET is unsupported; all remaining blocked controls are explicit restrictions.": "A navegação direta solicitada não é suportada; os controles restantes bloqueados estão registrados como restrições.",
    "The agent requested finish while this state still has a safe, untried interactive element.": "O agente solicitou o encerramento, mas ainda existe um controle seguro não testado neste estado.",
    "Finish the current state's safe, untried controls before leaving it.": "Testar os controles seguros pendentes deste estado antes de sair.",
    "The proposed element was already attempted or unavailable; use the next safe semantic candidate.": "O elemento proposto já foi testado ou está indisponível; usar a próxima opção segura.",
    "Finish the remaining controls in this dialog or screen before using its exit control.": "Testar os controles restantes deste diálogo ou desta tela antes de acionar a saída.",
    "The logical parent is another DOM state on this same route; reload its known same-origin URL instead of crossing unrelated browser history.": "O estado anterior é outra configuração do DOM nesta mesma rota; recarregar sua URL conhecida do mesmo domínio, sem atravessar entradas do histórico sem relação.",
    "The reachable coverage frontier is exhausted; do not cross the application or authenticated-session boundary.": "As opções de cobertura alcançáveis foram esgotadas; não ultrapassar os limites da aplicação ou da sessão autenticada.",
    "The proposed action/value was not an available atomic move; use the next safe variant for this control.": "A ação ou o valor proposto não era uma opção disponível; usar a próxima alternativa segura para este controle.",
    "Complete the current form's safe fields and submit before leaving through header or navigation controls.": "Preencher os campos seguros do formulário atual e enviá-lo antes de sair pelos controles do cabeçalho ou da navegação.",
    "A passive action was already used in this state; continue with a new interactive candidate.": "Uma ação passiva já foi usada neste estado; continuar com outro controle interativo.",
}


def action_label(action: Any) -> str:
    return ACTION_LABELS.get(str(action), str(action or "Ação"))


def check_label(kind: Any) -> str:
    return CHECK_LABELS.get(str(kind), str(kind or "Verificação observada"))


def _line(text: str) -> str:
    if text in TEXT:
        return TEXT[text]
    # Documentation combines complete messages under these owned prefixes.
    for prefix in ("Confirmada: ", "Não confirmada: ", "Verificar: "):
        if text.startswith(prefix):
            return prefix + _line(text[len(prefix):])
    match = re.fullmatch(r"(TypeSafe JEV|Laya|AutoJev deterministic planner) selected (\w+) for (.+) to maximize (safe untested|unsafe and untested) coverage \((.+)\)\.", text)
    if match and match[2] in ACTION_LABELS:
        decider, action, control, coverage_kind, signal_text = match.groups()
        decider = {"TypeSafe JEV": "O TypeSafe JEV", "Laya": "O Laya",
                   "AutoJev deterministic planner": "O planejador determinístico do AutoJev"}[decider]
        signals = []
        for part in signal_text.split("; "):
            key, _, value = part.partition(": ")
            label = {"QA focus": "foco de qualidade", "move probability": "probabilidade da ação",
                     "choice confidence": "confiança na escolha", "observed-issue probability": "probabilidade de problema observado"}.get(key)
            if label is None:
                return text
            if key == "QA focus":
                value = QA_FOCUS_LABELS.get(value.replace(" ", "_"), value)
            signals.append(f"{label}: {value}")
        coverage = "segura ainda não testada" if coverage_kind == "safe untested" else "insegura e ainda não testada"
        return f"{decider} escolheu a ação {action_label(action).lower()} no controle «{control}» para ampliar a cobertura {coverage} ({'; '.join(signals)})."
    match = re.fullmatch(r"AutoJev's deterministic invocation policy selected the next safe, untried (\w+) without a JEV call \((\w+)\)\.", text)
    if match and match[1] in ACTION_LABELS:
        reason = {"conventional_form": "formulário convencional", "known_navigation": "navegação conhecida",
                  "single_clear_control": "controle único identificado", "clear_safe_frontier": "opções seguras identificadas"}.get(match[2], match[2])
        return f"A política local do AutoJev selecionou a próxima ação segura ainda não testada: {action_label(match[1]).lower()} ({reason})."
    match = re.fullmatch(r"Playwright executed (\w+) successfully(.*)", text)
    if match and match[1] in ACTION_LABELS:
        suffix = match[2]
        if suffix == "; no new route or interactive-state change was observed":
            suffix = "; não foi observada uma nova rota nem mudança no estado dos controles"
        return f"O Playwright executou a ação {action_label(match[1]).lower()} com sucesso{suffix}"
    for english, portuguese in (("The browser is on ", "O navegador está em "), ("Browser ended at ", "O navegador terminou em ")):
        if text.startswith(english):
            return portuguese + text[len(english):]
    if text.startswith("Exploration scope: "):
        scope = text[len("Exploration scope: "):]
        return "Escopo da exploração: " + {"entire application": "aplicação inteira", "entire same-origin application": "aplicação inteira no mesmo domínio"}.get(scope, scope)
    match = re.fullmatch(r"Execute (\w+) on (.+)", text)
    if match and match[1] in ACTION_LABELS:
        return f"{action_label(match[1])} em " + ("a página observada" if match[2] == "the observed page" else match[2])
    match = re.fullmatch(r"Execute (\w+)", text)
    if match and match[1] in ACTION_LABELS:
        return action_label(match[1])
    return text


def report_text(value: Any) -> str:
    """Translate controlled narration only, preserving unknown/raw text verbatim."""
    text = str(value or "")
    return "\n".join(_line(line) for line in text.split("\n"))


def report_title(value: Any) -> str:
    text = str(value or "")
    if text.lower().replace(" ", "_") in ACTION_LABELS:
        return action_label(text.lower().replace(" ", "_"))
    match = re.fullmatch(r"Exploratory scenario (\d+)", text)
    if match:
        return f"Cenário exploratório {match[1]}"
    for key, label in QA_FOCUS_LABELS.items():
        prefix = key.replace("_", " ").title() + ": "
        if text.startswith(prefix):
            return label + ": " + text[len(prefix):]
    match = re.fullmatch(r"Explore (\w+) on (.+)", text)
    if match and match[1] in ACTION_LABELS:
        return f"Explorar: {action_label(match[1]).lower()} em «{match[2]}»"
    for prefix in ("Executar ", "Executar ação: ", "Ação registrada: ", "Ação: "):
        if text.startswith(prefix) and text[len(prefix):] in ACTION_LABELS:
            return action_label(text[len(prefix):])
    match = re.fullmatch(r"([A-Za-z_ ]+): (.+)", text)
    if match and match[1].lower().replace(" ", "_") in ACTION_LABELS:
        return action_label(match[1].lower().replace(" ", "_")) + ": " + match[2]
    return CHECK_LABELS.get(text, text)


def _localize_assertion(assertion: dict[str, Any]) -> None:
    assertion["message"] = report_text(assertion.get("message"))
    # The machine-readable expectation remains evidence. A separate display
    # field localizes only our own known prose, never an observed page value.
    expected = assertion.get("expected")
    if isinstance(expected, str) and expected in TEXT:
        assertion["expected_display"] = report_text(expected)


def localize_report_presentation(exploration: dict[str, Any]) -> dict[str, Any]:
    """Localize a detached presentation, including already-versioned timelines.

    Do not recursively translate dictionaries: their technical keys and actual
    observed values are evidence, not interface text. Actions, signals, usage,
    verdicts, visual grounding, timestamps and original artifacts stay intact.
    """
    data = dict(exploration)
    for key in ("scenarios", "scenario_contexts"):
        if key not in data:
            continue
        records = deepcopy(data[key])
        for scenario in records:
            scenario["title"] = report_title(scenario.get("title"))
            for field in ("description", "expected_result", "actual_result"):
                if field in scenario:
                    scenario[field] = report_text(scenario[field])
            for field in ("steps", "preconditions"):
                if field in scenario:
                    scenario[field] = [report_text(item) for item in scenario[field]]
            evidence = scenario.get("evidence") or {}
            for entry in evidence.get("timeline") or []:
                # Signal messages and captured page labels remain original.
                if entry.get("kind") in {"action", "assertion"}:
                    entry["title"] = report_title(entry.get("title"))
                    entry["message"] = report_text(entry.get("message"))
                elif entry.get("title") in {"Depois da ação", "Estado após a ação"}:
                    entry["message"] = report_text(entry.get("message"))
                if isinstance(entry.get("assertion"), dict):
                    _localize_assertion(entry["assertion"])
            for assertion in evidence.get("assertions") or []:
                _localize_assertion(assertion)
        data[key] = records
    data["report_locale"] = REPORT_LOCALE_VERSION
    return data
