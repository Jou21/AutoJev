import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { transpileModule, ModuleKind, JsxEmit, ScriptTarget } from "typescript";

const nativeRequire = createRequire(import.meta.url);
async function compile(file) {
  return transpileModule(await readFile(new URL(file, import.meta.url), "utf8"), { fileName: file,
    compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022, jsx: JsxEmit.ReactJSX } }).outputText;
}
const helperModule = { exports: {} };
new Function("module", "exports", await compile("../app/components/functional-flows.ts"))(helperModule, helperModule.exports);
const { functionalFlowStats, matchesFunctionalFlow, caseFlowLabel } = helperModule.exports;
const componentModule = { exports: {} };
new Function("require", "module", "exports", await compile("../app/components/FunctionalFlows.tsx"))(
  name => name.endsWith(".css") ? {} : name === "./functional-flows" ? helperModule.exports : nativeRequire(name), componentModule, componentModule.exports);
const { createElement } = nativeRequire("react"), { renderToStaticMarkup } = nativeRequire("react-dom/server");
const flow = { id: "boleto", name: "Cadastrar boleto", entity: "boleto", operation: "create", description: "Cadastros registrados", confidence: "identified", reason: "Objetivo explícito" };
const item = (id, status, membership = flow) => ({ id, status, flow_classification: { version: "functional-flows-v1", memberships: membership ? [membership] : [] } });
const render = props => renderToStaticMarkup(createElement(componentModule.exports.default, { flows: [flow], cases: [], selected: "all", onSelect() {}, ...props }));

test("flow stats deduplicate CTs and never count blocked/skipped as approved", () => {
  const cases = [item("A", "PASS"), item("B", "FAIL"), item("C", "BLOCKED"), item("D", "SKIPPED"), item("E", undefined), item("A", "PASS")];
  assert.deepEqual(functionalFlowStats(cases, flow.id), { total: 5, counts: { PASS: 1, FAIL: 1, BLOCKED: 1, SKIPPED: 2 }, evaluated: 2, suggested: 0, approval: 50 });
  assert.equal(functionalFlowStats([item("A", "BLOCKED")]).approval, null);
  assert.equal(functionalFlowStats([]).total, 0);
});

test("unclassified cases remain accessible and multiple associations do not create tests", () => {
  const value = item("A", "FAIL");
  value.flow_classification.memberships.push({ ...flow, id: "cliente", name: "Consultar cliente" });
  assert.equal(matchesFunctionalFlow(value, "cliente"), true);
  assert.equal(matchesFunctionalFlow(value, flow.id), true);
  assert.equal(matchesFunctionalFlow(value, "unclassified"), false);
  assert.equal(functionalFlowStats([value], "all").total, 1);
  assert.equal(matchesFunctionalFlow({ id: "legacy" }, "unclassified"), true);
  assert.equal(caseFlowLabel({ id: "legacy" }), "Não classificado");
  assert.equal(caseFlowLabel(value), "Cadastrar boleto · Consultar cliente");
});

test("the optional flow overview keeps actual status counts and the approval denominator", () => {
  const html = render({ cases: [item("A", "PASS"), item("B", "FAIL"), item("C", "BLOCKED")] });
  assert.match(html, /Cadastrar boleto/);
  assert.match(html, /Há falhas/);
  assert.match(html, /50% de aprovação · 2\/3 CTs avaliados/);
  assert.match(html, /não representam cobertura ou uma jornada completa/);
  assert.doesNotMatch(html, />Principais|>Complementares/);
  assert.match(html, /type="button"[^>]*data-select-flow="boleto"[^>]*aria-pressed="false"/);
});

test("weak associations, active filters and long flow lists stay explicit", () => {
  const suggested = { ...flow, confidence: "suggested" };
  const flows = Array.from({ length: 8 }, (_, index) => ({ ...suggested, id: "flow-" + index }));
  const html = render({ flows, cases: [item("A", "BLOCKED", flows[0])], selected: flows[0].id });
  assert.match(html, /Sugerido/);
  assert.match(html, /1 associação\(ões\) sugerida\(s\)/);
  assert.match(html, /Sem CTs avaliados/);
  assert.match(html, /<select[^>]*aria-label="Filtrar por fluxo funcional"/);
  assert.match(html, /<option value="flow-0" selected="">Cadastrar boleto \(1\) · sugerido/);
  assert.match(html, /<details class="functional-flows-overview"><summary>/);
  assert.match(html, /Explorar fluxos<\/span><span class="flow-overview-count">8/);
  assert.equal((html.match(/data-select-flow=/g) || []).length, 8);
  assert.match(render({ flows: [] }), /Nenhum fluxo identificado/);
  const compact = render({ flows: [suggested], compact: true, cases: [item("A", "FAIL", suggested)], selected: flow.id });
  assert.match(compact, /aria-label="Filtrar por fluxo funcional"/);
  assert.match(compact, /Cadastrar boleto \(1\) · sugerido/);
  assert.match(compact, /Remover filtro de fluxo/);
});

const text = node => node == null || typeof node === "boolean" ? "" : Array.isArray(node) ? node.map(text).join("")
  : typeof node === "object" ? text(node.props?.children) : String(node);
function* elements(node) {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) { for (const child of node) yield* elements(child); return; }
  if (!node.props) return;
  yield node;
  yield* elements(node.props.children);
}
const component = props => componentModule.exports.default({ flows: [flow], cases: [], selected: "all", onSelect() {}, ...props });

test("the default layout exposes filters, counts and failures without expanding the flow list", () => {
  const flows = Array.from({ length: 25 }, (_, index) => ({ ...flow, id: "flow-" + index, name: `Fluxo ${index}` }));
  const tree = component({ flows, cases: [item("A", "FAIL", flows[0]), item("B", "PASS", null)] });
  const all = [...elements(tree)];
  const details = all.find(node => node.type === "details");
  assert.ok(details);
  assert.ok(!details.props.open, "Overview is closed by default on small and large displays");
  assert.equal(all.filter(node => node.type === "details").length, 1, "One optional overview contains every flow, not a permanent grid plus more cards");
  const hiddenNodes = new Set(elements(details));
  const defaultNodes = all.filter(node => !hiddenNodes.has(node));
  assert.equal(defaultNodes.some(node => node.props["data-select-flow"]), false);
  assert.equal(defaultNodes.some(node => node.type === "h2" || node.props.className === "section-kicker"), false);
  assert.equal(defaultNodes.filter(node => node.type === "option").length, 27);
  assert.equal(all.filter(node => node.props["data-select-flow"]).length, 25, "No flow is dropped by an arbitrary first-six limit");
  const context = defaultNodes.find(node => node.props.role === "status");
  assert.equal(context.props["aria-live"], "polite");
  assert.equal(context.props["aria-atomic"], "true");
  assert.match(text(context), /2 CTs exibidos.*1 reprovado/);
  assert.equal(defaultNodes.some(node => node.type === "button" && text(node).startsWith("Não classificados")), true);
  const list = all.find(node => node.props["aria-label"] === "Fluxos disponíveis");
  assert.equal(list.props.role, "region");
  assert.equal(list.props.tabIndex, 0, "The scrollable overview can receive keyboard focus");
});

test("flow selection, unclassified and clearing only emit the chosen filter and do not mutate cases", () => {
  const cases = [item("A", "FAIL"), item("B", "PASS", null)], before = structuredClone(cases), chosen = [];
  const props = { cases, selected: flow.id, onSelect: value => chosen.push(value) };
  const nodes = [...elements(component(props))];
  const select = nodes.find(node => node.type === "select");
  assert.equal(select.props.value, flow.id);
  select.props.onChange({ target: { value: "unclassified" } });
  nodes.find(node => node.type === "button" && text(node).startsWith("Não classificados")).props.onClick();
  nodes.find(node => node.props["aria-label"] === "Remover filtro de fluxo").props.onClick();
  nodes.find(node => node.props["data-select-flow"] === flow.id).props.onClick();
  const unclassifiedNodes = [...elements(component({ ...props, selected: "unclassified" }))];
  unclassifiedNodes.find(node => node.type === "button" && text(node).startsWith("Não classificados")).props.onClick();
  assert.deepEqual(chosen, ["unclassified", "unclassified", "all", "all", "all"]);
  assert.deepEqual(cases, before);
  const status = nodes.find(node => node.props.role === "status");
  assert.match(text(status), /1 CT exibido.*Cadastrar boleto.*1 reprovado/);
});

test("long names and empty filters remain available in native options without a false approval", () => {
  const name = "Consultar produto com nome muito comprido e verificar suas características no catálogo";
  const flows = [{ ...flow, name }, { ...flow, id: "empty", name: "Excluir produto", confidence: "suggested" }];
  const html = render({ flows, cases: [item("A", "BLOCKED", flows[0])], selected: "empty" });
  assert.match(html, /<option value="boleto">Consultar produto com nome muito comprido/);
  assert.match(html, /<option value="empty" selected="">Excluir produto \(0\) · sugerido/);
  assert.match(html, /0 CTs exibidos/);
  assert.match(html, /Sem CTs avaliados \(aprovado\/reprovado\)/);
  assert.doesNotMatch(html, /100% de aprovação/);
  const noFlows = [...elements(component({ flows: [], cases: [item("A", "PASS", null)] }))];
  assert.equal(noFlows.filter(node => node.type === "details").length, 0);
  assert.equal(noFlows.filter(node => node.type === "option").length, 2);
  assert.match(text(noFlows.find(node => node.props.role === "status")), /1 CT exibido/);
});

test("responsive flow styles wrap controls and bound the optional overview by viewport height", async () => {
  const css = await readFile(new URL("../app/components/FunctionalFlows.css", import.meta.url), "utf8");
  assert.match(css, /\.functional-flow-filter-bar\s*\{[^}]*flex-wrap: wrap;[^}]*min-width: 0;/);
  assert.match(css, /\.functional-flow-select > select\s*\{[^}]*min-width: 0;[^}]*width: 100%;/);
  assert.match(css, /\.functional-flow-list\s*\{[^}]*max-height: min\(320px, 42dvh\);[^}]*overflow: auto;[^}]*overscroll-behavior: contain;/);
  assert.match(css, /\.functional-flow-list:focus-visible/);
  assert.match(css, /@media \(max-width: 640px\)[\s\S]*flex-basis: 100%;[\s\S]*max-height: min\(280px, 38dvh\);/);
  assert.doesNotMatch(css, /grid-template-columns: repeat\(3/);
  assert.doesNotMatch(css, /font-size: 23px/);
});
