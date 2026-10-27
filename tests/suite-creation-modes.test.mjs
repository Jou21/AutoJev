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
for (const name of ["suite-planning-chat", "suite-capture-plan", "suite-execution-history", "suite-horizontal-rail", "suite-step-evidence", "functional-flows"]) {
  const compiledModule = { exports: {} };
  new Function("module", "exports", await compiled(`../app/components/${name}.ts`))(compiledModule, compiledModule.exports);
  dependencies[`./${name}`] = compiledModule.exports;
}
const flowUiModule = { exports: {} };
new Function("require", "module", "exports", await compiled("../app/components/FunctionalFlows.tsx"))(
  name => name.endsWith(".css") ? {} : dependencies[name] || nativeRequire(name), flowUiModule, flowUiModule.exports);
dependencies["./FunctionalFlows"] = flowUiModule.exports;
const tooltipModule = { exports: {} };
new Function("require", "module", "exports", await compiled("../app/components/ActionTooltip.tsx"))(
  name => name.endsWith(".css") ? {} : nativeRequire(name), tooltipModule, tooltipModule.exports);
dependencies["./ActionTooltip"] = tooltipModule.exports;
const overlayModule = { exports: {} };
new Function("require", "module", "exports", await compiled("../app/components/StepDragOverlay.tsx"))(
  name => nativeRequire(name), overlayModule, overlayModule.exports);
dependencies["./StepDragOverlay"] = overlayModule.exports;
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
  yield* elements(node.props.creationActions);
}

async function workspace(t, providedSuite, onReport, initialProps = {}) {
  const slots = [], effects = [], calls = [], focus = [], scroll = [];
  const domRefs = new Set();
  const listeners = new Map();
  const intervals = new Map(); let intervalId = 0;
  const browser = { setInterval(callback) { intervals.set(++intervalId, callback); return intervalId; }, clearInterval(id) { intervals.delete(id); },
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
  function ImagePreviewFixture() {}
  let savedPlan;
  const compiledModule = { exports: {} };
  new Function("require", "module", "exports", "window", source)(name => name === "react" ? hooks
    : name === "./PlanningChat" ? { __esModule: true, default: ChatFixture }
      : name === "./StepEvidencePreview" ? { __esModule: true, default: ImagePreviewFixture }
      : name.endsWith(".css") ? {} : dependencies[name] || nativeRequire(name), compiledModule, compiledModule.exports,
  browser);
  const request = async (path, options = {}) => {
    calls.push({ path, method: options.method || "GET", body: options.body && JSON.parse(options.body) });
    if (options.method && initialProps.rejectWrite?.(path, options)) throw new Error("O CT foi alterado em outra tela. Atualize sua versão.");
    if (path === "/test-suites/root-fixture" && !options.method) return structuredClone(suite);
    if (path === "/test-suites/old-run/case-context/CT009" && !options.method && initialProps.caseContext) return structuredClone(initialProps.caseContext);
    if (path.startsWith("/test-suites/root-fixture/cases/") && path.endsWith("/catalog-state") && options.method === "PATCH") {
      const item = suite.cases.find(item => path === `/test-suites/root-fixture/cases/${item.id}/catalog-state`);
      assert.ok(item);
      const body = JSON.parse(options.body);
      assert.equal(body.expected_revision, item.catalog_revision || 0);
      item.archived = body.archived; item.catalog_revision = (item.catalog_revision || 0) + 1;
      return { id: item.id, archived: item.archived, catalog_revision: item.catalog_revision };
    }
    if (path === "/test-suites" && !options.method) return structuredClone(initialProps.groupsReply || [{ ...suite, created_at: suite.created_at || "2026-10-14T16:22:00Z", initial_status: "completed", legacy_runs: suite.legacy_runs || [] }]);
    if (path.startsWith("/test-suites/rounds/") && !options.method) {
      const found = suite.rounds.find(round => path === "/test-suites/rounds/" + round.id);
      if (found) return structuredClone(found);
    }
    if (path === "/test-suites/root-fixture/plans" && options.method === "POST") {
      savedPlan = { ...JSON.parse(options.body), id: "plan-fixture", root_id: suite.id, status: "draft", revision: 1 };
      return structuredClone(savedPlan);
    }
    if (path === "/test-suites/root-fixture/plans/plan-fixture" && options.method === "PUT") {
      savedPlan ||= structuredClone(suite.rounds.find(round => round.id === "plan-fixture"));
      assert.ok(savedPlan);
      const { expected_revision, ...body } = JSON.parse(options.body);
      assert.equal(expected_revision, savedPlan.revision);
      savedPlan = { ...savedPlan, ...body, revision: savedPlan.revision + 1 };
      savedPlan = initialProps.planUpdate?.(savedPlan) || savedPlan;
      return structuredClone(savedPlan);
    }
    if (path === "/test-suites/rounds/plan-fixture/validate" && options.method === "POST") {
      savedPlan ||= structuredClone(suite.rounds.find(round => round.id === "plan-fixture"));
      const checked = { revision: savedPlan.revision, available: true, effects: ["Somente os CTs selecionados serão executados após confirmação."],
        items: savedPlan.items.map(item => ({ case_id: item.case_id, available: true, code: "automatic" })) };
      return initialProps.validationReply?.(checked) || checked;
    }
    if (path === "/test-suites/rounds/plan-fixture/start" && options.method === "POST") {
      assert.deepEqual(JSON.parse(options.body), { confirm: true, expected_revision: savedPlan.revision });
      return { ...structuredClone(savedPlan), status: "running" };
    }
    if (path === "/test-suites/root-fixture/recordings" && options.method === "POST") return { id: "rec-fixture", steps: [], status: "recording", url: suite.app_url };
    if (path === "/test-suites/recordings/rec-fixture/state" && !options.method) return { id: "rec-fixture", url: suite.app_url, ...initialProps.recordingStateReply };
    if (path === "/test-suites/recordings/rec-fixture/finish" && options.method === "POST") return { id: "rec-fixture", steps: ["Abrir carrinho"], status: "completed", url: suite.app_url };
    if (path === "/test-suites/root-fixture/assistant" && options.method === "POST") return typeof initialProps.assistantReply === "function" ? initialProps.assistantReply(JSON.parse(options.body)) : initialProps.assistantReply || { reply: "Revise o rascunho.", selected_case_ids: [], new_cases: [{ title: "Carrinho vazio", url: suite.app_url, description: "Proposta", preconditions: [], steps: ["Abrir carrinho"], expected_result: "Vazio" }], usage: {} };
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
  const props = { rootId: suite.id, apiBase: "http://api.fixture.invalid/api", request, navigate() { assert.fail("No navigation expected"); }, openReport: onReport || (() => { assert.fail("No export expected"); }), ...initialProps };
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
  return { all, find, button, field, click, change, settle, calls, focus, scroll, chat: () => find(node => node.type === ChatFixture), image: () => find(node => node.type === ImagePreviewFixture),
    async tick() { for (const callback of [...intervals.values()]) await callback(); await settle(); },
    async windowEvent(type, event) { for (const callback of listeners.get(type) || []) callback(event); await settle(); },
    async navigate(hash) { const event = { detail: { hash }, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; for (const callback of listeners.get("autojev:before-navigate") || []) callback(event); await settle(); return !event.defaultPrevented; },
    unloadBlocked() { const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; for (const callback of listeners.get("beforeunload") || []) callback(event); return event.defaultPrevented; },
    has: id => all().some(node => node.props.id === id), submit: async () => { find(node => node.type === "form" && node.props.id === "suite-case-editor").props.onSubmit({ preventDefault() {} }); await settle(); } };
}

test("Add starts a local conversation with only two creation cards inside the chat", async t => {
  const view = await workspace(t);
  await view.click("Adicionar teste");
  assert.equal(view.focus.at(-1).name, "chat-composer");
  assert.deepEqual(view.chat().props.messages, [{ role: "assistant", kind: "creation_prompt", content: "Como você gostaria de realizar esse teste?" }]);
  const actions = [...elements(view.chat().props.creationActions)].filter(node => node.type === "button");
  assert.equal(actions.length, 2);
  assert.match(text(actions[0]), /^Gravar minha navegação/);
  assert.match(text(actions[1]), /^Planejar com IA/);
  assert.equal(view.all().some(node => /suite-new-modes|suite-mode-cards/.test(node.props.className || "")), false);
  assert.equal(view.all().some(node => text(node) === "Escrever teste"), false);
  assert.equal(view.has("suite-case-editor"), false);
  assert.equal(view.has("suite-record-confirmation"), false);
  assert.equal(view.find(node => node.props.className?.includes("suite-maintenance-view")).props["data-chat-only"], true);
  assert.equal(view.chat().props.purpose, "base");
  const attentionKey = view.chat().props.creationActions.key;
  view.chat().props.onValueChange("Mensagem ainda não enviada"); await view.settle();
  assert.equal(view.chat().props.creationActions.key, attentionKey, "Typing does not restart the highlight");
  await view.click("Adicionar teste");
  assert.notEqual(view.chat().props.creationActions.key, attentionKey, "Each explicit Add restarts both cards' finite highlight");
  assert.equal(view.chat().props.value, "Mensagem ainda não enviada");
  assert.equal(view.chat().props.messages.length, 1, "Repeated Add reveals the current question without duplicates");
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("base cards have one native full-card opening action and a separate history expander", async t => {
  const suite = { ...suiteFixture(), case_count: 1, active_count: 0, latest_activity: "2026-10-22T18:00:00Z" };
  const navigations = [];
  const view = await workspace(t, suite, undefined, { rootId: undefined, navigate: hash => navigations.push(hash) });
  const card = view.find(node => node.props.className === "suite-group-main");
  const actions = [...elements(card)].filter(node => node.type === "button");
  assert.equal(actions.length, 2, "Only opening the base and expanding history are separate focus targets");
  const open = actions.find(node => node.props.className.includes("suite-group-open"));
  assert.equal(open.props.type, "button");
  assert.match(open.props["aria-label"], /^Ver testes de fixture\.invalid, criado em /);
  assert.equal([...elements(card)].filter(node => node.props.className === "suite-group-name")[0].props.children[0].type, "strong");
  open.props.onClick(); await view.settle();
  assert.deepEqual(navigations, ["#/conjuntos/root-fixture"]);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.suite-group-main\s*\{[^}]*position: relative;/);
  assert.match(css, /\.suite-group-open::after\s*\{[^}]*position: absolute;[^}]*inset: 0;[^}]*z-index: 1;/);
  assert.match(css, /\.suite-workspace \.suite-group-open:active:not\(:disabled\)\s*\{[^}]*transform: none;/, "Pressing must not shrink the full-card hit area to the button");
  assert.match(css, /\.suite-expand\s*\{[^}]*position: relative;[^}]*z-index: 2;/);
  assert.match(css, /\.suite-group-open:focus-visible::after\s*\{[^}]*outline: 2px solid/);
});

test("expanding a base never opens it and history entries retain their own destinations", async t => {
  const suite = { ...suiteFixture(), case_count: 1, active_count: 0, created_at: "2026-10-14T16:22:00Z", latest_activity: "2026-10-22T18:00:00Z",
    legacy_runs: [{ id: "linked-fixture", count: 1, status: "completed", created_at: "2026-10-22T18:00:00Z" }] };
  const navigations = [];
  const view = await workspace(t, suite, undefined, { rootId: undefined, navigate: hash => navigations.push(hash) });
  view.find(node => node.props.className === "suite-expand").props.onClick(); await view.settle();
  assert.equal(view.find(node => node.props.className === "suite-expand").props["aria-expanded"], true);
  assert.deepEqual(navigations, []);
  view.find(node => node.props["data-history-id"] === "linked-fixture").props.onClick(); await view.settle();
  assert.deepEqual(navigations, ["#/exploracoes/linked-fixture"]);
  view.find(node => node.props.className === "suite-expand").props.onClick(); await view.settle();
  assert.equal(view.all().some(node => node.props["data-history-id"]), false);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

function baseGroupsFixture(count = 45) {
  const suite = suiteFixture();
  return Array.from({ length: count }, (_, index) => ({ ...suite, id: `root-${String(index + 1).padStart(3, "0")}`,
    app_url: `https://app-${String(index + 1).padStart(3, "0")}.fixture.invalid`, case_count: 1,
    active_count: index < 25 ? 1 : 0, initial_status: "completed", legacy_runs: [],
    created_at: "2026-10-01T12:00:00Z", latest_activity: new Date(Date.UTC(2026, 9, 22, 12, 0, -index)).toISOString(),
  })).reverse();
}
const visibleBaseIds = view => view.all().filter(node => node.props.className === "suite-group-name").map(node => node.props.children[0].props.title.replace("Grupo ", ""));
const basePagination = view => view.find(node => node.props.className === "suite-pagination");
async function searchBases(view, value) {
  view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar bases de testes").props.onChange({ target: { value } });
  await view.settle();
}

test("Nova exploração on Meus testes opens the creation screen directly without creating anything", async t => {
  const navigations = [];
  const view = await workspace(t, undefined, undefined, { rootId: undefined, navigate: hash => navigations.push(hash) });
  assert.equal(view.all().some(node => node.type === "button" && text(node) === "Nova base de testes"), false);
  await view.click("Nova exploração");
  assert.deepEqual(navigations, ["#/nova-exploracao"]);
  assert.equal(view.all().some(node => node.props["aria-label"] === "Criar outra base"), false);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("base pagination shows 20 per page, preserves newest-first order and disables its boundaries", async t => {
  const groups = baseGroupsFixture(), before = structuredClone(groups);
  const navigations = [];
  const view = await workspace(t, undefined, undefined, { rootId: undefined, groupsReply: groups, navigate: hash => navigations.push(hash) });
  assert.deepEqual(visibleBaseIds(view), Array.from({ length: 20 }, (_, i) => `root-${String(i + 1).padStart(3, "0")}`));
  assert.match(text(basePagination(view)), /^1–20 de 45/);
  assert.equal(view.button("Página anterior").props.disabled, true);
  assert.equal(view.button("Próxima página").props.disabled, false);
  assert.equal(view.button("Próxima página").props["aria-controls"], "suite-group-list");
  await view.click("Próxima página");
  assert.equal(visibleBaseIds(view).length, 20);
  assert.deepEqual([visibleBaseIds(view)[0], visibleBaseIds(view).at(-1)], ["root-021", "root-040"]);
  assert.match(text(basePagination(view)), /^21–40 de 45.*Página 2 de 3/);
  const open = view.find(node => node.props.className?.includes("suite-group-open"));
  open.props.onClick(); await view.settle();
  assert.deepEqual(navigations, ["#/conjuntos/root-021"]);
  await view.click("Próxima página");
  assert.deepEqual(visibleBaseIds(view), ["root-041", "root-042", "root-043", "root-044", "root-045"]);
  assert.match(text(basePagination(view)), /^41–45 de 45/);
  assert.equal(view.button("Próxima página").props.disabled, true);
  await view.click("Página anterior");
  assert.match(text(basePagination(view)), /Página 2 de 3/);
  assert.equal(view.calls.length, 1, "Changing pages reuses the already loaded bases");
  assert.deepEqual(groups, before);
});

test("search and active filters apply to all bases and start again on the first result page", async t => {
  const view = await workspace(t, undefined, undefined, { rootId: undefined, groupsReply: baseGroupsFixture() });
  await view.click("Próxima página");
  await searchBases(view, "app-045.");
  assert.deepEqual(visibleBaseIds(view), ["root-045"], "Search includes a base beyond the page being viewed");
  assert.equal(view.all().some(node => node.props.className === "suite-pagination"), false);
  await searchBases(view, "");
  assert.match(text(basePagination(view)), /Página 1 de 3/);
  await view.click("Próxima página"); await view.click("Em andamento");
  assert.match(text(basePagination(view)), /^1–20 de 25.*Página 1 de 2/);
  await view.click("Próxima página");
  assert.deepEqual(visibleBaseIds(view), ["root-021", "root-022", "root-023", "root-024", "root-025"]);
  await view.click("Todos");
  assert.match(text(basePagination(view)), /^1–20 de 45.*Página 1 de 3/);
  await searchBases(view, "not-found");
  assert.equal(visibleBaseIds(view).length, 0);
  assert.equal(view.all().some(node => node.props.className === "suite-pagination"), false);
  await view.click("Limpar filtros");
  assert.match(text(basePagination(view)), /^1–20 de 45/);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("refresh clamps removed last pages and small or empty lists need no pagination controls", async t => {
  const groups = baseGroupsFixture();
  const view = await workspace(t, undefined, undefined, { rootId: undefined, groupsReply: groups });
  await view.click("Próxima página"); await view.click("Próxima página");
  groups.splice(0, 24); // Keep the newest 21 fixtures; no live data is involved.
  await view.click("Atualizar");
  assert.deepEqual(visibleBaseIds(view), ["root-021"]);
  assert.match(text(basePagination(view)), /^21–21 de 21.*Página 2 de 2/);
  assert.equal(view.button("Próxima página").props.disabled, true);
  groups.splice(0, 1);
  await view.click("Atualizar");
  assert.equal(visibleBaseIds(view).length, 20);
  assert.equal(visibleBaseIds(view)[0], "root-001");
  assert.equal(view.all().some(node => node.props.className === "suite-pagination"), false);
  groups.splice(0);
  await view.click("Atualizar");
  assert.equal(visibleBaseIds(view).length, 0);
  assert.equal(view.all().some(node => node.props.className === "suite-pagination"), false);
  assert.equal(view.calls.every(call => call.method === "GET"), true);
});

test("AI choice asks for the screen and goal, preserves the composer and model, and sends nothing automatically", async t => {
  const view = await workspace(t);
  await view.click("Adicionar teste");
  const llm = { provider_id: "fixture", model_id: "chosen-model" };
  view.chat().props.onModelChange(llm);
  view.chat().props.onValueChange("Meu objetivo ainda não enviado");
  await view.settle();
  await view.click("Planejar com IA");
  assert.equal(view.focus.at(-1).name, "chat-composer");
  assert.equal(view.scroll.at(-1).name, "chat-composer");
  assert.equal(view.chat().props.value, "Meu objetivo ainda não enviado");
  assert.deepEqual(view.chat().props.model, llm);
  assert.equal(view.chat().props.messages.at(-2).content, "Planejar com IA");
  assert.match(view.chat().props.messages.at(-1).content, /Qual tela.*CRUD.*comparar.*base/i);
  assert.equal(view.chat().props.creationActions, false);
  const conversation = structuredClone(view.chat().props.messages);
  await view.click("Adicionar teste");
  assert.deepEqual(view.chat().props.messages.slice(0, -1), conversation);
  await view.click("Planejar com IA");
  assert.equal(view.focus.at(-1).name, "chat-composer");
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("Add protects an edited draft before replacing its editor with the chat choices", async t => {
  const view = await workspace(t);
  await view.click("Duplicar CT001 como novo CT manual");
  await view.change("Título", "Meu roteiro não salvo");
  await view.click("Adicionar teste");
  assert.ok(view.find(node => node.props.role === "dialog"));
  await view.click("Continuar editando");
  assert.equal(view.field("Título").props.value, "Meu roteiro não salvo");
  await view.click("Adicionar teste"); await view.click("Descartar e sair");
  assert.equal(view.has("suite-case-editor"), false);
  assert.equal(view.chat().props.messages.at(-1).kind, "creation_prompt");
  assert.equal(view.find(node => node.props.className?.includes("suite-maintenance-view")).props["data-chat-only"], true);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("reopening creation after direct chat keeps the selected order, print pauses and conversation", async t => {
  const suite = suiteFixture();
  suite.cases.push({ ...suite.cases[0], id: "CT002", title: "Consultar carrinho" });
  const capture = { version: 1, checkpoints: [{ id: "preserved-print", gap: 0, order: 0 }] };
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001", initialCapturePlan: capture });
  await view.click("Adicionar teste");
  await view.chat().props.onSend("Adicione CT002", null); await view.settle();
  assert.equal(view.chat().props.creationActions, false, "A typed answer dismisses the choices without requiring a card");
  const messages = structuredClone(view.chat().props.messages);
  await view.click("Adicionar teste");
  assert.deepEqual(view.chat().props.messages.slice(0, -1), messages);
  assert.deepEqual(plannedIds(view), ["CT001", "CT002"]);
  assert.ok(view.all().some(node => node.props["data-checkpoint-id"] === "preserved-print"));
  assert.equal(view.find(node => node.props["aria-label"] === "Planejamento da rodada").props["data-mobile-pane"], "chat");
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("recording consent and status stay inside chat and finishing opens a review without saving", async t => {
  const view = await workspace(t);
  await view.click("Adicionar teste");
  await view.click("Gravar minha navegação");
  assert.equal(view.has("suite-record-confirmation"), true);
  assert.equal([...elements(view.chat().props.children)].some(node => node.props.id === "suite-record-confirmation"), true);
  assert.match(view.focus.at(-1).name, /Confirmar e abrir navegador/);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
  await view.click("Confirmar e abrir navegador de gravação");
  assert.equal(view.has("suite-recording"), true);
  assert.equal(view.has("suite-record-confirmation"), false);
  assert.match(view.focus.at(-1).name, /Finalizar e revisar roteiro/);
  assert.deepEqual(view.calls.filter(call => call.method === "POST").map(({ path, body }) => ({ path, body })), [
    { path: "/test-suites/root-fixture/recordings", body: { confirm: true } },
  ]);
  await view.click("Adicionar teste");
  assert.match(text(view.find(node => node.props.role === "dialog")), /gravação em andamento/);
  assert.equal(view.button("Descartar e sair").props.disabled, true);
  await view.click("Continuar editando");
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

test("catalog icon hints explain duplication, archive confirmation and restoring without applying any action", async t => {
  const suite = suiteFixture();
  suite.cases.push({ ...suite.cases[0], id: "CT002", archived: true });
  const view = await workspace(t, suite);
  await view.click("Gerenciar");
  const tips = () => view.all().filter(node => node.type === tooltipModule.exports.default).map(node => node.props.text);
  assert.ok(tips().some(value => /Duplicar teste.*cópia.*manual.*revisar e salvar/.test(value)));
  assert.ok(tips().some(value => /Arquivar teste.*abre uma confirmação.*sem apagar resultados e prints/.test(value)));
  view.find(node => node.type === "select" && node.props["aria-label"] === "Filtrar CTs").props.onChange({ target: { value: "archived" } });
  await view.settle();
  assert.ok(tips().some(value => /Restaurar teste.*base ativa.*histórico preservado/.test(value)));
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
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
const buildingCard = view => view.find(node => node.props["data-construction-card"]);

test("About closes on outside clicks without stealing focus or cancelling the clicked action", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  const details = view.find(node => node.props.className === "suite-context-details").props.ref.current;
  const inside = {}, focus = [];
  details.open = true;
  details.contains = target => target === inside;
  details.querySelector = () => ({ focus(options) { focus.push(options); } });
  await view.windowEvent("pointerdown", { target: inside }); assert.equal(details.open, true);
  await view.windowEvent("pointerdown", { target: {}, preventDefault() { assert.fail("Outside actions stay usable"); } });
  assert.equal(details.open, false); assert.deepEqual(focus, []);
  details.open = true;
  await view.windowEvent("pointerdown", { target: inside }); assert.equal(details.open, true);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("Escape closes About and restores focus to its trigger only while the panel is open", async t => {
  const view = await workspace(t);
  const details = view.find(node => node.props.className === "suite-context-details").props.ref.current;
  const focused = []; let prevented = 0;
  details.open = true;
  details.querySelector = selector => { assert.equal(selector, "summary"); return { focus(options) { focused.push(options); } }; };
  await view.windowEvent("keydown", { key: "ArrowRight" }); assert.equal(details.open, true);
  await view.windowEvent("keydown", { key: "Escape", preventDefault() { prevented++; } });
  assert.equal(details.open, false); assert.deepEqual(focused, [{ preventScroll: true }]); assert.equal(prevented, 1);
  await view.windowEvent("keydown", { key: "Escape", preventDefault() { prevented++; } }); assert.equal(prevented, 1);
});

test("Add reveals one distinct construction card without counting it as a saved or executable CT", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await view.click("Ver imagem da etapa 1: Abrir página");
  await view.click("Adicionar teste");
  assert.match(text(buildingCard(view)), /Em construção.*Novo teste.*Escolha como começar/);
  assert.equal(buildingCard(view).props["data-case-id"], undefined);
  assert.equal(view.button("Ver teste em construção").props.draggable, undefined);
  assert.equal(view.button("Salvar rascunho").props.disabled, true);
  assert.equal(view.button("Revisar execução").props.disabled, true);
  assert.deepEqual(plannedIds(view), ["CT001"]); assert.ok(view.image());
  await view.click("Adicionar teste");
  assert.equal(view.all().filter(node => node.props["data-construction-card"]).length, 1);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
  await view.click("Ver teste em construção");
  assert.match(text(view.find(node => node.props["aria-label"] === "Etapas do teste em construção")), /Descreva o teste no assistente/);
  await view.click("Ver etapas de CT001: Ordenar produtos"); assert.ok(view.image());
  await view.click("Descartar teste em construção");
  assert.equal(view.all().some(node => node.props["data-construction-card"]), false);
  assert.equal(view.button("Revisar execução").props.disabled, false);
});

test("AI replies refine the same construction card and send the complete prior draft as context", async t => {
  const first = { title: "Conferir carrinho", description: "Proposta", url: "https://fixture.invalid", preconditions: [], steps: ["Abrir carrinho"], expected_result: "Itens visíveis" };
  const second = { ...first, title: "Conferir e esvaziar carrinho", steps: [...first.steps, "Remover um item", "Conferir carrinho vazio"], expected_result: "Nenhum item restante" };
  const inputs = [];
  const options = { initialCaseId: "CT001", assistantReply: body => { inputs.push(body); return { reply: "Roteiro atualizado para revisão.", selected_case_ids: [], new_cases: [inputs.length === 1 ? first : second], usage: {} }; } };
  const view = await workspace(t, undefined, undefined, options);
  await view.click("Adicionar teste"); await view.click("Planejar com IA");
  const llm = { provider_id: "fixture", model_id: "planner" };
  await view.chat().props.onSend("Quero testar o carrinho", llm); await view.settle();
  assert.match(text(buildingCard(view)), /Conferir carrinho.*1 etapa definida/);
  assert.equal(buildingCard(view).props["data-version"], 1); assert.equal(inputs[0].draft, undefined);
  await view.chat().props.onSend("Inclua remover todos os itens", llm); await view.settle();
  assert.deepEqual(inputs[1].draft, first);
  assert.match(text(buildingCard(view)), /Conferir e esvaziar carrinho.*3 etapas definidas/);
  assert.equal(buildingCard(view).props["data-version"], 2);
  await view.click("Ver teste em construção");
  const lane = view.find(node => node.props["aria-label"] === "Etapas propostas, rolagem horizontal");
  assert.equal([...elements(lane)].filter(node => node.type === "article").length, 3);
  assert.match(text(lane), /Remover um item.*Conferir carrinho vazio/);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 2);
  assert.equal(view.calls.some(call => /\/(cases|plans|start|discover)$/.test(call.path)), false);
  await view.click("Adicionar teste"); assert.match(text(buildingCard(view)), /Conferir e esvaziar carrinho/);
});

test("clarifying or failed AI replies keep the last complete draft, and multiple proposals require a choice", async t => {
  const proposal = { title: "Carrinho revisado", description: "Proposta", url: "https://fixture.invalid", preconditions: [], steps: ["Conferir itens"], expected_result: "Itens corretos" };
  let mode = "first";
  const options = { initialCaseId: "CT001", assistantReply: () => {
    if (mode === "error") throw new Error("Resposta inválida do assistente");
    return { reply: "Confira a proposta", selected_case_ids: [], new_cases: mode === "first" ? [proposal] : mode === "many" ? [proposal, { ...proposal, title: "Outro carrinho" }] : [], usage: {} };
  } };
  const view = await workspace(t, undefined, undefined, options), llm = { provider_id: "fixture", model_id: "planner" };
  await view.click("Adicionar teste"); await view.click("Planejar com IA");
  await view.chat().props.onSend("Carrinho", llm); await view.settle();
  mode = "clarify"; await view.chat().props.onSend("Tenho uma dúvida", llm); await view.settle();
  assert.match(text(buildingCard(view)), /Carrinho revisado/); assert.equal(buildingCard(view).props["data-version"], 1);
  mode = "error"; await view.chat().props.onSend("Tente novamente", llm); await view.settle();
  assert.match(text(buildingCard(view)), /Carrinho revisado.*Confira o aviso no chat/);
  assert.equal(view.chat().props.error, "Resposta inválida do assistente");
  mode = "many"; await view.chat().props.onSend("Mostre opções", llm); await view.settle();
  assert.match(text(buildingCard(view)), /2 propostas no chat/);
  await view.click("Outro carrinho"); assert.match(text(buildingCard(view)), /Outro carrinho/);
  assert.equal(view.field("Título").props.value, "Outro carrinho");
  assert.equal(view.calls.some(call => /\/(cases|plans|start)$/.test(call.path)), false);
});

test("a construction draft is saved only after review and becomes exactly one real selected card", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await view.click("Adicionar teste"); await view.click("Planejar com IA");
  await view.chat().props.onSend("Quero conferir o carrinho", { provider_id: "fixture", model_id: "planner" }); await view.settle();
  await view.click("Ver teste em construção"); await view.click("Revisar e salvar");
  assert.equal(view.field("Título").props.value, "Carrinho vazio");
  await view.change("Título", "Meu carrinho vazio"); assert.match(text(buildingCard(view)), /Meu carrinho vazio/);
  assert.equal(view.calls.some(call => call.path.endsWith("/cases")), false);
  await view.submit();
  assert.equal(view.all().some(node => node.props["data-construction-card"]), false);
  assert.deepEqual(plannedIds(view), ["CT001", "CT002"]);
  assert.equal(view.calls.filter(call => call.path.endsWith("/cases")).length, 1);
  assert.equal(view.calls.some(call => /\/(plans|start)$/.test(call.path)), false);
});

test("discarding a meaningful construction draft asks first and keeps the existing execution untouched", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await view.click("Adicionar teste"); await view.click("Planejar com IA");
  await view.chat().props.onSend("Quero conferir o carrinho", { provider_id: "fixture", model_id: "planner" }); await view.settle();
  await view.click("Descartar teste em construção"); await view.click("Continuar editando");
  assert.ok(buildingCard(view)); assert.deepEqual(plannedIds(view), ["CT001"]);
  await view.click("Descartar teste em construção"); await view.click("Descartar e sair");
  assert.equal(view.all().some(node => node.props["data-construction-card"]), false);
  assert.deepEqual(plannedIds(view), ["CT001"]); assert.equal(view.button("Revisar execução").props.disabled, false);
  assert.equal(view.calls.some(call => /\/(cases|plans|start)$/.test(call.path)), false);
});

test("recording updates the construction card but requires explicit consent, finish and review", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await view.click("Adicionar teste"); await view.click("Gravar minha navegação");
  assert.match(text(buildingCard(view)), /Aguardando gravação/);
  assert.equal(view.calls.some(call => call.path.endsWith("/recordings")), false);
  await view.click("Confirmar e abrir navegador de gravação");
  assert.match(text(buildingCard(view)), /Teste gravado.*Gravando navegação/);
  assert.equal(view.button("Descartar teste em construção").props.disabled, true);
  await view.click("Finalizar e revisar roteiro");
  assert.match(text(buildingCard(view)), /Teste gravado.*Revise antes de salvar/);
  assert.equal(view.field("Passos").props.value, "Abrir carrinho");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.calls.some(call => /\/(cases|plans|start)$/.test(call.path)), false);
});

test("finishing inside J opens the recorded script for review without another finish request or save", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001",
    recordingStateReply: { status: "finished", steps: ["Abrir produtos", "Consultar carrinho"] } });
  await view.click("Adicionar teste"); await view.click("Gravar minha navegação");
  await view.click("Confirmar e abrir navegador de gravação");
  assert.equal(view.has("suite-recording"), true);
  await view.tick();
  assert.equal(view.has("suite-recording"), false);
  assert.equal(view.field("Título").props.value, "Teste gravado");
  assert.equal(view.field("Passos").props.value, "Abrir produtos\nConsultar carrinho");
  assert.match(text(buildingCard(view)), /Revise antes de salvar/);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.calls.some(call => /\/(finish|cases|plans|start)$/.test(call.path)), false);
  const count = view.calls.length; await view.tick(); assert.equal(view.calls.length, count);
});

test("recording progress from J updates the construction card without opening or saving a script early", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001",
    recordingStateReply: { status: "recording", steps: ["Consultar produtos"] } });
  await view.click("Adicionar teste"); await view.click("Gravar minha navegação");
  await view.click("Confirmar e abrir navegador de gravação"); await view.tick();
  assert.match(text(view.find(node => node.props.id === "suite-recording")), /1 passos.*Consultar produtos/);
  assert.equal(view.has("suite-case-editor"), false);
  assert.equal(view.calls.some(call => /\/(finish|cases|plans|start)$/.test(call.path)), false);
});

test("a failed explicit save retains the construction card, edited definition and real selection", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", rejectWrite: path => path.endsWith("/cases") });
  await view.click("Adicionar teste"); await view.click("Planejar com IA");
  await view.chat().props.onSend("Quero testar o carrinho", { provider_id: "fixture", model_id: "planner" }); await view.settle();
  await view.click("Ver teste em construção"); await view.click("Revisar e salvar");
  await view.change("Título", "Roteiro que preciso manter"); await view.submit();
  assert.match(text(buildingCard(view)), /Roteiro que preciso manter/);
  assert.equal(view.field("Título").props.value, "Roteiro que preciso manter");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.button("Revisar execução").props.disabled, true);
});

test("a meaningful construction draft protects navigation even when no existing tests are selected", async t => {
  const navigations = [];
  const view = await workspace(t, undefined, undefined, { navigate: hash => navigations.push(hash) });
  await view.click("Preparar execução"); await view.click("Adicionar teste"); await view.click("Planejar com IA");
  await view.chat().props.onSend("Quero testar o carrinho", { provider_id: "fixture", model_id: "planner" }); await view.settle();
  assert.equal(view.unloadBlocked(), true);
  assert.equal(await view.navigate("#/exploracoes"), false);
  await view.click("Continuar editando"); assert.ok(buildingCard(view));
  assert.equal(await view.navigate("#/exploracoes"), false);
  await view.click("Descartar e sair"); assert.deepEqual(navigations, ["#/exploracoes"]);
  assert.equal(view.all().some(node => node.props["data-construction-card"]), false);
  assert.equal(view.calls.some(call => /\/(cases|plans|start)$/.test(call.path)), false);
});

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
  await view.click("Escolher testes"); await view.click("Filtros dos testes");
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
  await view.click("Duplicar CT001 como novo CT manual");
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
  assert.equal(view.all().some(node => node.props.className === "suite-plan-heading suite-plan-toolbar"), false);
  const help = view.find(node => node.props.id === "suite-planning-guidance");
  assert.equal(help.props.className, "sr-only");
  assert.match(text(help), /confirme|execução/);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("one small accessible icon replaces preparation actions and refresh while keeping the selector reachable", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  const nav = view.find(node => node.props["aria-label"] === "Áreas da base de testes");
  const icon = view.button("Escolher testes");
  assert.equal([...elements(nav)].includes(icon), true);
  assert.equal(icon.props.type, "button");
  assert.equal(text(icon), "", "Only the icon is visible, no label or duplicated count");
  assert.equal(icon.props["aria-controls"], "suite-case-selector");
  assert.equal(icon.props["aria-expanded"], false);
  const tooltip = view.find(node => node.type === tooltipModule.exports.default && node.props.children === icon);
  assert.match(tooltip.props.text, /Escolher testes da base/);
  assert.equal(view.all().some(node => node.props["aria-label"] === "Atualizar conjunto"), false);
  assert.equal(view.all().some(node => node.props.className === "suite-plan-heading suite-plan-toolbar"), false);
  assert.equal(view.all().filter(node => node.type === "button" && text(node) === "Adicionar teste").length, 1, "Creation remains in the main header only");
  await view.click("Escolher testes");
  assert.equal(view.button("Escolher testes").props["aria-expanded"], true);
  assert.equal(view.focus.at(-1).name, "Buscar CTs");
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, false);
  await view.click("Escolher testes");
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, true);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  await view.click("Testes");
  assert.equal(view.all().some(node => node.props["aria-label"] === "Atualizar conjunto"), true, "Catalog refresh is independent of the removed preparation refresh");
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
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

test("the chooser starts with only search and a filter icon, while filtering and bulk selection remain available", async t => {
  const suite = suiteFixture();
  suite.cases.push({ ...suite.cases[0], id: "CT002", title: "Cliente sem nome", status: "FAIL" });
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  await view.click("Escolher testes");
  const searchbar = view.find(node => node.props.className === "suite-selector-searchbar");
  assert.equal([...elements(searchbar)].filter(node => node.type === "input").length, 1);
  assert.equal([...elements(searchbar)].filter(node => node.type === "button").length, 1);
  assert.equal(view.has("suite-selector-filters"), false);
  assert.equal(view.all().some(node => node.props.className === "suite-selector-heading"), false);
  assert.equal(view.button("Filtros dos testes").props["aria-expanded"], false);
  view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.onChange({ target: { value: "CT002" } }); await view.settle();
  await view.click("Filtros dos testes");
  view.find(node => node.type === "select" && node.props["aria-label"] === "Filtrar CTs").props.onChange({ target: { value: "FAIL" } }); await view.settle();
  view.find(node => node.type === "select" && node.props["aria-label"] === "Filtrar por fluxo funcional").props.onChange({ target: { value: "unclassified" } }); await view.settle();
  assert.equal(view.button("Filtros dos testes").props["data-active"], true);
  assert.match(view.button("Filtros dos testes").props.title, /2 ativos/);
  await view.click("Filtros dos testes");
  assert.equal(view.has("suite-selector-filters"), false);
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.value, "CT002");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  await view.click("Filtros dos testes"); await view.click("Limpar filtros");
  assert.equal(view.button("Filtros dos testes").props["data-active"], false);
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.value, "CT002", "Clearing filters does not erase the search");
  await view.click("Adicionar filtrados");
  assert.deepEqual(plannedIds(view), ["CT001", "CT002"]);
  assert.equal(view.has("suite-selector-filters"), false);
  assert.equal(view.focus.at(-1).name, "Buscar CTs");
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("filter controls close on Escape or outside click without resetting filters, selection or capture plans", async t => {
  const capture = { version: 1, checkpoints: [{ id: "kept-print", gap: 0, order: 0 }] };
  const view = await workspace(t, suiteFixture(), undefined, { initialCaseId: "CT001", initialCapturePlan: capture });
  await view.click("Escolher testes"); await view.click("Filtros dos testes");
  view.find(node => node.type === "select" && node.props["aria-label"] === "Filtrar CTs").props.onChange({ target: { value: "PASS" } }); await view.settle();
  const inside = {};
  view.find(node => node.props.className === "suite-selector-tools").props.ref.current.contains = target => target === inside;
  await view.windowEvent("pointerdown", { target: inside });
  assert.equal(view.has("suite-selector-filters"), true);
  let prevented = false;
  await view.windowEvent("keydown", { key: "Escape", preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(view.has("suite-selector-filters"), false);
  assert.equal(view.focus.at(-1).name, "Filtros dos testes");
  await view.click("Filtros dos testes");
  assert.equal(view.find(node => node.type === "select" && node.props["aria-label"] === "Filtrar CTs").props.value, "PASS");
  await view.windowEvent("pointerdown", { target: {} });
  assert.equal(view.has("suite-selector-filters"), false);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "kept-print"), true);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("the script keeps two named horizontal rails without the visible Testes and Etapas headings", async t => {
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
  assert.equal(view.all().some(node => node.props.className === "suite-rail-heading"), false);
  assert.equal(view.all().some(node => node.props["aria-label"] === "Testes desta execução"), true);
  assert.equal(view.all().some(node => node.props["aria-label"] === "Etapas de CT003"), true);
  assert.equal(view.all().some(node => node.props.className === "suite-plan-state"), false);
  assert.match(text(view.find(node => node.props.className === "suite-plan-footer")), /não salvo/);
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

function caseTransfer() {
  let payload = "";
  return { types: ["application/x-autojev-test-case"], effectAllowed: "", dropEffect: "",
    setData(type, value) { assert.equal(type, this.types[0]); payload = value; }, getData() { return payload; } };
}
const draggedCard = (view, id) => view.find(node => node.props["data-case-id"] === id);
async function beginCaseDrag(view, id, transfer) {
  const card = draggedCard(view, id);
  const button = [...elements(card)].find(node => node.props.className === "suite-test-card-select");
  assert.equal(button.props.draggable, true);
  button.props.onDragStart({ dataTransfer: transfer }); await view.settle();
}
async function dropCaseOn(view, id, transfer, side) {
  const event = { dataTransfer: transfer, clientX: side === "before" ? 20 : 180,
    currentTarget: { getBoundingClientRect: () => ({ left: 0, width: 200 }) }, preventDefault() {}, stopPropagation() {} };
  draggedCard(view, id).props.onDragOver(event); await view.settle();
  assert.equal(draggedCard(view, id).props["data-drop-side"], side);
  draggedCard(view, id).props.onDrop(event); await view.settle();
}

test("dragging test cards inserts on either side, keeps the active CT and saves the exact order with its prints", async t => {
  const suite = suiteFixture();
  suite.cases.push({ ...suite.cases[0], id: "CT003", title: "Consultar produto" }, { ...suite.cases[0], id: "CT005", title: "Conferir carrinho" });
  const capture = { version: 1, checkpoints: [{ id: "db-8", gap: 0, order: 0 }] };
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001", initialCapturePlan: capture });
  await view.chat().props.onSend("Adicione CT003 e CT005", null); await view.settle();
  const transfer = caseTransfer();
  await beginCaseDrag(view, "CT005", transfer); await dropCaseOn(view, "CT001", transfer, "before");
  assert.deepEqual(plannedIds(view), ["CT005", "CT001", "CT003"]);
  assert.equal(view.button("Ver etapas de CT001: Ordenar produtos").props["aria-pressed"], true);
  await beginCaseDrag(view, "CT001", transfer); await dropCaseOn(view, "CT003", transfer, "after");
  assert.deepEqual(plannedIds(view), ["CT005", "CT003", "CT001"]);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "db-8"), true);
  assert.ok(view.all().some(node => node.props.role === "status" && /CT001 movido para a posição 3 de 3/.test(text(node))));
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
  await view.click("Salvar rascunho");
  const items = view.calls.find(call => call.path.endsWith("/plans")).body.items;
  assert.deepEqual(items.map(item => item.case_id), ["CT005", "CT003", "CT001"]);
  assert.deepEqual(items[2].capture_plan, capture);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("the empty rail area accepts the last position, while cancelling and invalid drags never change a saved order", async t => {
  const suite = suiteFixture(); suite.cases.push({ ...suite.cases[0], id: "CT003", title: "Consultar produto" });
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  await view.chat().props.onSend("Adicione CT003", null); await view.settle();
  const transfer = caseTransfer(), rail = () => view.find(node => node.props["aria-label"] === "Testes selecionados, rolagem horizontal");
  await beginCaseDrag(view, "CT001", transfer);
  rail().props.onDrop({ dataTransfer: transfer, preventDefault() {}, stopPropagation() {} }); await view.settle();
  assert.deepEqual(plannedIds(view), ["CT003", "CT001"]);
  await beginCaseDrag(view, "CT003", transfer);
  view.button("Ver etapas de CT003: Consultar produto").props.onDragEnd(); await view.settle();
  assert.deepEqual(plannedIds(view), ["CT003", "CT001"]);
  assert.equal(view.all().some(node => node.props["data-drop-side"]), false);
  for (const invalid of ["not json", JSON.stringify({ rootId: "other-suite", caseId: "CT003" }), JSON.stringify({ rootId: suite.id, caseId: "CT999" })]) {
    await beginCaseDrag(view, "CT003", transfer); transfer.setData(transfer.types[0], invalid);
    rail().props.onDrop({ dataTransfer: transfer, preventDefault() {}, stopPropagation() {} }); await view.settle();
    assert.deepEqual(plannedIds(view), ["CT003", "CT001"]);
  }
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

test("all three tools can be placed and moved by keyboard-friendly controls with the same original gaps", async t => {
  const suite = suiteFixture(), before = structuredClone(suite);
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  await view.click("Selecionar Executar SQL");
  await view.click("Adicionar Executar SQL na posição 0, intervalo 1");
  await view.click("Selecionar Aguardar print");
  await view.click("Adicionar print na posição 0");
  await view.click("Selecionar Executar SWS");
  await view.click("Adicionar Executar SWS na posição 0, intervalo 2");
  assert.equal(view.all().filter(node => node.type === "button" && node.props.draggable).length, 3);
  assert.equal(view.all().filter(node => node.type === "article" && node.props.draggable).length, 3);
  await view.click("Mover Executar SQL db-1 para direita");
  await view.click("Salvar rascunho");
  const saved = view.calls.find(call => call.path.endsWith("/plans")).body.items[0];
  assert.deepEqual(saved.capture_plan.checkpoints, [{ id: "db-2", gap: 0, order: 0 }]);
  assert.deepEqual(saved.planned_integrations, [{ id: "db-3", gap: 0, order: 1, kind: "sws" }, { id: "db-1", gap: 1, order: 0, kind: "sql" }]);
  assert.match(text(view.find(node => node.props.className === "suite-plan-footer")), /2 SWS\/SQL só no rascunho/);
  await view.click("Revisar execução");
  assert.equal(view.button("Executar 1 teste").props.disabled, true, "Unavailable integrations can never be silently omitted even if a stale backend says ready");
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
  assert.deepEqual(suite, before);
});

test("dragging a tool and an existing card preserves its identity; foreign or malformed drags do nothing", async t => {
  const view = await workspace(t, suiteFixture(), undefined, { initialCaseId: "CT001" });
  let payload = "";
  const transfer = { types: ["application/x-autojev-step"], effectAllowed: "", setData(type, value) { assert.equal(type, this.types[0]); payload = value; }, getData() { return payload; } };
  const drag = async (source, gap, index) => {
    source.props.onDragStart({ dataTransfer: transfer }); await view.settle();
    const target = view.find(node => node.type === "button" && node.props["data-gap"] === gap && node.props["data-insertion-index"] === index);
    target.props.onDrop({ dataTransfer: transfer, preventDefault() {} }); await view.settle();
  };
  await drag(view.button("Selecionar Executar SWS"), 0, 0);
  assert.equal(transfer.effectAllowed, "copy");
  await drag(view.find(node => node.type === "article" && node.props["data-checkpoint-id"] === "db-1"), 1, 0);
  assert.equal(transfer.effectAllowed, "move");
  const target = view.find(node => node.type === "button" && node.props["data-gap"] === 0);
  for (const invalid of ["{broken", JSON.stringify({kind:"sql",caseId:"CT999"}), JSON.stringify({kind:"shell",caseId:"CT001"}), JSON.stringify({kind:"capture",caseId:"CT001",id:"missing"})]) {
    payload = invalid; target.props.onDrop({ dataTransfer: transfer, preventDefault() {} }); await view.settle();
  }
  assert.equal(view.all().filter(node => node.props["data-checkpoint-id"]).length, 1);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
  await view.click("Salvar rascunho");
  assert.deepEqual(view.calls.find(call => call.path.endsWith("/plans")).body.items[0].planned_integrations, [{ id: "db-1", gap: 1, order: 0, kind: "sws" }]);
});

test("all three tools show a full-size hover preview without adding or saving anything before drop", async t => {
  const suite = suiteFixture();
  suite.rounds = [{ id: "preview-draft", root_id: suite.id, status: "draft", revision: 1, name: "Roteiro salvo", items: [{ case_id: "CT001" }] }];
  const view = await workspace(t, suite, undefined, { planId: "preview-draft" });
  let payload = "";
  const transfer = { types: ["application/x-autojev-step"], setData(_, value) { payload = value; }, getData() { return payload; } };
  for (const [title, kind] of [["Aguardar print", "capture"], ["Executar SWS", "sws"], ["Executar SQL", "sql"]]) {
    view.button(`Selecionar ${title}`).props.onDragStart({ dataTransfer: transfer, clientX: 110, clientY: 200 }); await view.settle();
    const overlay = view.find(node => node.type === overlayModule.exports.default);
    assert.equal(overlay.props.preview.kind, kind); assert.equal(overlay.props.preview.size, 160);
    view.find(node => node.type === "button" && node.props["data-gap"] === 1).props.onDragOver({ dataTransfer: transfer, preventDefault() {} }); await view.settle();
    const preview = view.find(node => node.props["data-step-insertion-preview"]);
    assert.equal(preview.props["aria-hidden"], "true");
    assert.equal(preview.props["data-preview-gap"], 1); assert.match(text(preview), new RegExp(title));
    const insertion = view.find(node => node.type === "button" && node.props["data-gap"] === 1);
    assert.equal(insertion.props["aria-hidden"], true);
    assert.equal(insertion.props.disabled, true);
    assert.equal(insertion.props.tabIndex, -1);
    assert.equal(view.all().filter(node => node.props["data-checkpoint-id"]).length, 0);
    assert.equal(view.calls.some(call => call.method !== "GET"), false);
    assert.match(text(view.find(node => node.props.className === "suite-plan-footer")), /salvo em Rascunhos/);
    overlay.props.onCancel(); await view.settle();
    assert.equal(view.all().some(node => node.props["data-step-insertion-preview"]), false);
    assert.equal(view.all().some(node => node.type === overlayModule.exports.default), false);
    assert.equal(view.find(node => node.type === "button" && node.props["data-gap"] === 1).props["aria-hidden"], undefined);
    assert.equal(view.find(node => node.type === "button" && node.props["data-gap"] === 1).props.disabled, false);
  }
});

test("dropping on the expanded preview commits exactly one step at its gap and clears both previews", async t => {
  const view = await workspace(t, suiteFixture(), undefined, { initialCaseId: "CT001" });
  let payload;
  const transfer = { types: ["application/x-autojev-step"], setData(_, value) { payload = value; }, getData() { return payload; } };
  view.button("Selecionar Aguardar print").props.onDragStart({ dataTransfer: transfer }); await view.settle();
  view.button("Adicionar print na posição 1").props.onDragOver({ dataTransfer: transfer, preventDefault() {} }); await view.settle();
  const target = view.find(node => node.props.className === "suite-step-drop-target" && [...elements(node)].some(child => child.props["data-step-insertion-preview"]));
  target.props.onDrop({ dataTransfer: transfer, preventDefault() {} }); await view.settle();
  assert.equal(view.all().filter(node => node.props["data-checkpoint-id"]).length, 1);
  assert.equal(view.all().some(node => node.props["data-step-insertion-preview"]), false);
  assert.equal(view.all().some(node => node.type === overlayModule.exports.default), false);
  const adjacentSlots = view.all().filter(node => node.type === "button" && node.props["data-gap"] === 1);
  assert.deepEqual(adjacentSlots.map(node => node.props["data-insertion-index"]), [0, 1]);
  for (const slot of adjacentSlots) { assert.equal(slot.props["aria-hidden"], undefined); assert.equal(slot.props.disabled, false); }
  await view.click("Salvar rascunho");
  assert.deepEqual(view.calls.find(call => call.path.endsWith("/plans")).body.items[0].capture_plan.checkpoints, [{ id: "db-1", gap: 1, order: 0 }]);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("canvas-wide hover works off + and drop commits the displayed gap even when the DOM target shifts", async t => {
  const view = await workspace(t, suiteFixture(), undefined, { initialCaseId: "CT001" });
  let payload;
  const transfer = { types: ["application/x-autojev-step"], setData(_, value) { payload = value; }, getData() { return payload; } };
  view.button("Selecionar Executar SQL").props.onDragStart({ dataTransfer: transfer }); await view.settle();
  const rail = view.find(node => node.props.ref && node.props.className === "suite-step-strip suite-horizontal-rail");
  rail.props.ref.current.querySelectorAll = () => [0, 1].map(gap => ({ dataset: { gap: String(gap), insertionIndex: "0" }, getBoundingClientRect: () => ({ left: 100 + gap * 240, width: 28 }) }));
  // The pointer is on the trailing half of the original, not inside a + button.
  const canvas = view.find(node => node.props["data-step-canvas"]);
  assert.equal(typeof canvas.props.onDragOver, "function");
  canvas.props.onDragEnter({ clientX: 300, clientY: 100, dataTransfer: transfer, preventDefault() {} }); await view.settle();
  assert.equal(view.find(node => node.props["data-step-insertion-preview"]).props["data-preview-gap"], 1);
  assert.equal(view.find(node => node.type === overlayModule.exports.default).props.hidden, true);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
  // Reflow can put a different button under the pointer. Never commit its gap.
  view.find(node => node.type === "button" && node.props["data-gap"] === 0).props.onDrop({ dataTransfer: transfer, preventDefault() {} }); await view.settle();
  await view.click("Salvar rascunho");
  assert.deepEqual(view.calls.find(call => call.path.endsWith("/plans")).body.items[0].planned_integrations, [{ id: "db-1", gap: 1, order: 0, kind: "sql" }]);
});

test("crossing canvas children keeps the preview; leaving or cancelling removes it without changing a saved draft", async t => {
  const suite = suiteFixture();
  suite.rounds = [{ id: "leave-preview", root_id: suite.id, status: "draft", revision: 1, name: "Roteiro salvo", items: [{ case_id: "CT001" }] }];
  const view = await workspace(t, suite, undefined, { planId: "leave-preview" });
  const transfer = { types: ["application/x-autojev-step"], setData() {} };
  view.button("Selecionar Aguardar print").props.onDragStart({ dataTransfer: transfer }); await view.settle();
  view.button("Adicionar print na posição 1").props.onDragOver({ dataTransfer: transfer, preventDefault() {} }); await view.settle();
  const event = { relatedTarget: null, clientX: 300, clientY: 150, currentTarget: { contains() { return false; }, getBoundingClientRect() { return { left: 100, right: 800, top: 100, bottom: 500 }; } } };
  view.find(node => node.props["data-step-canvas"]).props.onDragLeave(event); await view.settle();
  assert.ok(view.all().some(node => node.props["data-step-insertion-preview"]));
  view.find(node => node.props["data-step-canvas"]).props.onDragLeave({ ...event, clientX: 90 }); await view.settle();
  assert.equal(view.all().some(node => node.props["data-step-insertion-preview"]), false);
  assert.equal(view.find(node => node.type === overlayModule.exports.default).props.hidden, false);
  view.find(node => node.type === overlayModule.exports.default).props.onCancel(); await view.settle();
  assert.equal(view.all().some(node => node.type === overlayModule.exports.default), false);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"]), false);
  assert.match(text(view.find(node => node.props.className === "suite-plan-footer")), /salvo em Rascunhos/);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("reopening a draft preserves mixed steps and clearing integrations never clears real print pauses", async t => {
  const suite = suiteFixture();
  suite.rounds = [{ id: "mixed-draft", root_id: suite.id, status: "draft", revision: 2, name: "Meu roteiro", items: [{ case_id: "CT001", capture_plan: { version: 1, checkpoints: [{ id: "db-4", gap: 0, order: 2 }] }, planned_integrations: [{ id: "db-2", gap: 0, order: 0, kind: "sql" }, { id: "db-3", gap: 0, order: 1, kind: "sws" }] }] }];
  const view = await workspace(t, suite, undefined, { planId: "mixed-draft" });
  assert.deepEqual(view.all().filter(node => node.type === "article" && node.props.draggable).map(node => node.props["data-checkpoint-id"]), ["db-2", "db-3", "db-4"]);
  await view.click("Remover Executar SQL db-2"); await view.click("Remover Executar SWS db-3");
  assert.equal(view.all().filter(node => node.props["data-checkpoint-id"]).length, 1);
  assert.equal(view.find(node => node.props["data-checkpoint-id"] === "db-4").props.draggable, true);
  await view.click("Adicionar print após pausa db-4");
  assert.ok(view.all().some(node => node.props["data-checkpoint-id"] === "db-1"), "New IDs must not collide with any saved mixed step");
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("manual scripts use the same lower rail without pretending to support automatic print pauses", async t => {
  const suite = suiteFixture(); suite.cases[0].mode = "manual";
  suite.cases[0].steps = ["Abrir o formulário", "Preencher dados de teste", "Conferir o registro salvo"];
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  assert.equal(view.all().filter(node => node.props.className === "suite-source-step").length, 3);
  assert.equal(view.all().some(node => node.props["data-gap"] !== undefined), false);
  assert.deepEqual(view.all().filter(node => node.props.className === "suite-source-step").map(node => text(node.props.children[1])), suite.cases[0].steps);
  assert.equal(view.all().some(node => node.props.className === "suite-rail-details"), false);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("original step cards open only their own image, compact the lane and close without changing the plan", async t => {
  const suite = suiteFixture();
  suite.cases[0].recorded_steps = [
    { title: "Antes da ação", kind: "observation", screenshot: "before.png", evidence_exploration_id: "latest-run" },
    { title: "Selecionar opção", kind: "action", screenshot: "target.png", evidence_exploration_id: "latest-run" },
    { title: "Conferir seleção", kind: "assertion" },
  ];
  const capture = { version: 1, checkpoints: [{ id: "keep-print", gap: 1, order: 0 }] };
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001", initialCapturePlan: capture });
  const calls = view.calls.length;
  await view.click("Ver imagem da etapa 1: Antes da ação");
  assert.equal(view.image().props.src, "http://api.fixture.invalid/api/explorations/latest-run/screenshots/before.png");
  assert.equal(view.image().props.title, "Antes da ação");
  assert.equal(view.image().props.index, 0);
  const editor = view.find(node => node.props.className === "suite-step-editor");
  assert.equal(editor.props.children[2], view.image(), "The preview is a full-width grid sibling of both palette and step rail");
  assert.equal([...elements(view.find(node => node.props["data-step-canvas"]))].includes(view.image()), false, "The image must not leave an empty palette column beside it");
  assert.equal(view.find(node => node.props["aria-label"] === "Etapas de CT001").props["data-preview-open"], true);
  assert.equal(view.button("Ver imagem da etapa 1: Antes da ação").props["aria-expanded"], true);
  await view.click("Ver imagem da etapa 2: Selecionar opção");
  assert.match(view.image().props.src, /\/target\.png$/);
  assert.equal(view.image().props.index, 1);
  await view.click("Ver imagem da etapa 3: Conferir seleção");
  assert.equal(view.image().props.src, null, "Never borrow the previous capture for a step without one");
  view.image().props.onClose(); await view.settle();
  assert.equal(view.find(node => node.props["aria-label"] === "Etapas de CT001").props["data-preview-open"], false);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.ok(view.all().some(node => node.props["data-checkpoint-id"] === "keep-print"));
  assert.equal(view.calls.length, calls, "Opening images adds no API/model/plan operations");
});

test("compact image mode removes palette and drag instructions, restores them on close and retains integration warnings", async t => {
  const suite = suiteFixture();
  suite.cases[0].recorded_steps = [{ title: "Antes", kind: "observation", screenshot: "before.png", evidence_exploration_id: "owner-run" }];
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  const paletteLabel = () => view.all().some(node => node.type === "span" && text(node) === "Adicionar etapa");
  const hint = () => view.all().find(node => node.props.className === "suite-step-hint");
  assert.equal(paletteLabel(), true); assert.match(text(hint()), /Arraste entre as etapas/);
  await view.click("Ver imagem da etapa 1: Antes");
  assert.equal(paletteLabel(), false); assert.equal(hint(), undefined);
  for (const name of ["Aguardar print", "Executar SWS", "Executar SQL"]) assert.equal(view.button(`Selecionar ${name}`).props.draggable, true);
  view.image().props.onClose(); await view.settle();
  assert.equal(paletteLabel(), true); assert.match(text(hint()), /Arraste entre as etapas/);
  await view.click("Ver imagem da etapa 1: Antes");
  await view.click("Selecionar Executar SQL");
  await view.click("Adicionar Executar SQL na posição 0, intervalo 1");
  assert.equal(paletteLabel(), false); assert.match(text(hint()), /SWS\/SQL só no rascunho/);
  assert.ok(view.image()); assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("switching CTs clears the image selection and returning does not show another CT's capture", async t => {
  const suite = suiteFixture();
  suite.cases[0].recorded_steps = [{ title: "Imagem A", kind: "observation", screenshot: "a.png", evidence_exploration_id: "run-a" }];
  suite.cases.push({ ...suite.cases[0], id: "CT002", title: "Segundo teste", recorded_steps: [{ title: "Imagem B", kind: "observation", screenshot: "b.png", evidence_exploration_id: "run-b" }] });
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  await view.chat().props.onSend("Adicione CT002", null); await view.settle();
  await view.click("Ver imagem da etapa 1: Imagem A");
  await view.click("Ver etapas de CT002: Segundo teste");
  assert.equal(view.find(node => node.props["aria-label"] === "Etapas de CT002").props["data-preview-open"], false);
  await view.click("Ver imagem da etapa 1: Imagem B");
  assert.match(view.image().props.src, /run-b\/screenshots\/b\.png$/);
  await view.click("Ver etapas de CT001: Ordenar produtos");
  assert.equal(view.find(node => node.props["aria-label"] === "Etapas de CT001").props["data-preview-open"], false);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("print insertion still works with the image open, but dropping on the image never adds a step", async t => {
  const suite = suiteFixture();
  suite.cases[0].recorded_steps = [{ title: "Antes", kind: "observation", screenshot: "before.png", evidence_exploration_id: "owner-run" }];
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
  await view.click("Ver imagem da etapa 1: Antes");
  let payload;
  const transfer = { types: ["application/x-autojev-step"], setData(_, value) { payload = value; }, getData() { return payload; } };
  view.button("Selecionar Aguardar print").props.onDragStart({ dataTransfer: transfer }); await view.settle();
  view.find(node => node.type === "button" && node.props["data-gap"] === 1).props.onDrop({ dataTransfer: transfer, preventDefault() {}, clientX: 100 }); await view.settle();
  assert.equal(view.all().filter(node => node.props["data-checkpoint-id"]).length, 1);
  assert.match(view.image().props.src, /owner-run\/screenshots\/before\.png$/);
  view.button("Selecionar Executar SQL").props.onDragStart({ dataTransfer: transfer }); await view.settle();
  const canvas = view.find(node => node.props["data-step-canvas"]);
  const event = { dataTransfer: transfer, target: { closest: selector => selector === ".suite-step-image" ? {} : null }, preventDefault() {} };
  canvas.props.onDragOver(event); await view.settle();
  canvas.props.onDrop(event); await view.settle();
  assert.equal(view.all().filter(node => node.props["data-checkpoint-id"]).length, 1);
  assert.match(view.image().props.src, /before\.png$/);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("an explicit historical source uses its own stage order and owner rather than the latest catalog image", async t => {
  const suite = suiteFixture();
  suite.cases[0].recorded_steps = [{ title: "Imagem atual", kind: "observation", screenshot: "latest.png", evidence_exploration_id: "latest-run" }];
  const reference = { title: "Histórico", recorded_steps: [{ title: "Imagem antiga", kind: "observation", screenshot: "old.png", evidence_exploration_id: "old-run" }] };
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001", initialSourceId: "old-run", initialSourceCase: "CT009", caseContext: { root_id: suite.id, case_id: "CT001", mode: "automatic", reference } });
  await view.click("Ver imagem da etapa 1: Imagem antiga");
  assert.match(view.image().props.src, /old-run\/screenshots\/old\.png$/);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("narrow-screen pane switching keeps the editor, conversation, draft message and chosen model", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await reviewAiProposal(view);
  await view.change("Título", "Meu caso em edição");
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
  assert.equal(view.chat().props.messages.at(-2).content, "Adicione CT001");
  const conversation = structuredClone(view.chat().props.messages);
  await view.click("Ver roteiro da rodada");
  assert.equal(view.field("Título").props.value, "Meu caso em edição");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  await view.click("Ver assistente de testes");
  assert.deepEqual(view.chat().props.messages, conversation);
  assert.deepEqual(view.chat().props.model, llm);
  assert.equal(view.calls.filter(call => call.method !== "GET" && !call.path.endsWith("/assistant")).length, 0, "Direct additions and pane changes never write or execute tests");
});

test("preparation omits the numbered step bar without removing selection, script editing or review", async t => {
  const capture = { version: 1, checkpoints: [{ id: "preserved-print", gap: 0, order: 0 }] };
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", initialCapturePlan: capture });
  assert.equal(view.all().some(node => node.props.className === "suite-preparation-steps" || node.props["aria-label"] === "Etapas de preparação"), false);
  for (const label of ["1. Escolher testes", "2. Ajustar roteiro", "3. Revisar e confirmar"]) {
    assert.equal(view.all().some(node => text(node) === label), false);
  }
  await view.click("Escolher testes");
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, false);
  await view.click("Ver roteiro (1)");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "preserved-print"), true);
  assert.equal(view.button("Salvar rascunho").props.disabled, false);
  await view.click("Revisar execução");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.button("Executar 1 teste").props.disabled, true);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.equal(css.includes("suite-preparation-steps"), false);
});

test("creation and review stay inside the work pane and review requires explicit execution consent", async t => {
  const view = await workspace(t, suiteFixture(), undefined, { initialCaseId: "CT001" });
  await reviewAiProposal(view);
  const center = view.find(node => node.props.className === "suite-plan-center");
  assert.equal([...elements(center)].some(node => node.props.id === "suite-case-editor"), true);
  assert.equal([...elements(center)].some(node => node === view.chat()), false);
  await view.click("Fechar editor");
  await view.click("Descartar e sair");
  await view.click("Revisar execução");
  assert.equal(view.focus.at(-1).name, "suite-plan-review");
  assert.equal(view.scroll.at(-1).name, "suite-plan-review");
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, true);
  assert.equal(view.button("Executar 1 teste").props.disabled, true);
  assert.deepEqual(view.calls.filter(call => call.method === "POST" && !call.path.endsWith("/assistant")).map(call => call.path), ["/test-suites/root-fixture/plans", "/test-suites/rounds/plan-fixture/validate"]);
  assert.deepEqual(view.calls.find(call => call.path.endsWith("/plans")).body.items.map(item => item.case_id), ["CT001"]);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("saving a draft exposes its destination without starting or changing the selected tests", async t => {
  const navigations = [];
  const view = await workspace(t, catalogFixture(), undefined, { initialCaseId: "CT050", navigate: hash => navigations.push(hash) });
  await view.click("Testes"); await markCatalog(view, "CT067"); await view.click("Preparar execução (2)");
  assert.equal(view.all().some(node => node.props.className === "suite-draft-link text-button"), false);
  await view.click("Salvar rascunho");
  assert.match(text(view.find(node => node.props.className === "suite-plan-footer")), /salvo em Rascunhos/);
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

async function acceptReview(view) {
  const checkbox = [...elements(view.find(node => node.type === "label" && text(node).startsWith("Revisei os testes")))].find(node => node.type === "input");
  checkbox.props.onChange({ target: { checked: true } });
  await view.settle();
}

test("execution name is editable only in review and survives returning to the script", async t => {
  const capture = { version: 1, checkpoints: [{ id: "name-print", gap: 0, order: 0 }] };
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", initialCapturePlan: capture });
  assert.equal(view.all().some(node => node.props["aria-label"] === "Nome da execução"), false);
  await view.click("Revisar execução");
  assert.equal(view.field("Nome da execução").props.value, "Nova execução");
  assert.equal(view.field("Nome da execução").props.maxLength, 160);
  await acceptReview(view);
  const before = view.calls.length;
  await view.change("Nome da execução", "Regressão do carrinho");
  assert.equal(view.has("suite-plan-review"), true, "Typing must not send the user back to preparation");
  assert.equal(view.button("Executar 1 teste").props.disabled, true, "Renaming clears the earlier consent");
  assert.equal(view.calls.length, before, "Typing never saves or starts a test");
  await view.click("Ajustar");
  assert.equal(view.all().some(node => node.props["aria-label"] === "Nome da execução"), false);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "name-print"), true);
  await view.click("Salvar rascunho");
  const update = view.calls.find(call => call.method === "PUT");
  assert.equal(update.body.name, "Regressão do carrinho");
  assert.deepEqual(update.body.items, [{ case_id: "CT001", capture_plan: capture }]);
  await view.click("Revisar execução");
  assert.equal(view.field("Nome da execução").props.value, "Regressão do carrinho");
  assert.equal(view.calls.filter(call => call.method === "PUT").length, 1);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("confirmed execution saves a renamed review and uses its revalidated revision and exact selection", async t => {
  const navigations = [];
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", navigate: hash => navigations.push(hash) });
  await view.click("Revisar execução");
  await view.change("Nome da execução", "Conferir ordenação");
  await acceptReview(view);
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
  await view.click("Executar 1 teste"); // Isolated request fixture: no live execution.
  assert.deepEqual(view.calls.filter(call => call.method !== "GET").map(call => [call.method, call.path]), [
    ["POST", "/test-suites/root-fixture/plans"], ["POST", "/test-suites/rounds/plan-fixture/validate"],
    ["PUT", "/test-suites/root-fixture/plans/plan-fixture"], ["POST", "/test-suites/rounds/plan-fixture/validate"],
    ["POST", "/test-suites/rounds/plan-fixture/start"],
  ]);
  assert.deepEqual(view.calls.find(call => call.method === "PUT").body, { name: "Conferir ordenação", items: [{ case_id: "CT001" }], expected_revision: 1 });
  assert.deepEqual(view.calls.at(-1).body, { confirm: true, expected_revision: 2 });
  assert.deepEqual(navigations, ["#/conjuntos/root-fixture?round=plan-fixture"]);
});

test("a name save that refreshes test definitions requires a fresh review before starting", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001",
    planUpdate: plan => ({ ...plan, items: plan.items.map(item => ({ ...item, definition: { title: "Definição atualizada na base" } })) }),
  });
  await view.click("Revisar execução"); await view.change("Nome da execução", "Nova regressão"); await acceptReview(view);
  await view.click("Executar 1 teste");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.field("Nome da execução").props.value, "Nova regressão");
  assert.equal(view.button("Executar 1 teste").props.disabled, true);
  assert.match(text(view.find(node => node.props.className === "suite-error")), /condições da execução mudaram/);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("revalidation after renaming never starts a newly unavailable test", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001",
    validationReply: checked => checked.revision > 1 ? { ...checked, available: false, items: [{ case_id: "CT001", available: false, reason: "Teste arquivado", code: "archived" }] } : checked,
  });
  await view.click("Revisar execução"); await view.change("Nome da execução", "Minha execução"); await acceptReview(view);
  await view.click("Executar 1 teste");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.field("Nome da execução").props.value, "Minha execução");
  await acceptReview(view);
  assert.equal(view.button("Executar 1 teste").props.disabled, true);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("failed name saves retain the review and need consent again before retrying", async t => {
  let reject = true;
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", rejectWrite: (path, options) => reject && options.method === "PUT" });
  await view.click("Revisar execução"); await view.change("Nome da execução", "Nome preservado"); await acceptReview(view);
  await view.click("Executar 1 teste");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.field("Nome da execução").props.value, "Nome preservado");
  assert.equal(view.button("Executar 1 teste").props.disabled, true);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
  reject = false;
  await view.click("Ajustar"); await view.click("Revisar execução");
  assert.equal(view.field("Nome da execução").props.value, "Nome preservado");
  assert.equal(view.calls.findLast(call => call.method === "PUT").body.name, "Nome preservado");
});

test("saved names appear in review and an empty edit cannot strand the preparation screen", async t => {
  const suite = suiteFixture();
  suite.rounds = [{ id: "plan-fixture", root_id: suite.id, status: "draft", revision: 4, name: "Roteiro salvo", items: [{ case_id: "CT001" }] }];
  const view = await workspace(t, suite, undefined, { planId: "plan-fixture" });
  assert.equal(view.all().some(node => node.props["aria-label"] === "Nome da execução"), false);
  await view.click("Revisar execução");
  assert.equal(view.field("Nome da execução").props.value, "Roteiro salvo");
  await view.change("Nome da execução", "   "); await acceptReview(view);
  assert.equal(view.field("Nome da execução").props["aria-invalid"], true);
  assert.equal(view.button("Executar 1 teste").props.disabled, true);
  await view.click("Ajustar");
  assert.equal(view.button("Revisar execução").props.disabled, false);
  await view.click("Revisar execução");
  assert.equal(view.field("Nome da execução").props.value, "Roteiro salvo");
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("a failed revalidation after saving the name retries validation, not the save or an old start revision", async t => {
  let validations = 0;
  const navigations = [];
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", navigate: hash => navigations.push(hash),
    rejectWrite: path => path.endsWith("/validate") && ++validations === 2,
  });
  await view.click("Revisar execução"); await view.change("Nome da execução", "Revalidar com segurança"); await acceptReview(view);
  await view.click("Executar 1 teste");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.field("Nome da execução").props.value, "Revalidar com segurança");
  assert.equal(view.button("Executar 1 teste").props.disabled, true);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
  await acceptReview(view); await view.click("Executar 1 teste");
  assert.equal(view.calls.filter(call => call.method === "PUT").length, 1);
  assert.equal(view.calls.filter(call => call.path.endsWith("/validate")).length, 3);
  assert.deepEqual(view.calls.at(-1).body, { confirm: true, expected_revision: 2 });
  assert.deepEqual(navigations, ["#/conjuntos/root-fixture?round=plan-fixture"]);
});

test("a new test opened from review returns to editing without dropping the selection or executing", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await view.click("Revisar execução");
  await view.click("Ajustar"); await reviewAiProposal(view);
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
  await view.click("Escolher testes"); await view.click("Filtros dos testes"); await view.click("Adicionar filtrados"); await view.click("Revisar execução");
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
  assert.match(text(view.find(node => node.props.className === "suite-plan-footer")), /não salvo/);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("chat creation preserves the selected tests and does not consume the script work area", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await view.click("Adicionar teste");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.find(node => node.props["aria-label"] === "Planejamento da rodada").props["data-mobile-pane"], "chat");
  const center = view.find(node => node.props["aria-label"] === "Roteiro da rodada");
  assert.equal([...elements(center)].some(node => node.props.className === "suite-flow-board"), true);
  assert.equal([...elements(center)].some(node => node.props.className === "planning-chat-creation"), false);
  await view.click("Gravar minha navegação");
  assert.equal(view.has("suite-case-editor"), false);
  assert.equal(view.has("suite-record-confirmation"), true);
  assert.match(text(view.find(node => node.props.id === "suite-record-confirmation")), /podem alterar dados.*não uma automação pronta/);
  await view.click("Ver roteiro da rodada");
  assert.deepEqual(plannedIds(view), ["CT001"]);
  await view.click("Ver assistente de testes");
  assert.equal(view.has("suite-record-confirmation"), true);
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("planner responsive styles reserve space for the composer and use pane tabs instead of stacking chat below", async () => {
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.app-main:has\(\.suite-planner-view\)\s*\{[^}]*height: 100dvh;[^}]*min-height: 0;[^}]*overflow: hidden;/);
  assert.match(css, /\.suite-planner-view\s*\{[^}]*container: suite-planner \/ inline-size;[^}]*min-height: 0;/);
  assert.match(css, /\.suite-planner-view \.suite-planner-layout.with-chat\s*\{\s*grid-template-columns: minmax\(0, 1fr\) clamp\(320px, 30%, 420px\);/);
  assert.match(css, /\.suite-planning-main > \.suite-plan-footer\s*\{[^}]*grid-area: footer;[^}]*position: static;/);
  assert.match(css, /\.suite-planner-view \.suite-plan-center\s*\{[^}]*min-height: 0;[^}]*overflow: auto;/);
  assert.match(css, /\.suite-flow-board\s*\{[^}]*grid-template-rows: max-content minmax\(0, 1fr\);/, "Steps receive every pixel not needed by the test cards");
  assert.match(css, /\.suite-flow-board \.suite-selected-list\s*\{[^}]*flex: none;[^}]*height: calc\(var\(--rail-card-size\) \+ 20px\);/, "The test lane fits its cards and horizontal scrollbar, rather than stretching");
  assert.doesNotMatch(css, /grid-template-rows: minmax\(105px, 3fr\) minmax\(0, 7fr\)/);
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
  await view.click("Duplicar CT001 como novo CT manual");
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

async function reviewAiProposal(view) {
  await view.click("Adicionar teste"); await view.click("Planejar com IA");
  // This fixture never contacts a real model; exercise the actual proposal-review path.
  await view.chat().props.onSend("Sugira um caso para o carrinho", { provider_id: "fixture", model_id: "fixture" });
  await view.settle(); await view.click("Carrinho vazio");
}

test("base creation and explicit execution inclusion have different save destinations", async t => {
  const base = await workspace(t);
  await base.click("Duplicar CT001 como novo CT manual"); await fillManual(base);
  assert.equal(base.chat().props.purpose, "base");
  await base.submit();
  assert.equal(base.find(node => node.props["aria-label"] === "Selecionar CT002").props.checked, false);
  assert.equal(base.calls.filter(call => call.method !== "GET").length, 1);
  assert.equal(base.calls[1].path, "/test-suites/root-fixture/cases");

  const include = await workspace(t);
  await include.click("Duplicar CT001 como novo CT manual"); await fillManual(include);
  await include.click("Salvar e incluir na execução");
  assert.deepEqual(plannedIds(include), ["CT002"]);
  assert.equal(include.chat().props.purpose, "execution");
  assert.equal(include.calls.filter(call => call.method !== "GET").length, 1);

  const planner = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  await reviewAiProposal(planner); await fillManual(planner);
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
  await view.click("Duplicar CT001 como novo CT manual"); await fillManual(view);
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
  await reviewAiProposal(view); await fillManual(view);
  assert.equal(view.button("Revisar execução").props.disabled, true);
  assert.equal(view.button("Salvar rascunho").props.disabled, true);
  assert.equal(view.calls.some(call => call.method !== "GET" && !call.path.endsWith("/assistant")), false);
  await view.click("Fechar editor"); await view.click("Descartar e sair");
  assert.equal(view.button("Revisar execução").props.disabled, false);
  assert.deepEqual(plannedIds(view), ["CT001"]);
});

test("an incomplete form and a stale version preserve the user's text and never navigate or overwrite", async t => {
  const view = await workspace(t);
  await view.click("Duplicar CT001 como novo CT manual"); await view.change("Título", "Incompleto"); await view.change("Passos", "");
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
