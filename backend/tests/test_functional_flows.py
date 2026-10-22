"""Deterministic functional associations, never fabricated executions/coverage."""
from copy import deepcopy

import pytest

from autojev.services.functional_flows import FLOW_VERSION, classify_case_flows, group_functional_flows


def case(title, *, checks=(), url="https://fixture.invalid/", status="PASS", identity="CT001", **extra):
    item = {"id": identity, "title": title, "url": url, "status": status,
            "evidence": {"assertions": [{"kind": kind, "passed": status == "PASS"} for kind in checks]}, **extra}
    item["flow_classification"] = classify_case_flows(item)
    return item


def members(item):
    return item["flow_classification"]["memberships"]


@pytest.mark.parametrize("title,name", [
    ("Cadastrar boleto com dados válidos", "Cadastrar boleto"),
    ("CT009 · Verificar os boletos", None),
    ("CT002: Testar Criar clientes sem nome", "Cadastrar cliente"),
    ("Consultar boletos", "Consultar boleto"),
    ("Editar usuário", "Editar usuário"),
    ("Não permitir excluir boleto sem confirmação", "Excluir boleto"),
    ("Create customers with valid data", "Cadastrar cliente"),
    ("Update invoices", "Editar fatura"),
    ("Excluir nota fiscal", "Excluir nota fiscal"),
    ("Adicionar campo", None), ("Alterar opção", None),
])
def test_explicit_operation_and_entity_not_generic_controls(title, name):
    value = case(title)
    assert value["flow_classification"]["version"] == FLOW_VERSION
    assert [member["name"] for member in members(value)] == ([name] if name else [])
    if name:
        assert members(value)[0]["confidence"] == "identified"
        assert members(value)[0]["reason"]


def test_entity_and_operation_identity_stable_without_claiming_complete_crud():
    items = [case("Cadastrar boleto"), case("Criar boletos sem vencimento", identity="CT002"),
             case("Cadastrar cliente", identity="CT003"), case("Consultar boleto", identity="CT004")]
    assert members(items[0])[0]["id"] == members(items[1])[0]["id"]
    assert members(items[0])[0]["id"] != members(items[2])[0]["id"]
    assert members(items[0])[0]["id"] != members(items[3])[0]["id"]
    flows = group_functional_flows(items)
    assert flows == group_functional_flows(items)
    assert len(flows) == 3
    assert {flow["id"] for flow in flows} == {flow["id"] for flow in group_functional_flows(list(reversed(items)))}
    assert not any("coverage" in flow or "complete" in flow for flow in flows)


def test_routes_need_relevant_actions_and_weak_associations_are_suggested():
    route = "https://fixture.invalid/boletos/novo?token=secret"
    for title in ["Clicar no menu", "Observar página", "Alterar campo"]:
        assert members(case(title, url=route)) == []
    suggested = case("Preencher campo nome", checks=["field_retains_value"], url=route)
    explicit = case("Cadastrar boleto", url=route)
    assert members(suggested)[0]["name"] == "Cadastrar boleto"
    assert members(suggested)[0]["confidence"] == "suggested"
    assert members(suggested)[0]["id"] == members(explicit)[0]["id"]
    assert "secret" not in str(members(suggested))
    assert members(case("Preencher campo nome", url="https://fixture.invalid/boletos")) == []


@pytest.mark.parametrize("title,checks,name", [
    ("Selecionar opção", ["product_sort_order"], "Ordenar catálogo de produtos"),
    ("View details for Sauce Labs Backpack", ["product_detail_identity"], "Consultar produto"),
    ("Cart, empty", ["cart_count"], "Consultar carrinho"),
    ("Adicionar produto ao carrinho", ["cart_membership"], "Adicionar produto ao carrinho"),
    ("Remove", ["cart_item_removed"], "Remover produto do carrinho"),
    ("Conferir itens", ["checkout_items_match_cart"], "Revisar compra"),
    ("Checkout", [], "Iniciar compra"),
    ("Finalizar pedido", ["order_confirmation"], "Finalizar compra"),
])
def test_specific_business_checks_disambiguate_commerce_goals(title, checks, name):
    value = case(title, checks=checks)
    assert [member["name"] for member in members(value)] == [name]


def test_form_checks_do_not_become_a_completed_checkout_or_authentication():
    value = case("Preencher campo nome", checks=["form_required_validation"], url="https://fixture.invalid/checkout-step-one.html")
    assert [member["name"] for member in members(value)] == ["Informar dados da compra"]
    assert members(value)[0]["confidence"] == "suggested"
    assert members(case("Clicar login field", checks=["field_retains_value"])) == []


def test_associations_are_read_only_and_independent_of_verdicts():
    values = [case("Cadastrar boleto", status=status) for status in ["PASS", "FAIL", "BLOCKED", "SKIPPED"]]
    assert all(members(value) == members(values[0]) for value in values)
    before = deepcopy(values)
    for value in values:
        classify_case_flows(value)
    group_functional_flows(values)
    assert values == before


def test_aggregation_deduplicates_cts_and_uses_only_pass_fail_as_denominator():
    values = [case("Cadastrar boleto", status=status, identity=f"CT{index}") for index, status in enumerate(["PASS", "FAIL", "BLOCKED", "SKIPPED", None])]
    suggested = case("Preencher campo nome", url="https://fixture.invalid/boletos/novo", status="BLOCKED", identity="CTX")
    values += [suggested, deepcopy(values[0])]
    members(values[0]).append(deepcopy(members(values[0])[0]))
    flow, = group_functional_flows(values)
    assert flow["total"] == 6
    assert flow["counts"] == {"PASS": 1, "FAIL": 1, "BLOCKED": 2, "SKIPPED": 2}
    assert flow["evaluated"] == 2 and flow["approval_percent"] == 50
    assert flow["suggested_count"] == 1 and flow["identified_count"] == 5
    assert flow["confidence"] == "identified"
    assert group_functional_flows([suggested])[0]["approval_percent"] is None
    assert group_functional_flows([suggested])[0]["confidence"] == "suggested"


def test_multiple_objectives_keep_real_ct_identity_in_each_flow():
    value = case("Cadastrar boleto", steps=["Consultar cliente", "Cadastrar boleto"])
    flows = group_functional_flows([value])
    assert {flow["name"] for flow in flows} == {"Cadastrar boleto", "Consultar cliente"}
    assert all(flow["case_ids"] == ["CT001"] for flow in flows)
    assert len(flows) > len([value]), "Association counts are not coverage or a count of new CTs"


def test_empty_and_optional_metadata_remain_accessible():
    assert group_functional_flows([]) == []
    assert classify_case_flows({"title": None, "url": "http://[", "evidence": None, "steps": None})["memberships"] == []
    assert classify_case_flows({})["unclassified_reason"]
