// Isolated component events and focus effects. No browser, live API, model or recorder is used.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { transpileModule, ModuleKind, JsxEmit, ScriptTarget } from "typescript";

const nativeRequire = createRequire(import.meta.url);
async function compiled(path) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  return transpileModule(source, { fileName: path, compilerOptions: {
    module: ModuleKind.CommonJS, target: ScriptTarget.ES2022, jsx: JsxEmit.ReactJSX,
  } }).outputText;
}
const source = await compiled("../app/components/TestSuitesWorkspace.tsx");
const dependencies = {};
for (const name of ["suite-planning-chat", "suite-capture-plan", "suite-execution-history", "suite-horizontal-rail", "functional-flows"]) {
  const compiledModule = { exports: {} };
  new Function("module", "exports", await compiled(`../app/components/${name}.ts`))(compiledModule, compiledModule.exports);
  dependencies[`./${name}`] = compiledModule.exports;
}
const flowUiModule = { exports: {} };
new Function("require", "module", "exports", await compiled("../app/components/FunctionalFlows.tsx"))(
  name => name.endsWith(".css") ? {} : dependencies[name] || nativeRequire(name), flowUiModule, flowUiModule.exports);
dependencies["./FunctionalFlows"] = flowUiModule.exports;
const suiteFixture = () => ({ id: "root-fixture", app_url: "https://fixture.invalid", initial_count: 1, rounds: [], discoveries: [],
  cases: [{ id: "CT001", title: "Ordenar produtos", description: "Referência", steps: ["Abrir página"], expected_result: "Lista ordenada",
    preconditions: [], url: "https://fixture.invalid", mode: "automatic", revision: 1, status: "PASS" }] });
const text = node => node === null || typeof node === "boolean" || node === undefined ? "" : Array.isArray(node)
  ? node.map(text).join("") : typeof node === "object" ? text(node.props?.children) : String(node);
function* elements(node) {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) { for (const item of node) yield* elements(item); return; }
  if (!node.props) return;
  yield node;
  if (node.type === flowUiModule.exports.default) yield* elements(node.type(node.props));
  yield* elements(node.props.children);
}

async function workspace(t, providedSuite, onReport, initialProps = {}) {
  const slots = [], effects = [], calls = [], focus = [], scroll = [];
  const domRefs = new Set();
  const listeners = new Map();
  const browser = { setInterval() { return 1; }, clearInterval() {},
    addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback); } };
  const suite = providedSuite || suiteFixture();
  let cursor = 0, dirty = true, tree;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const hooks = {
    Fragment: Symbol.for("react.fragment"),
    useState(initial) {
      const index = cursor++;
      slots[index] ||= { value: typeof initial === "function" ? initial() : initial };
      return [slots[index].value, next => {
        const value = typeof next === "function" ? next(slots[index].value) : next;
        if (!Object.is(value, slots[index].value)) { slots[index].value = value; dirty = true; }
      }];
    },
    useRef(initial) { const index = cursor++; slots[index] ||= { ref: { current: initial } }; return slots[index].ref; },
    useCallback(callback, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) slots[index] = { value: callback, deps };
      return slots[index].value;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) effects.push(() => {
        slots[index]?.cleanup?.(); slots[index] = { deps, cleanup: effect() };
      });
    },
  };
  function ChatFixture() {}
  let savedPlan;
  const compiledModule = { exports: {} };
  new Function("require", "module", "exports", "window", source)(name => name === "react" ? hooks
    : name === "./PlanningChat" ? { __esModule: true, default: ChatFixture }
      : name.endsWith(".css") ? {} : dependencies[name] || nativeRequire(name), compiledModule, compiledModule.exports,
  browser);
  const request = async (path, options = {}) => {
    calls.push({ path, method: options.method || "GET", body: options.body && JSON.parse(options.body) });
    if (options.method && initialProps.rejectWrite?.(path, options)) throw new Error("O CT foi alterado em outra tela. Atualize sua versão.");
    if (path === "/test-suites/root-fixture" && !options.method) return structuredClone(suite);
    if (path.startsWith("/test-suites/root-fixture/cases/") && path.endsWith("/catalog-state") && options.method === "PATCH") {
      const item = suite.cases.find(item => path === `/test-suites/root-fixture/cases/${item.id}/catalog-state`);
      assert.ok(item);
      const body = JSON.parse(options.body);
      assert.equal(body.expected_revision, item.catalog_revision || 0);
      item.archived = body.archived; item.catalog_revision = (item.catalog_revision || 0) + 1;
      return { id: item.id, archived: item.archived, catalog_revision: item.catalog_revision };
    }
    if (path === "/test-suites" && !options.method) return [{ ...structuredClone(suite), created_at: suite.created_at || "2026-10-14T16:22:00Z", initial_status: "completed", legacy_runs: suite.legacy_runs || [] }];
    if (path.startsWith("/test-suites/rounds/") && !options.method) {
      const found = suite.rounds.find(round => path === "/test-suites/rounds/" + round.id);
      if (found) return structuredClone(found);
    }
    if (path === "/test-suites/root-fixture/plans" && options.method === "POST") {
      savedPlan = { ...JSON.parse(options.body), id: "plan-fixture", root_id: suite.id, status: "draft", revision: 1 };
      return savedPlan;
    }
    if (path === "/test-suites/rounds/plan-fixture/validate" && options.method === "POST") return {
      revision: 1, available: true, effects: ["Somente os CTs selecionados serão executados após confirmação."],
      items: savedPlan.items.map(item => ({ case_id: item.case_id, available: true, code: "automatic" })),
    };
    if (path === "/test-suites/root-fixture/recordings" && options.method === "POST") return { id: "rec-fixture", steps: [], status: "recording", url: suite.app_url };
    if (path === "/test-suites/recordings/rec-fixture/finish" && options.method === "POST") return { id: "rec-fixture", steps: ["Abrir carrinho"], status: "completed", url: suite.app_url };
    if (path === "/test-suites/root-fixture/assistant" && options.method === "POST") return initialProps.assistantReply || { reply: "Revise o rascunho.", selected_case_ids: [], new_cases: [{ title: "Carrinho vazio", url: suite.app_url, description: "Proposta", preconditions: [], steps: ["Abrir carrinho"], expected_result: "Vazio" }], usage: {} };
    if (path === "/test-suites/root-fixture/cases" && options.method === "POST") {
      const id = "CT" + String(Math.max(...suite.cases.map(item => Number(item.id.slice(2))), 0) + 1).padStart(3, "0");
      const item = { ...JSON.parse(options.body), id, mode: "manual", revision: 1 };
      suite.cases.push(item); return item;
    }
    if (path.startsWith("/test-suites/root-fixture/cases/") && options.method === "PUT") {
      const index = suite.cases.findIndex(item => path.endsWith("/" + item.id));
      assert.ok(index >= 0);
      const { expected_revision, ...definition } = JSON.parse(options.body);
      assert.equal(expected_revision, suite.cases[index].revision);
      const item = { ...definition, id: suite.cases[index].id, mode: "manual", revision: expected_revision + 1 };
      suite.cases[index] = { ...item, archived: suite.cases[index].archived, catalog_revision: suite.cases[index].catalog_revision };
      return structuredClone(item);
    }
    assert.fail("Unexpected request: " + path);
  };
  const props = { rootId: suite.id, request, navigate() { assert.fail("No navigation expected"); }, openReport: onReport || (() => { assert.fail("No export expected"); }), ...initialProps };
  const fakeTarget = name => ({ focus(options) { focus.push({ name, options }); }, scrollIntoView(options) { scroll.push({ name, options }); }, reportValidity() { return true; } });
  function render() {
    cursor = 0; dirty = false;
    tree = compiledModule.exports.default(props);
    for (const ref of domRefs) ref.current = null;
    domRefs.clear();
    for (const node of elements(tree)) {
      if (node.props.ref) { node.props.ref.current = fakeTarget(node.props.id || node.props["aria-label"] || text(node) || node.type); domRefs.add(node.props.ref); }
      if (node.type === ChatFixture) { node.props.composerRef.current = fakeTarget("chat-composer"); domRefs.add(node.props.composerRef); }
    }
    while (effects.length) effects.shift()();
  }
  async function settle() {
    for (let index = 0; index < 20; index++) { if (dirty) render(); await Promise.resolve(); }
    if (dirty) render();
  }
  const all = () => [...elements(tree)];
  const find = predicate => { const result = all().find(predicate); assert.ok(result, "Expected component element"); return result; };
  const button = label => find(node => node.type === "button" && (node.props["aria-label"] === label || text(node).startsWith(label)));
  const field = label => [...elements(find(node => node.type === "label" && text(node).startsWith(label)))].find(node => ["input", "textarea"].includes(node.type));
  async function click(label) { const node = button(label); assert.ok(!node.props.disabled); node.props.onClick({ currentTarget: fakeTarget(label), preventDefault() {} }); await settle(); }
  async function change(label, value) { const node = field(label); assert.ok(node); node.props.onChange({ target: { value } }); await settle(); }
  t.after(() => { for (const slot of slots) slot?.cleanup?.(); });
  await settle();
  return { all, find, button, field, click, change, settle, calls, focus, scroll, chat: () => find(node => node.type === ChatFixture),
    async navigate(hash) { const event = { detail: { hash }, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; for (const callback of listeners.get("autojev:before-navigate") || []) callback(event); await settle(); return !event.defaultPrevented; },
    unloadBlocked() { const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; for (const callback of listeners.get("beforeunload") || []) callback(event); return event.defaultPrevented; },
    has: id => all().some(node => node.props.id === id), submit: async () => { find(node => node.type === "form" && node.props.id === "suite-case-editor").props.onSubmit({ preventDefault() {} }); await settle(); } };
}

test("writing opens and focuses the editor; repeated clicks and mode changes preserve the draft", async t => {
  const view = await workspace(t);
  await view.click("Adicionar teste");
  await view.click("Escrever teste");
  assert.equal(view.button("Escrever teste").props["aria-pressed"], true);
  assert.equal(view.has("suite-case-editor"), true);
  assert.equal(view.scroll.at(-1).name, "suite-case-editor");
  assert.equal(view.focus.at(-1).name, "input");
  await view.change("Título", "Meu rascunho preservado");
  await view.change("Passos", "Abrir carrinho\nConferir produtos");
  await view.click("Escrever teste");
  assert.equal(view.field("Título").props.value, "Meu rascunho preservado");
  await view.click("Gravar minha navegação");
  assert.equal(view.has("suite-case-editor"), false);
  await view.click("Escrever teste");
  assert.equal(view.has("suite-record-confirmation"), false);
  assert.equal(view.field("Título").props.value, "Meu rascunho preservado");
  assert.equal(view.field("Passos").props.value, "Abrir carrinho\nConferir produtos");
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("AI mode focuses the existing chat, reopens it when closed, and never sends automatically", async t => {
  const view = await workspace(t);
  await view.click("Adicionar teste");
  await view.click("Planejar com IA");
  assert.equal(view.focus.at(-1).name, "chat-composer");
  assert.equal(view.scroll.at(-1).name, "chat-composer");
  const count = view.focus.length;
  await view.click("Planejar com IA");
  assert.equal(view.focus.length, count + 1);
  await view.click("Assistente");
  assert.equal(view.button("Assistente").props["aria-expanded"], false);
  await view.click("Planejar com IA");
  assert.equal(view.button("Assistente").props["aria-expanded"], true);
  assert.equal(view.button("Planejar com IA").props["aria-pressed"], true);
  assert.equal(view.focus.at(-1).name, "chat-composer");
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("recording requires confirmation, remains visible when options close, and finishes in a focused draft", async t => {
  const view = await workspace(t);
  await view.click("Adicionar teste");
  await view.click("Gravar minha navegação");
  await view.click("Gravar minha navegação");
  assert.equal(view.has("suite-record-confirmation"), true);
  assert.match(view.focus.at(-1).name, /Confirmar e abrir navegador/);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
  await view.click("Confirmar e abrir navegador de gravação");
  assert.equal(view.has("suite-recording"), true);
  assert.equal(view.has("suite-record-confirmation"), false);
  assert.match(view.focus.at(-1).name, /Finalizar e revisar roteiro/);
  assert.deepEqual(view.calls.filter(call => call.method === "POST").map(({ path, body }) => ({ path, body })), [
    { path: "/test-suites/root-fixture/recordings", body: { confirm: true } },
  ]);
  await view.click("Fechar opções de criação");
  assert.equal(view.has("suite-recording"), true);
  await view.click("Finalizar e revisar roteiro");
  assert.equal(view.has("suite-recording"), false);
  assert.equal(view.has("suite-case-editor"), true);
  assert.equal(view.field("Título").props.value, "Teste gravado");
  assert.equal(view.field("Passos").props.value, "Abrir carrinho");
  assert.equal(view.focus.at(-1).name, "input");
  assert.equal(view.calls.filter(call => /\/(cases|start|discover)$/.test(call.path)).length, 0);
});

test("AI suggestions open a focused editor and a manual case is created only after form submission", async t => {
  const view = await workspace(t);
  await view.click("Adicionar teste");
  await view.click("Planejar com IA");
  await view.chat().props.onSend("Sugira um caso para o carrinho", { provider_id: "fixture", model_id: "fixture" });
  await view.settle();
  await view.click("Carrinho vazio");
  assert.equal(view.field("Título").props.value, "Carrinho vazio");
  assert.equal(view.focus.at(-1).name, "input");
  assert.equal(view.calls.filter(call => call.path.endsWith("/cases")).length, 0);
  await view.submit();
  assert.equal(view.calls.filter(call => call.path.endsWith("/cases")).length, 1);
  assert.equal(view.has("suite-case-editor"), false);
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Selecionar CT002").props.checked, false);
  assert.match(text(view.find(node => node.props.role === "status" && text(node).includes("adicionado à base"))), /Nenhuma execução foi criada/);
  assert.equal(view.calls.filter(call => /\/(plans|start|discover)$/.test(call.path)).length, 0);
});

test("cloning an existing case also opens the editor with its reference intact", async t => {
  const view = await workspace(t);
  await view.click("Duplicar CT001 como novo CT manual");
  assert.equal(view.field("Título").props.value, "Ordenar produtos · cópia");
  assert.equal(view.field("Resultado esperado").props.value, "Lista ordenada");
  assert.equal(view.scroll.at(-1).name, "suite-case-editor");
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("catalog rows open the stable CT in the consolidated report without planning or executing it", async t => {
  const suite = suiteFixture(), reports = [];
  suite.cases[0].source_exploration_id = "child-run";
  suite.cases[0].source_scenario_id = "CT009";
  const before = structuredClone(suite);
  const view = await workspace(t, suite, async (...args) => { reports.push(args); });
  await view.click("Ver resultado de CT001: Ordenar produtos");
  assert.deepEqual(reports, [[suite.id, "consolidated", undefined, "CT001"]], "The report uses catalog identity, not an executor's local CT number");
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Selecionar CT001").props.checked, false);
  assert.deepEqual(suite, before);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
  assert.equal(view.has("suite-case-editor"), false);
});

test("selection, duplication and manual editing remain separate from opening a result", async t => {
  const suite = suiteFixture(), reports = [];
  suite.cases.push({ ...suite.cases[0], id: "CT002", title: "Caso manual", mode: "manual", status: undefined });
  const view = await workspace(t, suite, async (...args) => { reports.push(args); });
  const pending = view.button("Ver resultado de CT002: Caso manual");
  assert.equal(pending.props.disabled, true);
  assert.equal(text(pending), "Ainda não executado");
  view.find(node => node.type === "input" && node.props["aria-label"] === "Selecionar CT001").props.onChange();
  await view.settle();
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Selecionar CT001").props.checked, true);
  await view.click("Duplicar CT001 como novo CT manual");
  assert.equal(view.field("Título").props.value, "Ordenar produtos · cópia");
  await view.click("Testes");
  await view.click("Descartar e sair");
  await view.click("Editar");
  assert.equal(view.field("Título").props.value, "Caso manual");
  assert.deepEqual(reports, []);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.suite-case-open:not\(:disabled\)::after\s*\{[^}]*inset:\s*0;[^}]*z-index:\s*1/);
  assert.match(css, /\.suite-case-row > \.suite-check, \.suite-case-row > button:not\(\.suite-case-open\)\s*\{[^}]*z-index:\s*2/);
});

test("failed result navigation gives feedback without changing filters or selections", async t => {
  const view = await workspace(t, suiteFixture(), async () => { throw new Error("Não foi possível carregar as evidências."); });
  await view.click("Ver resultado de CT001: Ordenar produtos");
  assert.match(text(view.find(node => node.props.role === "alert")), /Não foi possível carregar as evidências/);
  assert.equal(view.button("Ver resultado de CT001: Ordenar produtos").props.disabled, false);
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Selecionar CT001").props.checked, false);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

function catalogFixture() {
  const suite = suiteFixture();
  const original = suite.cases[0];
  suite.cases = ["CT050", "CT060", "CT067", "CT094", "CT097", "CT108", "CT109"].map(id => ({ ...original, id, title: "Carrinho · " + id }));
  return suite;
}

async function markCatalog(view, id) {
  view.find(node => node.type === "input" && node.props["aria-label"] === "Selecionar " + id).props.onChange();
  await view.settle();
}

const selectionBar = view => view.find(node => node.props["aria-label"] === "Selecionados para reexecução");
const plannedIds = view => view.all().filter(node => node.type === "button" && /^Retirar CT/.test(node.props["aria-label"] || "")).map(node => node.props["aria-label"].match(/^Retirar (CT\d+)/)[1]);

test("two checked rows have a visible next step and planning includes only those two, never all seven", async t => {
  const suite = catalogFixture(), before = structuredClone(suite);
  const view = await workspace(t, suite);
  assert.equal(view.all().some(node => node.props["aria-label"] === "Selecionados para reexecução"), false);
  assert.match(text(view.find(node => node.props.id === "suite-catalog-selection-help")), /Selecionar não inicia nenhum teste/);
  await markCatalog(view, "CT050");
  assert.match(text(selectionBar(view)), /1 teste selecionado/);
  assert.equal(view.button("Preparar execução (1)").props.disabled, false);
  await markCatalog(view, "CT067");
  assert.match(text(selectionBar(view)), /2 testes selecionadosCT050 · CT067/);
  assert.equal(view.button("Selecionar os outros 5 filtrados").props.disabled, false);
  assert.equal(view.all().some(node => node.type === "button" && /Planejar CTs filtrados/.test(text(node))), false);
  await view.click("Preparar execução (2)");
  assert.deepEqual(plannedIds(view), ["CT050", "CT067"]);
  assert.equal(view.all().some(node => node.props["aria-label"] === "Selecionados para reexecução"), false);
  assert.equal(view.focus.at(-1).name, "suite-planning-heading");
  assert.equal(view.scroll.at(-1).name, "suite-planning-heading");
  assert.match(text(view.find(node => node.props.id === "suite-planning-guidance")), /2 testes selecionados.*Revisar execução.*após sua confirmação/);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
  assert.deepEqual(suite, before);
});

test("filtered-out selections stay explicit and proceed without adding an unselected visible CT", async t => {
  const view = await workspace(t, catalogFixture());
  await markCatalog(view, "CT050");
  await markCatalog(view, "CT067");
  view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.onChange({ target: { value: "CT060" } });
  await view.settle();
  assert.match(text(selectionBar(view)), /2 selecionados fora dos filtros atuais/);
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Selecionar CT060").props.checked, false);
  await view.click("Preparar execução (2)");
  assert.deepEqual(plannedIds(view), ["CT050", "CT067"]);
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.value, "CT060");
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("clearing or unchecking the final CT removes the bar, resets checkboxes and keeps the current query", async t => {
  const view = await workspace(t, catalogFixture());
  await markCatalog(view, "CT050");
  await markCatalog(view, "CT067");
  view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.onChange({ target: { value: "CT050" } });
  await view.settle();
  await view.click("Limpar seleção");
  assert.equal(view.all().some(node => node.props["aria-label"] === "Selecionados para reexecução"), false);
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Selecionar CT050").props.checked, false);
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.value, "CT050");
  assert.equal(view.focus.at(-1).name, "suite-catalog-selection-help");
  await markCatalog(view, "CT050");
  await markCatalog(view, "CT050");
  assert.equal(view.all().some(node => node.props["aria-label"] === "Selecionados para reexecução"), false);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("bulk selection marks visible cases without jumping screens or executing; its next action uses the exact count", async t => {
  const view = await workspace(t, catalogFixture());
  await view.click("Selecionar todos os 7 filtrados");
  assert.match(text(selectionBar(view)), /7 testes selecionados/);
  assert.match(text(selectionBar(view)), /\+3/);
  assert.equal(view.button("Todos os 7 filtrados selecionados").props.disabled, true);
  assert.equal(view.has("suite-planning-heading"), false);
  assert.equal(view.all().filter(node => node.type === "input" && node.props.type === "checkbox" && node.props.checked).length, 7);
  const status = [...elements(selectionBar(view))].find(node => node.props.role === "status");
  assert.equal(status.props["aria-live"], "polite");
  await view.click("Preparar execução (7)");
  assert.equal(plannedIds(view).length, 7);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.suite-catalog-selection\s*\{[^}]*position:\s*sticky;[^}]*bottom:/);
  assert.match(css, /@media \(max-width: 640px\)[^\n]*\.suite-selection-actions\s*\{\s*width:\s*100%/);
});

test("catalog-to-planner keeps an existing capture plan and the chosen CT order", async t => {
  const suite = catalogFixture();
  const capture = { version: 1, checkpoints: [{ id: "existing-print", gap: 0, order: 0 }] };
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT050", initialCapturePlan: capture });
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "existing-print"), true);
  await view.click("Testes");
  await markCatalog(view, "CT067");
  await view.click("Preparar execução (2)");
  assert.deepEqual(plannedIds(view), ["CT050", "CT067"]);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "existing-print"), true);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("compact flow filters combine with verdicts and bulk planning keeps the selected stable CTs", async t => {
  const suite = suiteFixture();
  const original = suite.cases[0];
  const create = { id: "flow-create", name: "Cadastrar cliente", entity: "cliente", operation: "create", description: "Cadastros de cliente", confidence: "identified", reason: "Objetivo explícito" };
  const remove = { ...create, id: "flow-delete", name: "Excluir cliente", operation: "delete" };
  const flow = memberships => ({ version: "functional-flows-v1", memberships });
  suite.functional_flows = [create, remove];
  suite.cases = [
    { ...original, id: "CT001", title: "Criar cliente", status: "PASS", flow_classification: flow([create]) },
    { ...original, id: "CT002", title: "Criar cliente sem nome", status: "FAIL", flow_classification: flow([create]) },
    { ...original, id: "CT003", title: "Preencher campo", status: "PASS", flow_classification: flow([]) },
    { ...original, id: "CT004", title: "Excluir cliente", status: "BLOCKED", flow_classification: flow([remove]) },
  ];
  const before = structuredClone(suite);
  const view = await workspace(t, suite);
  const visibleIds = () => view.all().filter(node => node.props.className === "suite-case-row").map(row => text(row.props.children[0]));
  const choose = async (label, value) => {
    view.find(node => node.type === "select" && node.props["aria-label"] === label).props.onChange({ target: { value } });
    await view.settle();
  };
  assert.deepEqual(visibleIds(), ["CT001", "CT002", "CT003", "CT004"]);
  await choose("Filtrar por fluxo funcional", "flow-create");
  assert.deepEqual(visibleIds(), ["CT001", "CT002"]);
  await choose("Filtrar CTs", "PASS");
  assert.deepEqual(visibleIds(), ["CT001"]);
  const option = view.find(node => node.type === "option" && node.props.value === "flow-create");
  assert.match(text(option), /\(1\)/);
  assert.doesNotMatch(text(option), /reprovado/);
  await view.click("Remover filtro de fluxo");
  assert.deepEqual(visibleIds(), ["CT001", "CT003"], "Removing the flow keeps the status filter");
  await choose("Filtrar CTs", "all");
  await choose("Filtrar por fluxo funcional", "unclassified");
  assert.deepEqual(visibleIds(), ["CT003"]);
  await choose("Filtrar por fluxo funcional", "flow-create");
  await view.click("Selecionar todos os 2 filtrados");
  await view.click("Preparar execução (2)");
  for (const identity of ["CT001", "CT002"]) {
    assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Incluir " + identity).props.checked, true);
  }
  await choose("Filtrar por fluxo funcional", "unclassified");
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Incluir CT003").props.checked, false);
  assert.equal(view.all().filter(node => node.type === "button" && /^Retirar CT00[12] desta execução$/.test(node.props["aria-label"] || "")).length, 2);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
  assert.deepEqual(suite, before);
});

test("the compact header keeps identity and actions together, with secondary context collapsed by default", async t => {
  const suite = catalogFixture();
  suite.created_at = "2026-10-14T19:22:00Z";
  const view = await workspace(t, suite);
  const header = view.find(node => node.type === "header" && node.props.className === "suite-detail-header");
  const headerElements = [...elements(header)];
  const title = headerElements.find(node => node.type === "h1");
  assert.equal(text(title), "fixture.invalid");
  assert.equal(title.props.title, "fixture.invalid");
  assert.equal(text(headerElements.find(node => node.props.className === "suite-detail-count")), "7 testes");
  assert.equal(headerElements.some(node => node.props.className === "section-kicker"), false);
  assert.equal(view.all().some(node => node.props.className === "suite-notice"), false, "No always-visible catalog banner");
  const context = headerElements.find(node => node.type === "details");
  assert.ok(context);
  assert.ok(!context.props.open, "Native details hides secondary information without a loading request");
  assert.equal(text(context.props.children[0]), "Sobre o conjunto");
  assert.match(text(context), /root-fixture/);
  assert.match(text(context), /Iniciado em/);
  assert.match(text(context), /CTs iniciais1CTs no catálogo7/);
  assert.match(text(context), /datas e rodadas diferentes.*não revalida os demais.*histórico é preservado/);
  const nav = headerElements.find(node => node.type === "nav");
  assert.equal(nav.props["aria-label"], "Áreas da base de testes");
  assert.deepEqual([...elements(nav)].filter(node => node.type === "button").map(node => node.props["aria-label"] || text(node)), ["Testes", "Preparar execução", "Execuções", "Atualizar conjunto"]);
  assert.equal(view.button("Testes").props["aria-pressed"], true);
  assert.equal(view.calls.length, 1);
});

test("compact navigation preserves selections, filters and capture plans, and focuses planning from either entry point", async t => {
  const capture = { version: 1, checkpoints: [{ id: "header-print", gap: 0, order: 0 }] };
  const view = await workspace(t, catalogFixture(), undefined, { initialCaseId: "CT050", initialCapturePlan: capture });
  await view.click("Testes");
  await markCatalog(view, "CT067");
  view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.onChange({ target: { value: "CT050" } });
  await view.settle();
  assert.equal(text(view.button("Preparar execução")), "Preparar execução2");
  await view.click("Execuções");
  assert.equal(view.button("Execuções").props["aria-pressed"], true);
  await view.click("Preparar execução");
  assert.deepEqual(plannedIds(view), ["CT050", "CT067"]);
  assert.equal(view.focus.at(-1).name, "suite-planning-heading");
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "header-print"), true);
  await view.click("Testes");
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.value, "CT050");
  assert.match(text(selectionBar(view)), /1 selecionado fora dos filtros atuais/);
  const focusCount = view.focus.length;
  await view.click("Preparar execução");
  assert.deepEqual(plannedIds(view), ["CT050", "CT067"]);
  assert.equal(view.focus.length, focusCount + 1);
  assert.equal(view.scroll.at(-1).name, "suite-planning-heading");
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("compact report, refresh and back actions stay read-only and do not clear selected cases", async t => {
  const reports = [], navigations = [];
  const view = await workspace(t, catalogFixture(), async (...args) => { reports.push(args); }, { navigate: hash => navigations.push(hash) });
  await markCatalog(view, "CT050");
  await markCatalog(view, "CT067");
  await view.click("Abrir relatório consolidado");
  assert.deepEqual(reports, [["root-fixture", "consolidated"]]);
  assert.match(text(selectionBar(view)), /2 testes selecionadosCT050 · CT067/);
  const callsBeforeRefresh = view.calls.length;
  await view.click("Atualizar conjunto");
  assert.equal(view.calls.length, callsBeforeRefresh + 1);
  assert.deepEqual(view.calls.at(-1), { path: "/test-suites/root-fixture", method: "GET", body: undefined });
  assert.equal(view.button("Atualizar conjunto").props.disabled, false);
  assert.match(text(selectionBar(view)), /2 testes selecionadosCT050 · CT067/);
  await view.click("Voltar para Meus testes");
  assert.deepEqual(navigations, ["#/exploracoes"]);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("leaving an unfinished base editor offers staying or explicit discard without creating an execution", async t => {
  const view = await workspace(t);
  await view.click("Adicionar teste");
  await view.click("Escrever teste");
  await view.change("Título", "Novo teste ainda não salvo");
  await view.click("Testes");
  assert.equal(view.find(node => node.props.role === "dialog").props["aria-modal"], "true");
  await view.click("Continuar editando");
  assert.equal(view.has("suite-case-editor"), true);
  assert.equal(view.field("Título").props.value, "Novo teste ainda não salvo");
  await view.click("Voltar aos testes");
  await view.click("Descartar e sair");
  assert.equal(view.has("suite-case-editor"), false);
  assert.equal(view.button("Testes").props["aria-pressed"], true);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("compact header styles are scoped to suite details and long application names can shrink", async t => {
  const suite = suiteFixture();
  const hostname = "aplicacao-com-um-nome-bem-comprido.departamento.empresa.invalid";
  suite.app_url = "https://" + hostname;
  const view = await workspace(t, suite);
  const title = view.find(node => node.type === "h1");
  assert.equal(text(title), hostname);
  assert.equal(title.props.title, hostname);
  assert.equal(text(view.find(node => node.props.className === "suite-detail-count")), "1 teste");
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.page-content:has\(\.suite-detail-workspace\)\s*\{\s*padding: 18px 24px 32px;/);
  assert.match(css, /\.suite-detail-identity h1\s*\{[^}]*min-width: 0;[^}]*text-overflow: ellipsis;[^}]*font-size: 20px;/);
  assert.match(css, /\.suite-context-content\s*\{[^}]*position: absolute;[^}]*width: min\(330px, calc\(100vw - 32px\)\);/);
  assert.match(css, /\.suite-workspace summary:focus-visible/);
  assert.match(css, /@media \(max-width: 640px\)\s*\{\s*\.page-content:has\(\.suite-detail-workspace\)\s*\{\s*padding: 14px 16px 24px;/);
  assert.match(css, /\.suite-detail-identity\s*\{\s*flex-basis: 100%;/);
  assert.match(css, /\.suite-page-heading h1\s*\{[^}]*font-size: clamp\(26px, 3.4vw, 42px\);/, "Suite list heading remains unchanged");
});

test("planning groups the catalog, scrollable editor and actions separately from the chat", async t => {
  const view = await workspace(t);
  await view.click("Preparar execução");
  const planner = view.find(node => node.props.className === "suite-planner-view");
  assert.equal(planner.props["data-mobile-pane"], "plan");
  const layout = view.find(node => node.props.className === "suite-planner-layout with-chat");
  assert.equal(layout.props.children[1], view.chat(), "Chat is a sibling of the work area, not below its editor or footer");
  const main = layout.props.children[0];
  assert.match(main.props.className, /suite-planning-main/);
  assert.equal([...elements(main)].some(node => node.props.className === "suite-plan-footer"), true);
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, false);
  const toolbar = view.find(node => node.props.className === "suite-plan-heading suite-plan-toolbar");
  const help = [...elements(toolbar)].find(node => node.type === "details");
  assert.ok(!help.props.open);
  assert.match(text(help), /confirme|executar/);
  assert.equal([...elements(toolbar)].some(node => node.props.className === "section-kicker"), false);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("opening and finishing CT selection preserves order, filters and existing capture checkpoints", async t => {
  const capture = { version: 1, checkpoints: [{ id: "drawer-print", gap: 0, order: 0 }] };
  const view = await workspace(t, catalogFixture(), undefined, { initialCaseId: "CT050", initialCapturePlan: capture });
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, true);
  await view.click("Escolher testes");
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, false);
  assert.equal(view.focus.at(-1).name, "Buscar CTs");
  view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.onChange({ target: { value: "CT067" } });
  await view.settle();
  view.find(node => node.type === "input" && node.props["aria-label"] === "Incluir CT067").props.onChange();
  await view.settle();
  await view.click("Ver roteiro (2)");
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, true);
  assert.equal(view.button("Escolher testes").props["aria-expanded"], false);
  assert.deepEqual(plannedIds(view), ["CT050", "CT067"]);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "drawer-print"), true);
  await view.click("Escolher testes");
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.value, "CT067");
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Incluir CT067").props.checked, true);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("the script has two independent horizontal rails and long explanations are collapsed", async t => {
  const suite = suiteFixture();
  suite.cases.push({ ...suite.cases[0], id: "CT003", title: "Consultar produto", steps: ["Abrir detalhe", "Conferir produto"] });
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  await view.chat().props.onSend("Adicione CT003", null); await view.settle();
  assert.equal(view.all().filter(node => node.props.className?.includes("suite-horizontal-rail")).length, 2);
  assert.equal(view.find(node => node.props["aria-label"] === "Testes selecionados, rolagem horizontal").props.tabIndex, 0);
  assert.equal(view.button("Ver etapas de CT001: Ordenar produtos").props["aria-pressed"], true);
  await view.click("Ver etapas de CT003: Consultar produto");
  assert.equal(view.button("Ver etapas de CT003: Consultar produto").props["aria-pressed"], true);
  assert.equal(view.all().some(node => node.props["aria-label"] === "Etapas de CT003, rolagem horizontal"), true);
  const details = view.find(node => node.type === "details" && node.props.className === "suite-rail-details");
  assert.ok(!details.props.open);
  assert.match(text(details), /Lista ordenada/);
  const plan = view.find(node => node.props.className === "panel suite-case-plan suite-flow-lane");
  assert.equal([...elements(plan)].some(node => node.props.className === "section-kicker"), false);
  assert.equal([...elements(plan)].some(node => node.props.className === "suite-print-palette"), false);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("horizontal order controls and removing the active card keep the remaining test and its print plan", async t => {
  const suite = suiteFixture();
  suite.cases.push({ ...suite.cases[0], id: "CT003", title: "Consultar produto" });
  const capture = { version: 1, checkpoints: [{ id: "keep-print", gap: 0, order: 0 }] };
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001", initialCapturePlan: capture });
  await view.chat().props.onSend("Adicione CT003", null); await view.settle();
  await view.click("Mover CT003 para esquerda");
  assert.deepEqual(plannedIds(view), ["CT003", "CT001"]);
  await view.click("Mover CT003 para direita");
  assert.deepEqual(plannedIds(view), ["CT001", "CT003"]);
  await view.click("Ver etapas de CT003: Consultar produto");
  await view.click("Retirar CT003 desta execução");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.button("Ver etapas de CT001: Ordenar produtos").props["aria-pressed"], true);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "keep-print"), true);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("square step cards keep a plus before and after each pause and persist the exact insertion order", async t => {
  const view = await workspace(t, suiteFixture(), undefined, { initialCaseId: "CT001" });
  await view.click("Adicionar print na posição 0");
  await view.click("Adicionar print após pausa db-1");
  assert.equal(view.all().filter(node => node.props["data-checkpoint-id"]).length, 2);
  assert.equal(view.button("Adicionar print após pausa db-2").props["data-insertion-index"], 2);
  assert.equal(view.button("Adicionar print na posição 1").props["data-gap"], 1);
  await view.click("Salvar rascunho");
  const body = view.calls.find(call => call.path.endsWith("/plans")).body;
  assert.deepEqual(body.items[0].capture_plan.checkpoints, [{ id: "db-1", gap: 0, order: 0 }, { id: "db-2", gap: 0, order: 1 }]);
  assert.equal(view.calls.some(call => /\/(start|cases|recordings)$/.test(call.path)), false);
});

test("manual scripts use the same lower rail without pretending to support automatic print pauses", async t => {
  const suite = suiteFixture(); suite.cases[0].mode = "manual";
  suite.cases[0].steps = ["Abrir o formulário", "Preencher dados de teste", "Conferir o registro salvo"];
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  assert.equal(view.all().filter(node => node.props.className === "suite-source-step").length, 3);
  assert.equal(view.all().some(node => node.props["data-gap"] !== undefined), false);
  const details = view.find(node => node.type === "details" && node.props.className === "suite-rail-details");
  assert.ok(!details.props.open); assert.match(text(details), /Roteiro manual.*Conferir o registro salvo/);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("narrow-screen pane switching keeps the editor, conversation, draft message and chosen model", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await view.click("Adicionar teste");
  await view.click("Escrever teste");
  await view.change("Título", "Meu caso em edição");
  await view.change("Nome da execução", "Regressão do carrinho");
  const llm = { provider_id: "fixture", model_id: "test-model", label: "Modelo escolhido" };
  view.chat().props.onModelChange(llm);
  view.chat().props.onValueChange("Mensagem ainda não enviada");
  await view.settle();
  await view.click("Ver assistente de testes");
  assert.equal(view.find(node => node.props.className === "suite-planner-view").props["data-mobile-pane"], "chat");
  assert.equal(view.focus.at(-1).name, "chat-composer");
  assert.equal(view.field("Título").props.value, "Meu caso em edição");
  assert.equal(view.chat().props.value, "Mensagem ainda não enviada");
  assert.deepEqual(view.chat().props.model, llm);
  await view.chat().props.onSend("Adicione CT001", llm);
  await view.settle();
  assert.equal(view.chat().props.messages.length, 2);
  const conversation = structuredClone(view.chat().props.messages);
  await view.click("Ver roteiro da rodada");
  assert.equal(view.field("Título").props.value, "Meu caso em edição");
  assert.equal(view.field("Nome da execução").props.value, "Regressão do carrinho");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  await view.click("Ver assistente de testes");
  assert.deepEqual(view.chat().props.messages, conversation);
  assert.deepEqual(view.chat().props.model, llm);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0, "Direct additions and pane changes never call a model or execute tests");
});

test("creation and review stay inside the work pane and review requires explicit execution consent", async t => {
  const view = await workspace(t, suiteFixture(), undefined, { initialCaseId: "CT001" });
  await view.click("Adicionar teste");
  await view.click("Escrever teste");
  const center = view.find(node => node.props.className === "suite-plan-center");
  assert.equal([...elements(center)].some(node => node.props.id === "suite-case-editor"), true);
  assert.equal([...elements(center)].some(node => node === view.chat()), false);
  await view.click("Fechar editor");
  await view.click("Revisar execução");
  assert.equal(view.focus.at(-1).name, "suite-plan-review");
  assert.equal(view.scroll.at(-1).name, "suite-plan-review");
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, true);
  assert.equal(view.button("Executar 1 teste").props.disabled, true);
  assert.deepEqual(view.calls.filter(call => call.method === "POST").map(call => call.path), ["/test-suites/root-fixture/plans", "/test-suites/rounds/plan-fixture/validate"]);
  assert.deepEqual(view.calls.find(call => call.path.endsWith("/plans")).body.items.map(item => item.case_id), ["CT001"]);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("saving a draft exposes its destination without starting or changing the selected tests", async t => {
  const navigations = [];
  const view = await workspace(t, catalogFixture(), undefined, { initialCaseId: "CT050", navigate: hash => navigations.push(hash) });
  await view.click("Testes"); await markCatalog(view, "CT067"); await view.click("Preparar execução (2)");
  assert.equal(view.all().some(node => node.props.className === "suite-draft-link text-button"), false);
  await view.click("Salvar rascunho");
  assert.match(text(view.find(node => node.props.className === "suite-plan-state")), /Salvo em Rascunhos/);
  assert.deepEqual(plannedIds(view), ["CT050", "CT067"]);
  const before = view.calls.length;
  await view.click("Ver rascunhos");
  assert.deepEqual(navigations, ["#/execucoes-planejadas"]);
  assert.equal(view.calls.length, before);
  assert.deepEqual(view.calls.find(call => call.method === "POST").body.items.map(item => item.case_id), ["CT050", "CT067"]);
  assert.equal(view.calls.some(call => /\/(start|discover|recordings)$/.test(call.path)), false);
});

test("review is a focused stage and returning to the script preserves checkpoints but clears consent", async t => {
  const capture = { version: 1, checkpoints: [{ id: "review-print", gap: 0, order: 0 }] };
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", initialCapturePlan: capture });
  await view.click("Revisar execução");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.all().some(node => node.props.className === "panel suite-case-plan"), false);
  assert.equal(view.all().some(node => node.props.className === "suite-selected-list"), false);
  assert.equal(view.all().some(node => node.type === "button" && text(node) === "Revisar execução"), false);
  const bar = view.find(node => node.type === "header" && node.props.className === "suite-review-bar");
  assert.equal([...elements(bar)].some(node => node.type === "button" && text(node) === "Executar 1 teste"), true);
  assert.equal(view.all().some(node => node.props.className === "suite-plan-footer"), false);
  assert.equal(view.all().some(node => node.props.className === "suite-detail-header"), false);
  assert.equal(view.all().some(node => node.props.className === "suite-plan-heading suite-plan-toolbar"), false);
  const consent = [...elements(view.find(node => node.type === "label" && text(node).startsWith("Revisei os testes")))].find(node => node.type === "input");
  consent.props.onChange({ target: { checked: true } }); await view.settle();
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
  await view.click("Ajustar");
  assert.equal(view.has("suite-plan-review"), false);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "review-print"), true);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.focus.at(-1).name, "suite-planning-heading");
  await view.click("Revisar execução");
  assert.equal(view.button("Executar 1 teste").props.disabled, true);
  assert.equal(view.calls.filter(call => call.path.endsWith("/plans")).length, 1);
  assert.equal(view.calls.filter(call => call.path.endsWith("/validate")).length, 2);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("a new test opened from review returns to editing without dropping the selection or executing", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await view.click("Revisar execução");
  await view.click("Ajustar"); await view.click("Adicionar teste"); await view.click("Escrever teste");
  assert.equal(view.has("suite-plan-review"), false);
  assert.equal(view.has("suite-case-editor"), true);
  assert.equal(view.button("Salvar e adicionar").props.type, "submit");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.calls.some(call => /\/(cases|start)$/.test(call.path)), false);
});

test("review uses all available width for a long exact list, with one execution action and no duplicate headers", async t => {
  const suite = suiteFixture();
  suite.cases = Array.from({ length: 50 }, (_, i) => ({ ...suite.cases[0], id: `CT${String(i + 1).padStart(3, "0")}`, title: `Teste ${i + 1}` }));
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  await view.click("Escolher testes"); await view.click("Adicionar filtrados"); await view.click("Revisar execução");
  assert.equal(view.all().filter(node => node.props.role === "listitem" && node.props.className === "suite-review-case").length, 50);
  assert.equal(view.all().filter(node => node.type === "button" && text(node) === "Executar 50 testes").length, 1);
  assert.equal(view.all().some(node => node.props.className === "suite-planner-layout with-chat"), false);
  assert.equal(view.all().some(node => node.props["aria-label"] === "Etapas de preparação"), false);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("CRUD analysis compares existing and missing tests but additions and proposals still require acceptance", async t => {
  const proposal = { title: "Cadastrar cliente", url: "https://fixture.invalid", description: "Proposta", preconditions: [], steps: ["Cadastrar cliente"], expected_result: "Cliente persistido" };
  const assistantReply = { reply: "Há uma referência de consulta. Revise as lacunas.", selected_case_ids: ["CT001"], new_cases: [proposal], usage: {},
    coverage: { screen: "Clientes", operations: [
      { operation: "read", status: "existing", case_ids: ["CT001"], new_case_indexes: [], reason: "Teste cadastrado para consulta." },
      { operation: "create", status: "gap", case_ids: [], new_case_indexes: [0], reason: "Objetivo não encontrado no catálogo." },
      { operation: "update", status: "uncertain", case_ids: [], new_case_indexes: [], reason: "Confirmar o comportamento." },
      { operation: "delete", status: "not_applicable", case_ids: [], new_case_indexes: [], reason: "O usuário informou que não há exclusão." },
    ] } };
  const view = await workspace(t, undefined, undefined, { assistantReply });
  await view.click("Preparar execução");
  const llm = { provider_id: "fixture", model_id: "planner" };
  await view.chat().props.onSend("Meu teste consiste em testar o CRUD da tela Clientes", llm); await view.settle();
  const coverage = view.find(node => node.props["aria-label"] === "Comparação do CRUD com a base");
  assert.match(text(coverage), /CRUD · Clientes.*Já existe.*Não encontrado.*Precisa conferir.*Não se aplica/);
  assert.deepEqual(plannedIds(view), []);
  const writes = view.calls.filter(call => call.method !== "GET");
  assert.equal(writes.length, 1); assert.ok(writes[0].path.endsWith("/assistant"));
  await view.click("Incluir na execução: 1");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  await view.click("Revisar: Cadastrar cliente");
  assert.equal(view.field("Título").props.value, proposal.title);
  assert.equal(view.calls.some(call => /\/(cases|start)$/.test(call.path)), false);
  await view.submit();
  assert.equal(view.all().some(node => node.props["aria-label"] === "Comparação do CRUD com a base"), false, "Do not retain dangling indexes after saving a proposal");
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("a failed draft save keeps the selection and never advertises a successful save", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", rejectWrite: () => true });
  await view.click("Salvar rascunho");
  assert.match(text(view.find(node => node.props.role === "alert")), /alterado em outra tela/);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.all().some(node => node.props.className === "suite-draft-link text-button"), false);
  assert.match(text(view.find(node => node.props.className === "suite-plan-state")), /Não salvo/);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("compact creation choices apply to both maintenance and preparation without hiding recording consent", async t => {
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.suite-planner-view \.suite-plan-center:has\(\.suite-case-form\) \.suite-mode-cards\s*\{\s*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/);
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await view.click("Adicionar teste"); await view.click("Escrever teste");
  await view.change("Título", "Minha proposta");
  await view.click("Gravar minha navegação");
  assert.equal(view.has("suite-case-editor"), false);
  assert.equal(view.has("suite-record-confirmation"), true);
  assert.match(text(view.find(node => node.props.id === "suite-record-confirmation")), /podem alterar dados.*não uma automação pronta/);
  await view.click("Escrever teste");
  assert.equal(view.field("Título").props.value, "Minha proposta");
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("planner responsive styles reserve space for the composer and use pane tabs instead of stacking chat below", async () => {
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.app-main:has\(\.suite-planner-view\)\s*\{[^}]*height: 100dvh;[^}]*min-height: 0;[^}]*overflow: hidden;/);
  assert.match(css, /\.suite-planner-view\s*\{[^}]*container: suite-planner \/ inline-size;[^}]*min-height: 0;/);
  assert.match(css, /\.suite-planner-view \.suite-planner-layout.with-chat\s*\{\s*grid-template-columns: minmax\(0, 1fr\) clamp\(320px, 30%, 420px\);/);
  assert.match(css, /\.suite-planning-main > \.suite-plan-footer\s*\{[^}]*grid-area: footer;[^}]*position: static;/);
  assert.match(css, /\.suite-planner-view \.suite-plan-center\s*\{[^}]*min-height: 0;[^}]*overflow: auto;/);
  assert.match(css, /@container suite-planner \(max-width: 830px\)/);
  assert.match(css, /data-mobile-pane="plan"[^}]*\.suite-chat\s*\{\s*display: none;/);
  assert.match(css, /data-mobile-pane="chat"[^}]*\.suite-planning-main\s*\{\s*display: none;/);
});

test("catalog archiving is explicit, clears only the archived selection and preserves visible result navigation", async t => {
  const reports = [];
  const view = await workspace(t, catalogFixture(), async (...args) => reports.push(args));
  await markCatalog(view, "CT050"); await markCatalog(view, "CT067");
  await view.click("Gerenciar");
  await view.click("Arquivar CT050");
  assert.equal(view.calls.filter(call => call.method === "PATCH").length, 0, "Opening confirmation never archives");
  assert.equal(view.focus.at(-1).name, "Confirmar arquivamento");
  await view.click("Cancelar");
  assert.equal(view.focus.at(-1).name, "Arquivar CT050");
  await view.click("Arquivar CT050");
  await view.click("Confirmar arquivamento");
  assert.equal(view.all().some(node => node.props["aria-label"] === "Selecionar CT050"), false);
  assert.equal(view.find(node => node.props["aria-label"] === "Selecionar CT067").props.checked, true);
  assert.match(text(selectionBar(view)), /1 teste selecionadoCT067/);
  view.find(node => node.type === "select" && node.props["aria-label"] === "Filtrar CTs").props.onChange({ target: { value: "archived" } });
  await view.settle();
  assert.equal(view.find(node => node.props["aria-label"] === "Selecionar CT050").props.disabled, true);
  await view.click("Ver resultado de CT050: Carrinho · CT050");
  assert.deepEqual(reports, [["root-fixture", "consolidated", undefined, "CT050"]]);
  await view.click("Restaurar CT050");
  assert.equal(view.all().some(node => node.props["aria-label"] === "Selecionar CT050"), false, "Restored test leaves the archive filter");
  assert.deepEqual(view.calls.filter(call => call.method === "PATCH").map(call => call.body), [{ archived: true, expected_revision: 0 }, { archived: false, expected_revision: 1 }]);
  assert.equal(view.calls.some(call => /\/start|\/result|\/report/.test(call.path)), false);
});

test("archived tests cannot be added by direct chat and active tests remain available", async t => {
  const suite = catalogFixture(); suite.cases[0].archived = true;
  const view = await workspace(t, suite);
  await view.click("Preparar execução");
  await view.chat().props.onSend("adicione CT050", null); await view.settle();
  assert.match(view.chat().props.messages.at(-1).content, /Não adicionei/);
  assert.deepEqual(plannedIds(view), []);
  await view.chat().props.onSend("adicione CT067", null); await view.settle();
  assert.deepEqual(plannedIds(view), ["CT067"]);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("execution history includes original and linked attempts, excludes drafts and keeps long results collapsed", async t => {
  const suite = suiteFixture(), navigations = [];
  const execution = { id: "round-fixture", root_id: suite.id, name: "Conferir produtos", status: "completed", revision: 2, created_at: "2026-10-20T12:00:00Z", updated_at: "2026-10-20T12:01:00Z", items: [{ case_id: "CT001", mode: "automatic", definition: suite.cases[0], state: "completed", execution_id: "run-fixture", result: { status: "PASS", actual_result: "Lista e opções conferidas" } }] };
  suite.rounds = [execution, { ...execution, id: "draft-fixture", name: "Não executado", status: "draft" }];
  suite.legacy_runs = [{ id: "legacy-fixture", count: 1, status: "completed", created_at: "2026-10-19T12:00:00Z" }];
  const view = await workspace(t, suite, undefined, { roundId: execution.id, navigate: hash => navigations.push(hash) });
  const history = view.find(node => node.props["aria-label"] === "Execuções, mais recentes primeiro");
  assert.deepEqual([...elements(history)].filter(node => node.type === "button").map(button => text([...elements(button)].find(node => node.type === "strong"))), ["Conferir produtos", "Reexecução", "Execução inicial"]);
  assert.equal([...elements(history)].some(node => text(node).includes("Não executado")), false);
  assert.match(text(view.find(node => node.props.className === "panel suite-round-detail")), /1 de 1 teste com resultado/);
  const explanation = view.find(node => node.type === "details" && node.props.className === "suite-result-explanation");
  assert.equal(explanation.props.open, undefined);
  assert.match(text(explanation), /Lista e opções conferidas/);
  assert.equal(view.all().some(node => node.props.className === "suite-comparison"), false);
  await view.click("Ver execução");
  assert.deepEqual(navigations, ["#/exploracoes/run-fixture/acompanhar"]);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("manual editor shows essentials first, retaining the advanced fields without saving or executing", async t => {
  const view = await workspace(t);
  await view.click("Adicionar teste"); await view.click("Escrever teste");
  const editor = view.find(node => node.props.id === "suite-case-editor");
  const advanced = [...elements(editor)].find(node => node.props.className === "suite-case-advanced");
  assert.equal(advanced.props.open, undefined);
  assert.match(text(advanced), /URL da aplicação.*Descrição.*Pré-condições/);
  assert.equal(view.field("URL da aplicação").props.value, "https://fixture.invalid");
  await view.change("Título", "Novo caso"); await view.change("Passos", "Abrir página\nConferir resultado");
  await view.change("Resultado esperado", "Resultado visível");
  assert.equal(view.field("Título").props.value, "Novo caso");
  assert.equal(view.field("Resultado esperado").props.value, "Resultado visível");
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

async function fillManual(view, title = "Conferir carrinho") {
  await view.change("Título", title);
  await view.change("Passos", "Abrir carrinho\nConferir mensagem");
  await view.change("Resultado esperado", "Mensagem de carrinho vazio");
}

test("base creation and explicit execution inclusion have different save destinations", async t => {
  const base = await workspace(t);
  await base.click("Adicionar teste"); await base.click("Escrever teste"); await fillManual(base);
  assert.equal(base.chat().props.purpose, "base");
  await base.submit();
  assert.equal(base.find(node => node.props["aria-label"] === "Selecionar CT002").props.checked, false);
  assert.equal(base.calls.filter(call => call.method !== "GET").length, 1);
  assert.equal(base.calls[1].path, "/test-suites/root-fixture/cases");

  const include = await workspace(t);
  await include.click("Adicionar teste"); await include.click("Escrever teste"); await fillManual(include);
  await include.click("Salvar e incluir na execução");
  assert.deepEqual(plannedIds(include), ["CT002"]);
  assert.equal(include.chat().props.purpose, "execution");
  assert.equal(include.calls.filter(call => call.method !== "GET").length, 1);

  const planner = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await planner.click("Adicionar teste"); await planner.click("Escrever teste"); await fillManual(planner);
  assert.equal(planner.button("Salvar e adicionar").props.type, "submit");
  await planner.submit();
  assert.deepEqual(plannedIds(planner), ["CT001", "CT002"]);
  assert.equal(planner.calls.some(call => /\/(plans|start|discover)$/.test(call.path)), false);
});

test("manual version edits preserve the ID and cannot inherit approval or alter old evidence", async t => {
  const suite = suiteFixture();
  suite.cases[0] = { ...suite.cases[0], mode: "manual", revision: 3, recorded_at: "2026-10-14T16:22:00Z" };
  suite.rounds = [{ id: "historical", root_id: suite.id, name: "Histórico", status: "completed", created_at: "2026-10-14T16:22:00Z", items: [{ case_id: "CT001", result: { status: "PASS", evidence: "preserved.png" } }] }];
  const historical = structuredClone(suite.rounds);
  const view = await workspace(t, suite);
  await view.click("Editar"); await fillManual(view, "Nova definição"); await view.submit();
  const write = view.calls.find(call => call.method === "PUT");
  assert.equal(write.path, "/test-suites/root-fixture/cases/CT001");
  assert.equal(write.body.expected_revision, 3);
  assert.equal(suite.cases[0].revision, 4);
  assert.equal(view.button("Ver resultado de CT001: Nova definição").props.disabled, true);
  assert.deepEqual(suite.rounds, historical);
  assert.equal(view.calls.some(call => /\/(plans|start)$/.test(call.path)), false);
});

test("editing a restored manual case retains its independent catalog revision for archiving", async t => {
  const suite = suiteFixture();
  suite.cases[0] = { ...suite.cases[0], mode: "manual", archived: false, catalog_revision: 4 };
  const view = await workspace(t, suite);
  await view.click("Editar"); await fillManual(view); await view.submit();
  await view.click("Gerenciar"); await view.click("Arquivar CT001"); await view.click("Confirmar arquivamento");
  assert.equal(view.calls.find(call => call.method === "PATCH").body.expected_revision, 4);
  assert.equal(suite.cases[0].archived, true);
});

test("unsaved manual text blocks navigation and reload, then saves without executing before leaving", async t => {
  const navigations = [];
  const view = await workspace(t, undefined, undefined, { navigate: hash => navigations.push(hash) });
  await view.click("Adicionar teste"); await view.click("Escrever teste"); await fillManual(view);
  assert.equal(view.unloadBlocked(), true);
  assert.equal(await view.navigate("#/execucoes-planejadas"), false);
  await view.click("Continuar editando");
  assert.equal(view.field("Título").props.value, "Conferir carrinho");
  assert.equal(await view.navigate("#/execucoes-planejadas"), false);
  await view.click("Salvar e continuar");
  assert.deepEqual(navigations, ["#/execucoes-planejadas"]);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 1);
  assert.equal(view.calls.some(call => /\/(plans|start)$/.test(call.path)), false);
});

test("leaving a changed selection saves only the exact draft and never starts it", async t => {
  const navigations = [];
  const view = await workspace(t, catalogFixture(), undefined, { navigate: hash => navigations.push(hash) });
  await markCatalog(view, "CT050"); await markCatalog(view, "CT067");
  assert.equal(await view.navigate("#/execucoes-planejadas"), false);
  await view.click("Salvar e continuar");
  assert.deepEqual(navigations, ["#/execucoes-planejadas"]);
  const writes = view.calls.filter(call => call.method !== "GET");
  assert.equal(writes.length, 1);
  assert.deepEqual(writes[0].body.items.map(item => item.case_id), ["CT050", "CT067"]);
  assert.equal(writes[0].path, "/test-suites/root-fixture/plans");
});

test("an unsaved new case must be resolved before the existing selection can be reviewed", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await view.click("Adicionar teste"); await view.click("Escrever teste"); await fillManual(view);
  assert.equal(view.button("Revisar execução").props.disabled, true);
  assert.equal(view.button("Salvar rascunho").props.disabled, true);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
  await view.click("Fechar editor"); await view.click("Descartar e sair");
  assert.equal(view.button("Revisar execução").props.disabled, false);
  assert.deepEqual(plannedIds(view), ["CT001"]);
});

test("an incomplete form and a stale version preserve the user's text and never navigate or overwrite", async t => {
  const view = await workspace(t);
  await view.click("Adicionar teste"); await view.click("Escrever teste"); await view.change("Título", "Incompleto");
  await view.navigate("#/execucoes-planejadas"); await view.click("Salvar e continuar");
  assert.equal(view.field("Título").props.value, "Incompleto");
  assert.match(text(view.find(node => node.props.role === "alert")), /Complete título/);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);

  const suite = suiteFixture(); suite.cases[0].mode = "manual";
  const edit = await workspace(t, suite, undefined, { rejectWrite: () => true });
  await edit.click("Editar"); await fillManual(edit, "Minha edição"); await edit.submit();
  assert.equal(edit.field("Título").props.value, "Minha edição");
  assert.equal(suite.cases[0].title, "Ordenar produtos");
  assert.match(text(edit.find(node => node.props.role === "alert")), /alterado em outra tela.*alterações foram mantidas/);
});

test("empty bases, archived-only bases and empty filters explain different next actions", async t => {
  const emptySuite = suiteFixture(); emptySuite.cases = [];
  const empty = await workspace(t, emptySuite);
  assert.match(text(empty.find(node => node.props.className === "suite-catalog-empty")), /Sua base ainda não tem testes/);
  assert.ok(empty.button("Adicionar teste"));
  const archivedSuite = suiteFixture(); archivedSuite.cases[0].archived = true;
  const archived = await workspace(t, archivedSuite);
  assert.match(text(archived.find(node => node.props.className === "suite-catalog-empty")), /Todos os testes estão arquivados.*resultados continuam no histórico/);
  await archived.click("Ver arquivados");
  assert.ok(archived.button("Restaurar CT001"));
  const filtered = await workspace(t);
  filtered.find(node => node.props["aria-label"] === "Buscar CTs").props.onChange({ target: { value: "nada-corresponde" } });
  await filtered.settle();
  assert.match(text(filtered.find(node => node.props.className === "suite-catalog-empty")), /Nenhum teste neste filtro/);
  await filtered.click("Ver todos os testes");
  assert.ok(filtered.button("Ver resultado de CT001: Ordenar produtos"));
});

test("a failed initial load offers a read-only retry instead of stranding the user", async t => {
  let failed = true;
  const reads = [];
  const view = await workspace(t, undefined, undefined, { request: async (path, options) => {
    reads.push({ path, options });
    if (failed) throw new Error("Falha de rede simulada");
    return suiteFixture();
  } });
  assert.match(text(view.find(node => node.props.role === "alert")), /Falha de rede/);
  failed = false;
  await view.click("Tentar novamente");
  assert.ok(view.button("Ver resultado de CT001: Ordenar produtos"));
  assert.equal(reads.length, 2);
  assert.ok(reads.every(call => call.options === undefined));
});

test("unknown references in a resumed selection are unavailable, not falsely described as archived", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT999" });
  assert.match(text(view.find(node => node.props.className === "panel suite-archived-selection")), /CT999 não está disponível nesta base/);
  assert.equal(view.button("Revisar execução").props.disabled, true);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("an archived test in a saved selection must be removed or restored before review", async t => {
  const suite = suiteFixture(); suite.cases[0].archived = true;
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  const notice = view.find(node => node.props.className === "panel suite-archived-selection");
  assert.match(text(notice), /CT001 está arquivado/);
  assert.equal(view.button("Revisar execução").props.disabled, true);
  await view.click("Retirar indisponíveis desta execução");
  assert.deepEqual(plannedIds(view), []);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});
