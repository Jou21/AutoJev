"""Real local browser → immutable action evidence → standalone report.

The shop is fulfilled entirely by Playwright, with no external network or
model calls. Its report and original PNGs remain in pytest's isolated temp
directory so the same artifact can be inspected with the browser MCP.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
from html.parser import HTMLParser
import json
import struct

import pytest

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.adapters.report import StandaloneHtmlReport
from autojev.domain.models import AgentDecision, ExplorationOptions, ScenarioStatus, utc_now
from autojev.services.business_checks import check_business_outcomes
from autojev.services.exploration import ExplorationManager
from autojev.services.scenario_documentation import local_scenario_documentation
from autojev.services.technical_signals import build_scenario_evidence, scenario_screenshots


SHOP_HTML = """<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<title>Ateliê — Objetos para o seu dia</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f7f8fa;color:#1c2937;font:16px Arial,sans-serif}
header{height:86px;border-bottom:1px solid #e4e8ec;background:#fff;display:flex;align-items:center;
justify-content:space-between;padding:0 72px}.brand{font-size:26px;font-weight:800;letter-spacing:-1px}
.brand span{color:#d8793b}.nav{display:flex;align-items:center;gap:32px;font-size:14px;color:#64748b}
.bag{padding:12px 18px;border-radius:24px;background:#f1f5f9;font-weight:bold;color:#334155}
main{max-width:1296px;margin:auto;padding:46px 0}.eyebrow{font-size:12px;color:#b66431;letter-spacing:2px;
font-weight:700;text-transform:uppercase}h1{font-size:42px;letter-spacing:-1.5px;margin:12px 0}
.intro{color:#64748b;line-height:1.6;margin:0}.collection-head{display:flex;align-items:flex-end;
justify-content:space-between;margin-bottom:30px}.sort{display:flex;align-items:center;gap:12px;color:#64748b;font-size:14px}
select{height:44px;border:1px solid #d5dde5;border-radius:10px;padding:0 14px;background:white;color:#233449;
font-size:14px;min-width:200px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
.product-card{background:white;border:1px solid #e1e7eb;border-radius:18px;overflow:hidden}
.product-visual{height:264px;display:flex;justify-content:center;align-items:center;position:relative}
.product-visual:after{content:'';position:absolute;bottom:42px;width:150px;height:18px;background:#26334212;
border-radius:50%;filter:blur(7px)}.mug-scene{background:#eaf1f0}.notebook-scene{background:#f0ecf5}
.lamp-scene{background:#f5f0e7}.mug{position:relative;width:113px;height:111px;background:#789e95;border-radius:10px 10px 43px 43px;
box-shadow:inset -17px 0 #517b7026}.mug:after{content:'';position:absolute;width:43px;height:59px;right:-29px;top:15px;
border:12px solid #789e95;border-left:0;border-radius:0 24px 24px 0}.mug:before{content:'';position:absolute;left:4px;right:4px;
top:-8px;height:18px;border:5px solid #7fa399;border-radius:50%;background:#dce7e3}
.notebook{width:112px;height:149px;background:#9585b2;border-radius:3px 12px 12px 3px;transform:rotate(-10deg);
border-left:8px solid #786a98;box-shadow:5px 8px 0 #ded6ea}.notebook:after{content:'ideias';display:block;color:#ede7f3;
text-align:center;margin-top:34px;font-size:16px;letter-spacing:2px}.lamp{position:relative;width:125px;height:62px;
background:#dbb971;border-radius:70px 70px 5px 5px;align-self:center;margin-top:-60px}.lamp:before{content:'';position:absolute;
width:10px;height:105px;background:#9b8051;left:58px;top:62px}.lamp:after{content:'';position:absolute;width:95px;height:14px;
background:#c6a365;left:15px;top:155px;border-radius:50%}.product-info{padding:24px}.tag{color:#83919e;font-size:11px;
letter-spacing:1.5px;text-transform:uppercase}.product-name{font-size:22px;font-weight:700;margin:10px 0}
.product-description{color:#7a8796;font-size:14px;margin-bottom:24px}.product-bottom{display:flex;align-items:center;
justify-content:space-between;gap:8px}.price{font-weight:700;font-size:22px}button{background:#263a35;color:white;border:0;
border-radius:9px;font-size:13px;min-height:42px;padding:0 15px;font-weight:600;cursor:pointer}button:hover{background:#3a514a}
.run-status{display:flex;align-items:center;gap:10px;color:#61756c;font-size:13px;margin-top:25px;
padding:16px 20px;border:1px solid #dce7df;border-radius:10px;background:#eff6f1}.dot{width:8px;height:8px;background:#77a28b;
border-radius:50%}.footer{margin-top:30px;display:flex;justify-content:space-between;color:#94a0ac;font-size:12px}
</style><header><div class="brand">ateliê<span>.</span></div><div class="nav"><span>Objetos essenciais</span>
<span>Nossa história</span><div class="bag">Sacola · <span id="bag-count">0</span></div></div></header>
<main><div class="collection-head"><div><div class="eyebrow">Feito para durar</div><h1>Pequenos objetos. Grandes momentos.</h1>
<p class="intro">Uma seleção cuidadosa de peças para tornar o dia mais leve.</p></div>
<label class="sort">Ordenar por <select aria-label="Ordenar produtos" id="sort"><option value="name">Nome (A a Z)</option>
<option value="price-asc">Menor preço</option><option value="price-desc">Maior preço</option></select></label></div>
<section class="grid" id="products" aria-label="Produtos"></section>
<div class="run-status" role="status"><span class="dot"></span><span id="status">3 objetos selecionados para você.</span></div>
<div class="footer"><span>Materiais naturais. Design atemporal.</span><span>Ambiente demonstrativo · Validação local sem IA</span></div></main>
<script>
const products=[{id:'notebook',name:'Caderno de ideias',price:59,description:'Papel natural, espaço para criar.',scene:'notebook'},
{id:'mug',name:'Copo de cerâmica',price:39,description:'Seu café, no ritmo certo.',scene:'mug'},
{id:'lamp',name:'Luminária de mesa',price:129,description:'Uma luz acolhedora para o seu canto.',scene:'lamp'}];
let bag=0;
function render(){
 document.querySelector('#products').innerHTML=products.map(product=>`<article class="product-card" data-product-id="${product.id}">
 <div class="product-visual ${product.scene}-scene"><div class="${product.scene}"></div></div><div class="product-info">
 <div class="tag">Coleção essencial</div><h2 class="product-name">${product.name}</h2><p class="product-description">${product.description}</p>
 <div class="product-bottom"><span class="price">R$ ${product.price},00</span><button aria-label="Adicionar ${product.name} à sacola"
 data-product="${product.id}">Adicionar à sacola +</button></div></div></article>`).join('');
 document.querySelectorAll('[data-product]').forEach(button=>button.onclick=()=>{
  bag++;document.querySelector('#bag-count').textContent=bag;button.textContent='Adicionado ✓';
  document.querySelector('#status').textContent=bag+' produto na sacola.';
 });
}
document.querySelector('#sort').onchange=event=>{
 const value=event.target.value;
 products.sort(value==='price-asc'?(a,b)=>a.price-b.price:value==='price-desc'?(a,b)=>b.price-a.price:(a,b)=>a.name.localeCompare(b.name));
 render();document.querySelector('#status').textContent=value==='price-asc'?'Ordenado por menor preço.':
 value==='price-desc'?'Ordenado por maior preço.':'Ordenado pelo nome.';
};render();
</script></html>"""


class PreviewParser(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.previews = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "figure" and "action-preview" in attributes.get("class", "").split():
            self.previews.append(json.loads(attributes["data-action-preview"]))


class SelectionVerificationParser(HTMLParser):
    """Inspect rendered comparisons, without matching embedded JS/CSS strings."""

    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, document):
        super().__init__()
        self.verifications = []
        self.technical_details = []
        self._depth = 0
        self._verification = self._card = self._value = self._technical = None
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if tag not in self._VOID_TAGS:
            self._depth += 1
        if "selection-verification" in classes:
            block = {"result": attributes.get("data-result"), "values": {}}
            self.verifications.append(block)
            self._verification = (self._depth, block)
        if "selection-choice" in classes and self._verification:
            self._card = (self._depth, attributes.get("data-side"))
        if "selection-choice-value" in classes and self._card:
            self._value = (self._depth, self._card[1], [])
        if tag == "details" and "selection-technical" in classes:
            detail = {"open": "open" in attributes, "text": ""}
            self.technical_details.append(detail)
            self._technical = (self._depth, detail, [])

    def handle_data(self, data):
        if self._value:
            self._value[2].append(data)
        if self._technical:
            self._technical[2].append(data)

    def handle_endtag(self, tag):
        if self._value and self._value[0] == self._depth:
            self._verification[1]["values"][self._value[1]] = " ".join(
                "".join(self._value[2]).split()
            )
            self._value = None
        if self._card and self._card[0] == self._depth:
            self._card = None
        if self._verification and self._verification[0] == self._depth:
            self._verification = None
        if self._technical and self._technical[0] == self._depth:
            self._technical[1]["text"] = "".join(self._technical[2])
            self._technical = None
        self._depth -= 1


async def test_real_executor_action_previews_survive_scenario_and_standalone_report(tmp_path):
    playwright_api = pytest.importorskip("playwright.async_api")
    run_id = "action-preview-integration"
    shots = tmp_path / run_id / "screenshots"
    shots.mkdir(parents=True)
    async with playwright_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                viewport={"width": 1440, "height": 900}, device_scale_factor=2,
            )
            routes = []

            async def fulfill_local(route):
                routes.append(route.request.url)
                assert route.request.url.startswith("https://ateliereport.test/")
                await route.fulfill(status=200, content_type="text/html", body=SHOP_HTML)

            await page.route("**/*", fulfill_local)
            await page.goto("https://ateliereport.test/colecao")
            executor = PlaywrightExecutor(shots, ExplorationOptions(), headless=True)
            executor._page, executor._browser, executor._context = page, browser, page.context
            executor._allowed_origin = executor._origin(page.url)
            observation = await executor.observe("colecao-inicial")
            scenarios, actions = [], []
            started = utc_now()
            definitions = [
                ("select", "Ordenar produtos", "price-asc", "Ordenado por menor preço.",
                 "Ordenar a coleção pelo menor preço"),
                ("click", "Adicionar Copo de cerâmica à sacola", None, "1 produto na sacola.",
                 "Adicionar o copo de cerâmica à sacola"),
            ]
            for step, (action, name, value, expected, title) in enumerate(definitions, 1):
                before = observation
                target = next(item for item in before.interactive_elements if item.get("name") == name)
                decision = AgentDecision(
                    action=action, target=target["ref"], value=value,
                    rationale=f"Validar a interação com {name} e conferir seu resultado observável.",
                    assertions=[{"kind": "text_contains", "expected": expected}],
                    scenario_hint={"title": title},
                )
                result, observation = await executor.execute(decision, {})
                assert result.success, result.message
                if action == "select":
                    result.assertions.extend(check_business_outcomes(before, decision, result, observation))
                    assert next(check for check in result.assertions if check.kind == "selection_matches").passed
                assert result.assertions and all(check.passed for check in result.assertions)
                assert result.evidence["action_preview"]["action"] == action
                assert result.evidence["action_preview"]["target"] == target["ref"]
                assert result.evidence["action_preview"]["image"] == {"width": 1440, "height": 900}
                if action == "select":
                    assert result.evidence["action_preview"]["selection"] == {
                        "version": "select-labels-v1",
                        "requested_label": "Menor preço",
                        "before_label": "Nome (A a Z)",
                        "after_label": "Menor preço",
                        "status": "confirmed",
                    }
                else:
                    assert "selection" not in result.evidence["action_preview"]
                assert await page.locator("#sort").input_value() == "price-asc"
                assert await page.locator(".product-name").all_text_contents() == [
                    "Copo de cerâmica", "Caderno de ideias", "Luminária de mesa",
                ]
                if action == "click":
                    assert await page.locator("#bag-count").text_content() == "1"
                    assert await page.locator('[data-product="mug"]').text_content() == "Adicionado ✓"
                result.evidence.update(
                    before_screenshot=before.screenshot,
                    before_observed_at=before.captured_at,
                    action_step=step,
                )
                evidence = build_scenario_evidence(observation, decision, result)
                action_entry = next(entry for entry in evidence["timeline"] if entry["kind"] == "action")
                assert action_entry["action_preview"] == result.evidence["action_preview"]
                screenshots = scenario_screenshots(evidence, result)
                assert result.evidence["action_preview"]["screenshot"] in screenshots
                documentation = local_scenario_documentation(observation, decision, result)
                scenarios.append({
                    "id": f"CT{step:03d}", **documentation, "status": "PASS", "url": observation.url,
                    "created_at": result.finished_at, "screenshots": screenshots, "evidence": evidence,
                })
                actions.append({"step_number": step, "decision": asdict(decision), "result": result.to_dict()})
            assert routes == ["https://ateliereport.test/colecao"]
            assert not await page.locator('[data-autojev-action-marker],.action-target-overlay').count()
            run = {
                "id": run_id, "status": "completed", "app_url": page.url,
                "started_at": started, "finished_at": utc_now(),
                "options": asdict(executor.options), "scenarios": scenarios, "actions": actions,
                "issues": [], "summary": {"total_scenarios": 2, "pass": 2, "fail": 0},
            }
            backup = deepcopy(run)
            digests = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in shots.glob("*.png")}
            report = tmp_path / "report.html"
            StandaloneHtmlReport(tmp_path).generate(run, str(report))
            assert run == backup
            document = report.read_text()
            previews = PreviewParser(document).previews
            assert len(previews) == 2
            assert [preview["action"] for preview in previews] == ["select", "click"]
            assert previews[0]["selection"] == scenarios[0]["evidence"]["action_preview"]["selection"]
            assert "selection" not in previews[1]
            assert "Opção solicitada" in document
            assert "Selecionada depois" in document
            assert "Seleção confirmada" in document
            selection_report = SelectionVerificationParser(document)
            assert selection_report.verifications == [{
                "result": "pass",
                "values": {"expected": "Menor preço", "actual": "Menor preço"},
            }]
            assert selection_report.technical_details
            assert all(not detail["open"] for detail in selection_report.technical_details)
            assert any("option_matches" in detail["text"] for detail in selection_report.technical_details)
            assert document.count('<details class="scenario"') == 2
            assert "Captura preservada" not in document, "action-owned captures already have reliable timing"
            for preview, scenario in zip(previews, scenarios, strict=True):
                expected_preview = scenario["evidence"]["action_preview"]
                assert preview["box"] == expected_preview["box"]
                assert preview["target"] == expected_preview["target"]
                filename = expected_preview["screenshot"]
                from PIL import Image
                with Image.open(shots / filename) as image:  # JPEG capture
                    assert image.size == (1440, 900)
                assert filename in document
            assert digests == {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in shots.glob("*.png")
            }
            # The report adds the marker at render time; native target pixels
            # in both before-action PNG files remain unannotated and intact.
            print(f"ACTION_PREVIEW_REPORT={report}")
        finally:
            await browser.close()


async def test_reverted_selection_keeps_failed_business_verdict_and_honest_report(tmp_path):
    playwright_api = pytest.importorskip("playwright.async_api")
    run_id = "dropdown-mismatch-integration"
    shots = tmp_path / run_id / "screenshots"
    shots.mkdir(parents=True)
    async with playwright_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            async def fulfill_local(route):
                assert route.request.url.startswith("https://ateliereport.test/")
                await route.fulfill(status=200, content_type="text/html", body=SHOP_HTML)
            await page.route("**/*", fulfill_local)
            await page.goto("https://ateliereport.test/colecao")
            # An intentional fixture defect: an event handler discards the
            # user's choice, while Playwright still dispatches select normally.
            await page.locator("#sort").evaluate("el => el.onchange = () => { el.value = 'name'; }")
            executor = PlaywrightExecutor(shots, ExplorationOptions(), headless=True)
            executor._page, executor._browser, executor._context = page, browser, page.context
            executor._allowed_origin = executor._origin(page.url)
            before = await executor.observe("antes-da-selecao")
            target = next(item for item in before.interactive_elements if item.get("name") == "Ordenar produtos")
            decision = AgentDecision(
                action="select", target=target["ref"], value="price-asc",
                rationale="Selecionar menor preço e conferir a opção mantida pelo site.",
            )
            result, after = await executor.execute(decision, {})
            assert result.success, "dispatch success must not conceal a failed business result"
            selection = result.evidence["action_preview"]["selection"]
            assert selection == {
                "version": "select-labels-v1", "requested_label": "Menor preço",
                "before_label": "Nome (A a Z)", "after_label": "Nome (A a Z)",
                "status": "mismatch",
            }
            result.assertions.extend(check_business_outcomes(before, decision, result, after))
            check = next(item for item in result.assertions if item.kind == "selection_matches")
            assert not check.passed
            result.evidence.update(before_screenshot=before.screenshot, action_step=1)
            scenario = ExplorationManager._scenario_from_evidence(after, decision, result, 1)
            assert scenario.status == ScenarioStatus.FAIL
            run = {
                "id": run_id, "status": "completed", "app_url": after.url,
                "started_at": result.started_at, "finished_at": result.finished_at,
                "options": asdict(executor.options), "scenarios": [asdict(scenario)],
                "actions": [{"step_number": 1, "decision": asdict(decision), "result": result.to_dict()}],
                "issues": [], "summary": {"total_scenarios": 1, "pass": 0, "fail": 1},
            }
            backup = deepcopy(run)
            digests = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in shots.glob("*.png")}
            report = tmp_path / "report.html"
            StandaloneHtmlReport(tmp_path).generate(run, str(report))
            document = report.read_text()
            assert PreviewParser(document).previews[0]["selection"] == selection
            assert 'data-selection-status="mismatch"' in document
            assert "Seleção diferente da solicitada" in document
            selection_report = SelectionVerificationParser(document)
            assert selection_report.verifications == [{
                "result": "fail",
                "values": {"expected": "Menor preço", "actual": "Nome (A a Z)"},
            }]
            assert selection_report.technical_details
            assert all(not detail["open"] for detail in selection_report.technical_details)
            assert any("option_matches" in detail["text"] for detail in selection_report.technical_details)
            assert 'data-status="FAIL"' in document
            assert "REPROVADO" in document
            assert run == backup
            assert digests == {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in shots.glob("*.png")
            }
            assert not await page.locator(".action-target-overlay").count()
            print(f"DROPDOWN_MISMATCH_REPORT={report}")
        finally:
            await browser.close()
