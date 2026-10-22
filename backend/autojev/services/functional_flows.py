"""Explainable, read-only associations between recorded CTs and functional goals.

An identified goal is not an executed/complete journey or requirements coverage.
Route context alone never creates a flow. No models, navigation or persistence.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import OrderedDict
from typing import Any
from urllib.parse import unquote, urlsplit

FLOW_VERSION = "functional-flows-v1"
VERDICTS = ("PASS", "FAIL", "BLOCKED", "SKIPPED")
OPERATIONS = {"create": "Cadastrar", "read": "Consultar", "update": "Editar", "delete": "Excluir"}
PREFIX = re.compile(r"^(?:ct\d+\s*[-:·]?\s*|(?:comportamento funcional|functional behavior|clicar|click|acionar|explorar)\s*:\s*|(?:validar|verificar|testar|validate|verify|test|tentar|attempt to|nao permitir|impedir)\s+(?:(?:o|a|os|as)\s+)?)")
CRUD = {
    "create": re.compile(r"^(?:criar|cadastrar|adicionar|inserir|incluir|cadastro(?: de)?|criacao(?: de)?|novo|nova|create|add|insert|register|new)\s+(.+)$"),
    "read": re.compile(r"^(?:consultar|listar|visualizar|detalhar|conferir|consulta(?: de)?|listagem(?: de)?|read|list|view(?: details)?|search(?: for)?)\s+(.+)$"),
    "update": re.compile(r"^(?:editar|atualizar|alterar|modificar|edicao(?: de)?|atualizacao(?: de)?|edit|update|modify|change)\s+(.+)$"),
    "delete": re.compile(r"^(?:excluir|remover|deletar|apagar|exclusao(?: de)?|remocao(?: de)?|delete|remove)\s+(.+)$"),
}
GENERIC = {"botao", "botoes", "button", "buttons", "campo", "campos", "field", "fields", "elemento", "elementos", "element", "elements", "opcao", "opcoes", "option", "options", "selecao", "selection", "texto", "text", "imagem", "image", "menu", "menus", "pagina", "page", "tela", "screen", "valor", "value", "resultado", "result", "estado", "state", "foco", "focus", "filtro", "filter", "ordenacao", "sorting", "sort", "browser", "erro", "error", "mensagem", "message", "e", "it", "this", "isso"}
GENERIC.update({"item", "itens", "items", "dado", "dados", "data", "informacao", "informacoes", "information", "formulario", "form", "controle", "control", "icone", "icon", "link"})
ARTICLES = {"o", "a", "os", "as", "um", "uma", "de", "do", "da", "dos", "das", "the", "an", "to", "for", "of"}
ALIASES = {"customers": "cliente", "customer": "cliente", "clientes": "cliente", "users": "usuário", "user": "usuário", "usuarios": "usuário", "usuario": "usuário", "products": "produto", "product": "produto", "produtos": "produto", "orders": "pedido", "order": "pedido", "pedidos": "pedido", "payments": "pagamento", "payment": "pagamento", "pagamentos": "pagamento", "invoices": "fatura", "invoice": "fatura", "boletos": "boleto", "suppliers": "fornecedor", "supplier": "fornecedor", "fornecedores": "fornecedor", "accounts": "conta", "account": "conta", "contas": "conta"}
ALIASES.update({"cart": "carrinho", "carts": "carrinho", "carrinhos": "carrinho"})
ROUTE_ACTIONS = {"new": "create", "novo": "create", "nova": "create", "create": "create", "cadastrar": "create", "cadastro": "create", "edit": "update", "editar": "update", "update": "update", "delete": "delete", "excluir": "delete", "details": "read", "detalhes": "read", "view": "read"}
ROUTE_GENERIC = {"app", "api", "admin", "home", "index", "dashboard", "settings", "configuracoes", "login", "signin", "auth", "assets", "static", "public", "www"}


def _fold(value: str) -> str:
    return " ".join("".join(char for char in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(char)).split())


def _objective(value: str) -> str:
    value = _fold(value[:2000]).strip(" \"'“”‘’")
    for _ in range(4):
        next_value = PREFIX.sub("", value, count=1)
        if next_value == value:
            break
        value = next_value
    return value


def _entity(value: str, original: str = "") -> str | None:
    value = re.split(r"\b(?:com|sem|with|without|quando|apos|using|usando|utilizando|para)\b|[:;,.!?]", value, maxsplit=1)[0]
    value = re.sub(r"^(?:dados|informacoes|detalhes) (?:do|da|de) ", "", value)
    words = re.findall(r"[^\W\d_]+", value, flags=re.UNICODE)
    while words and words[0] in ARTICLES:
        words.pop(0)
    if not words or words[0] in GENERIC or len(words[0]) < 3:
        return None
    if words[0] in ALIASES:
        return ALIASES[words[0]]
    # Preserve spelling/accents from the observed label, not raw HTML or IDs.
    pretty_words = re.findall(r"[^\W\d_]+", original, flags=re.UNICODE)
    pretty = next((word.casefold() for word in pretty_words if _fold(word) == words[0]), words[0])
    if len(words) > 1 and words[1] in {"fiscal", "bancaria", "bancario"}:
        pretty += " " + words[1]
    if pretty.endswith("s") and not pretty.endswith(("ss", "is", "us", "status")):
        pretty = pretty[:-1]
    return pretty[:70]


def _route_context(url: Any) -> tuple[str | None, str | None, str]:
    if not isinstance(url, str):
        return None, None, ""
    try:
        path = _fold(unquote(urlsplit(url).path))[:1000]
    except ValueError:
        return None, None, ""
    parts = [re.sub(r"\.(?:html?|php|aspx?)$", "", part) for part in path.split("/") if part]
    operation, entity = None, None
    for part in parts:
        if part in ROUTE_ACTIONS:
            operation = ROUTE_ACTIONS[part]
        elif part not in ROUTE_GENERIC and not re.fullmatch(r"(?:\d+|[a-f0-9-]{12,}|v\d+)", part):
            candidate = _entity(part.replace("_", " ").replace("-", " "))
            if candidate:
                entity = candidate
    return entity, operation, path


def _membership(operation: str, entity: str, confidence: str, reason: str, name: str | None = None) -> dict[str, Any]:
    key = operation + ":" + _fold(entity)
    return {"id": "flow-" + hashlib.sha256(key.encode()).hexdigest()[:16], "name": name or OPERATIONS[operation] + " " + entity,
            "entity": entity, "operation": operation, "confidence": confidence, "reason": reason,
            "description": "Casos relacionados ao objetivo «" + (name or OPERATIONS[operation] + " " + entity) + "»."}


def classify_case_flows(case: dict[str, Any]) -> dict[str, Any]:
    evidence = case.get("evidence") if isinstance(case.get("evidence"), dict) else {}
    assertions = evidence.get("assertions") or []
    kinds = {item["kind"] for item in assertions if isinstance(item, dict) and isinstance(item.get("kind"), str)}
    route_entity, route_operation, path = _route_context(case.get("url"))
    fields = [("título", case.get("title")), ("objetivo", case.get("description"))]
    if isinstance(case.get("steps"), list):
        fields += [("passo declarado", step) for step in case["steps"][:100] if isinstance(step, str)]
    objectives = [(source, _objective(value), value) for source, value in fields if isinstance(value, str)]
    title = _objective(str(case.get("title") or ""))
    memberships: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def add(operation: str, entity: str, source: str, confidence: str = "identified", name: str | None = None):
        item = _membership(operation, entity, confidence, source, name)
        previous = memberships.get(item["id"])
        if not previous or previous["confidence"] == "suggested" and confidence == "identified":
            memberships[item["id"]] = item

    cart_action = None
    for _, objective, _ in objectives:
        if re.match(r"^(?:adicionar|incluir|add).*(?:carrinho|\bcart\b)", objective):
            cart_action = "add"
        if re.match(r"^(?:remover|excluir|remove|delete).*(?:carrinho|\bcart\b)", objective):
            cart_action = "remove"
    if "cart_item_removed" in kinds:
        cart_action = "remove"
    elif kinds & {"cart_count", "cart_membership", "cart_control_state"} and re.match(r"^(?:remover|excluir|remove|delete)\b", title):
        cart_action = "remove"
    if cart_action:
        add(cart_action, "carrinho", "Ação do carrinho explicitada no objetivo ou em uma conferência específica.", name="Adicionar produto ao carrinho" if cart_action == "add" else "Remover produto do carrinho")
    if kinds & {"product_detail_identity", "product_detail_price"}:
        add("read", "produto", "Conferências específicas de identidade/preço do produto em seus detalhes.")
    if "product_sort_order" in kinds:
        add("sort", "catálogo de produtos", "Conferência específica da ordenação preservando os produtos.", name="Ordenar catálogo de produtos")
    if not cart_action and (kinds & {"cart_contents_match", "cart_item_prices", "cart_persistence"} or "cart_count" in kinds and re.search(r"(?:cart|carrinho)", title)):
        add("read", "carrinho", "Conferências específicas sobre o conteúdo, preços ou persistência do carrinho.")
    if kinds & {"checkout_items_match_cart", "checkout_item_prices", "checkout_subtotal", "checkout_total"}:
        add("review", "compra", "Conferências específicas dos itens ou valores na revisão da compra.", name="Revisar compra")
    if "order_confirmation" in kinds:
        add("finish", "compra", "O CT contém uma conferência específica de confirmação do pedido; o resultado individual é preservado.", name="Finalizar compra")

    for source, objective, original in objectives:
        if re.match(r"^(?:login|log in|sign in|autenticar|autenticacao|entrar no sistema|entrar na conta)\b", objective) and not re.search(r"\b(?:campo|field|placeholder)\b", objective):
            add("authenticate", "acesso", "Objetivo de autenticação explícito no " + source + ".", name="Autenticar usuário")
        if re.match(r"^(?:finalizar|concluir|confirmar) (?:a |o )?(?:compra|pedido|pagamento)\b|^(?:place order|complete purchase|finish checkout)\b", objective):
            add("finish", "compra", "Objetivo de finalização da compra explícito no " + source + ".", name="Finalizar compra")
        elif re.match(r"^(?:checkout|iniciar compra|iniciar checkout)\b", objective):
            add("start", "compra", "Objetivo de iniciar a compra explícito no " + source + "; não equivale a finalizar um pedido.", name="Iniciar compra")
        for operation, pattern in CRUD.items():
            match = pattern.match(objective)
            if not match:
                continue
            # Domain-specific assertions disambiguate product instances and cart controls.
            if cart_action and re.search(r"\b(?:cart|carrinho)\b", objective):
                continue
            if operation == "read" and kinds & {"product_detail_identity", "product_detail_price"}:
                continue
            entity = _entity(match[1], original)
            if entity:
                add(operation, entity, "Operação e entidade explícitas no " + source + ".")

    # Weak contextual associations need both an action/check and a relevant form route.
    # A URL on its own, or an unrelated click on that page, is never enough.
    form_checks = kinds & {"field_value_matches", "field_retains_value", "form_required_validation", "form_progress", "form_fields_preserved_on_dismiss"}
    fill_action = any(re.match(r"^(?:preencher|fill|enter|type)\b", objective) for _, objective, _ in objectives)
    if re.search(r"checkout", path) and (form_checks or fill_action):
        add("fill", "dados da compra", "Relação sugerida pelo campo/validação e pelo formulário da compra; não comprova finalização.", "suggested", "Informar dados da compra")
    elif route_entity and route_operation and (form_checks or fill_action) and not memberships:
        add(route_operation, route_entity, "Relação sugerida pela ação de formulário e pela rota; a operação de negócio não foi presumida como concluída.", "suggested")
    if not memberships and route_entity and route_operation and re.match(r"^(?:salvar|save|submit|confirmar|confirm)\b", title):
        add(route_operation, route_entity, "Controle genérico associado a uma rota de operação; falta objetivo funcional explícito.", "suggested")
    return {"version": FLOW_VERSION, "memberships": list(memberships.values()),
            "unclassified_reason": None if memberships else "Os registros não permitem associar este CT a um objetivo funcional com segurança."}


def group_functional_flows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for index, case in enumerate(cases):
        classification = case.get("flow_classification") or {}
        seen = set()
        for membership in classification.get("memberships") or []:
            if membership["id"] in seen:
                continue
            seen.add(membership["id"])
            flow = grouped.setdefault(membership["id"], {key: membership[key] for key in ("id", "name", "entity", "operation", "description")})
            if "case_ids" not in flow:
                flow.update(case_ids=[], case_indices=[], counts=dict.fromkeys(VERDICTS, 0), identified_count=0, suggested_count=0)
            identity = str(case.get("id") or index)
            if identity in flow["case_ids"]:
                continue
            flow["case_ids"].append(identity)
            flow["case_indices"].append(case.get("index", index))
            status = case.get("status") if case.get("status") in VERDICTS else "SKIPPED"
            flow["counts"][status] += 1
            flow["identified_count" if membership["confidence"] == "identified" else "suggested_count"] += 1
    for flow in grouped.values():
        flow["total"] = len(flow["case_ids"])
        flow["confidence"] = "identified" if flow["identified_count"] else "suggested"
        flow["evaluated"] = flow["counts"]["PASS"] + flow["counts"]["FAIL"]
        flow["approval_percent"] = round(flow["counts"]["PASS"] * 100 / flow["evaluated"], 1) if flow["evaluated"] else None
    return sorted(grouped.values(), key=lambda flow: (_fold(flow["name"]), flow["id"]))
