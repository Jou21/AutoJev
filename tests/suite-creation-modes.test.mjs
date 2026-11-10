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
for (const name of ["suite-planning-chat", "suite-capture-plan", "suite-execution-history", "suite-report-export", "suite-horizontal-rail", "suite-step-evidence", "functional-flows"]) {
  const compiledModule = { exports: {} };
  new Function("require", "module", "exports", await compiled(`../app/components/${name}.ts`))(name => dependencies[name] || nativeRequire(name), compiledModule, compiledModule.exports);
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
  const slots = [], effects = [], calls = [], focus = [], scroll = [], downloads = [];
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
  new Function("require", "module", "exports", "window", source)(name => name === "./suite-report-export" ? { ...dependencies[name], downloadExecutionReport: async (load, _url, filename, options) => {
      options.onProgress({ stage: "preparing", elapsedMs: 0, bytes: 0 });
      const response = await load(options.signal);
      if (!response.ok || !response.headers.get("content-type")?.includes("text/html")) throw new Error("O relatório HTML não está disponível. Tente novamente mais tarde.");
      downloads.push({ html: await response.text(), filename });
      options.onProgress({ stage: "complete", elapsedMs: 100, bytes: 10 }); return true;
    } } : name === "react" ? hooks
    : name === "./PlanningChat" ? { __esModule: true, default: ChatFixture }
      : name === "./StepEvidencePreview" ? { __esModule: true, default: ImagePreviewFixture }
      : name.endsWith(".css") ? {} : dependencies[name] || nativeRequire(name), compiledModule, compiledModule.exports,
  browser);
  const request = async (path, options = {}) => {
    calls.push({ path, method: options.method || "GET", body: options.body && (path === "/test-suites/import" ? options.body : JSON.parse(options.body)) });
    if (options.method && initialProps.rejectWrite?.(path, options)) throw new Error("O CT foi alterado em outra tela. Atualize sua versão.");
    if (options.raw && initialProps.exportResponse) return initialProps.exportResponse(options);
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
    if (path === "/test-suites/import" && options.method === "POST") {
      if (initialProps.importError) throw new Error(initialProps.importError);
      return { id: suite.id, message: "Base importada para revisão." };
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
      const { credentials, ...body } = JSON.parse(options.body);
      assert.deepEqual(body, { confirm: true, expected_revision: savedPlan.revision });
      if (credentials) assert.ok(Object.values(credentials).every(value => typeof value === "string" && value.trim()));
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
  const fakeTarget = name => ({ close() {}, showModal() { focus.push({ name: "export-dialog" }); }, focus(options) { focus.push({ name, options }); }, scrollIntoView(options) { scroll.push({ name, options }); }, reportValidity() { return true; } });
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
  const button = label => all().find(node => node.type === "button" && (node.props["aria-label"] === label || text(node) === label)) || find(node => node.type === "button" && text(node).startsWith(label));
  const field = label => [...elements(find(node => node.type === "label" && text(node).startsWith(label)))].find(node => ["input", "textarea"].includes(node.type));
  async function click(label) { const node = button(label); assert.ok(!node.props.disabled); node.props.onClick({ currentTarget: fakeTarget(label), preventDefault() {} }); await settle(); }
  async function change(label, value) { const node = field(label); assert.ok(node); node.props.onChange({ target: { value } }); await settle(); }
  t.after(() => { for (const slot of slots) slot?.cleanup?.(); });
  await settle();
  return { all, find, button, field, click, change, settle, calls, focus, scroll, downloads, chat: () => find(node => node.type === ChatFixture), image: () => find(node => node.type === ImagePreviewFixture),
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
  assert.equal(view.has("suite-planning-heading"), true);
  assert.equal(view.chat().props.purpose, "execution");
  const attentionKey = view.chat().props.creationActions.key;
  view.chat().props.onValueChange("Mensagem ainda não enviada"); await view.settle();
  assert.equal(view.chat().props.creationActions.key, attentionKey, "Typing does not restart the highlight");
  await view.click("Adicionar teste");
  assert.notEqual(view.chat().props.creationActions.key, attentionKey, "Each explicit Add restarts both cards' finite highlight");
  assert.equal(view.chat().props.value, "Mensagem ainda não enviada");
  assert.equal(view.chat().props.messages.length, 1, "Repeated Add reveals the current question without duplicates");
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("base cards open the original exploration from the whole card, the base from Ver testes, and expand history separately", async t => {
  const suite = { ...suiteFixture(), case_count: 1, active_count: 0, latest_activity: "2026-10-22T18:00:00Z" };
  const navigations = [], reports = [];
  const view = await workspace(t, suite, async (...args) => { reports.push(args); }, { rootId: undefined, navigate: hash => navigations.push(hash) });
  const card = view.find(node => node.props.className === "suite-group-main");
  const actions = [...elements(card)].filter(node => node.type === "button");
  assert.equal(actions.length, 4, "Base rows expose exploration, history, tests and report export independently");
  const open = actions.find(node => node.props.className.includes("suite-group-open"));
  assert.equal(open.props.type, "button");
  assert.match(open.props["aria-label"], /^Ver testes de fixture\.invalid, criado em /);
  const title = [...elements(card)].filter(node => node.props.className === "suite-group-name")[0].props.children[0];
  assert.equal(title.type, "button");
  assert.equal(title.props.className, "suite-group-title");
  assert.equal(title.props.type, "button");
  assert.equal(title.props.title, "Grupo root-fixture");
  assert.match(title.props["aria-label"], /^Abrir exploração de fixture\.invalid, criada em /);
  open.props.onClick(); await view.settle();
  title.props.onClick(); await view.settle();
  assert.deepEqual(reports, [["root-fixture", "consolidated", undefined, undefined, "tests"]], "Ver testes opens the report on its Testes view in a new tab");
  assert.match(open.props.title, /nova aba/);
  assert.deepEqual(navigations, ["#/exploracoes/root-fixture"], "Ver testes no longer navigates to the old suite screen");
  assert.equal(view.calls.some(call => call.method !== "GET"), false);
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.suite-group-main\s*\{[^}]*position: relative;/);
  assert.match(css, /\.suite-group-title::after\s*\{[^}]*position: absolute;[^}]*inset: 0;[^}]*z-index: 1;/, "The whole card opens the original exploration");
  assert.match(css, /\.suite-workspace \.suite-group-title:active:not\(:disabled\)\s*\{[^}]*transform: none;/, "Pressing must not shrink the full-card hit area to the title");
  assert.match(css, /\.suite-group-open\s*\{[^}]*position: relative;[^}]*z-index: 2;/, "Ver testes sits above the card overlay");
  assert.doesNotMatch(css, /\.suite-group-open::after/, "Ver testes no longer owns the card-wide overlay");
  assert.match(css, /\.suite-expand\s*\{[^}]*position: relative;[^}]*z-index: 2;/);
  assert.match(css, /\.suite-group-title:focus-visible::after\s*\{[^}]*outline: 2px solid/);
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
  const navigations = [], reports = [];
  const view = await workspace(t, undefined, async (...args) => { reports.push(args); }, { rootId: undefined, groupsReply: groups, navigate: hash => navigations.push(hash) });
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
  assert.deepEqual(reports, [["root-021", "consolidated", undefined, undefined, "tests"]]);
  assert.deepEqual(navigations, []);
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
  await reviewAiProposal(view);
  await view.change("Título", "Meu roteiro não salvo");
  await view.click("Adicionar teste");
  assert.ok(view.find(node => node.props.role === "dialog"));
  await view.click("Continuar editando");
  assert.equal(view.field("Título").props.value, "Meu roteiro não salvo");
  await view.click("Adicionar teste"); await view.click("Descartar e sair");
  assert.equal(view.has("suite-case-editor"), false);
  assert.equal(view.chat().props.messages.at(-1).kind, "creation_prompt");
  assert.equal(view.has("suite-planning-heading"), true);
  assert.equal(view.calls.some(call => call.method !== "GET" && !call.path.endsWith("/assistant")), false);
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
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Incluir CT002").props.checked, true);
  assert.match(view.chat().props.messages.at(-1).content, /salvo na base e incluído na preparação/);
  assert.equal(view.calls.filter(call => /\/(plans|start|discover)$/.test(call.path)).length, 0);
});

test("sidebar cards do not add unrequested duplication buttons", async t => {
 const view=await workspace(t);
 assert.equal(view.all().some(n=>n.props["aria-label"]?.startsWith("Duplicar ")),false);
 assert.equal(view.find(n=>n.props["data-selector-case"]==="CT001").props.children.filter(n=>n?.type==="button").length,1);
 assert.equal(view.calls.some(call=>call.method!=="GET"),false);
});

function catalogFixture() {
  const suite = suiteFixture(), original = suite.cases[0];
  suite.cases = ["CT050", "CT060", "CT067", "CT094", "CT097", "CT108", "CT109"].map(id => ({ ...original, id, title: "Carrinho · " + id }));
  return suite;
}
async function markCatalog(view, id) {
  view.find(node => node.type === "input" && node.props["aria-label"] === "Incluir " + id).props.onChange();
  await view.settle();
}
const plannedIds = view => view.all().filter(node => node.props.className === "suite-selector-position").map(node => ({id: view.all().find(card => card.props["data-selector-case"] && [...elements(card)].includes(node)).props["data-selector-case"], order: Number(text(node))})).sort((a,b)=>a.order-b.order).map(item=>item.id);
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
  await view.click("1º na execução: CT001 · Ordenar produtos"); assert.ok(view.image());
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
  await view.click("Adicionar teste"); await view.click("Planejar com IA");
  await view.chat().props.onSend("Quero testar o carrinho", { provider_id: "fixture", model_id: "planner" }); await view.settle();
  assert.equal(view.unloadBlocked(), true);
  assert.equal(await view.navigate("#/exploracoes"), false);
  await view.click("Continuar editando"); assert.ok(buildingCard(view));
  assert.equal(await view.navigate("#/exploracoes"), false);
  await view.click("Descartar e sair"); assert.deepEqual(navigations, ["#/exploracoes"]);
  assert.equal(view.all().some(node => node.props["data-construction-card"]), false);
  assert.equal(view.calls.some(call => /\/(cases|plans|start)$/.test(call.path)), false);
});

test("suite roots open only preparation and keep exact selection through search and bulk filters", async t => {
  const view = await workspace(t, catalogFixture());
  assert.equal(view.has("suite-planning-heading"), true);
  assert.equal(view.all().some(node => node.props.className === "suite-tabs suite-main-tabs"), false);
  assert.equal(view.all().some(node => node.props.className === "panel suite-catalog"), false);
  await markCatalog(view, "CT050");
  await markCatalog(view, "CT067");
  view.find(node => node.props["aria-label"] === "Buscar CTs").props.onChange({ target: { value: "CT060" } });
  await view.settle();
  assert.equal(view.find(node => node.props["aria-label"] === "Incluir CT060").props.checked, false);
  await view.click("Filtros dos testes");
  await view.click("Adicionar filtrados (1)");
  await view.click("Salvar rascunho");
  assert.deepEqual(view.calls.find(call => call.method === "POST").body.items.map(item => item.case_id), ["CT050", "CT067", "CT060"]);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("planner filter changes preserve capture plans and the selected order", async t => {
  const capture = { version: 1, checkpoints: [{ id: "header-print", gap: 0, order: 0 }] };
  const view = await workspace(t, catalogFixture(), undefined, { initialCaseId: "CT050", initialCapturePlan: capture });
  await markCatalog(view, "CT067");
  await view.click("Filtros dos testes");
  view.find(node => node.props["aria-label"] === "Filtrar CTs").props.onChange({ target: { value: "PASS" } });
  await view.settle();
  await view.click("Salvar rascunho");
  const items = view.calls.find(call => call.method === "POST").body.items;
  assert.deepEqual(items.map(item => item.case_id), ["CT050", "CT067"]);
  assert.deepEqual(items[0].capture_plan, capture);
});

test("planner reports and back remain read-only without alternative workspace screens", async t => {
  const reports = [], navigations = [];
  const view = await workspace(t, catalogFixture(), async (...args) => reports.push(args), { navigate: hash => navigations.push(hash) });
  await view.click("Abrir relatório consolidado");
  await view.click("Voltar para Meus testes");
  assert.deepEqual(reports, [["root-fixture", "consolidated"]]);
  assert.deepEqual(navigations, ["#/exploracoes"]);
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
  const planner = view.find(node => node.props.className === "suite-planner-view");
  assert.equal(planner.props["data-mobile-pane"], "plan");
  const layout = view.find(node => node.props.className === "suite-planner-layout with-chat");
  assert.equal(layout.props.children[1], view.chat(), "Chat is a sibling of the work area, not below its editor or footer");
  const main = layout.props.children[0];
  assert.match(main.props.className, /suite-planning-main/);
  assert.equal([...elements(main)].some(node => node.props.className === "suite-plan-footer"), false);
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, false);
  assert.equal(view.all().some(node => node.props.className === "suite-plan-heading suite-plan-toolbar"), false);
  const help = view.find(node => node.props.id === "suite-planning-guidance");
  assert.equal(help.props.className, "sr-only");
  assert.match(text(help), /confirme|execução/);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("preparation keeps the chooser open without a redundant toggle", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  assert.equal(view.all().some(node => node.props["aria-label"] === "Escolher testes"), false);
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, false);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.all().some(node => node.props["aria-label"] === "Áreas da base de testes"), false);
});

test("opening and finishing CT selection preserves order, filters and existing capture checkpoints", async t => {
  const capture = { version: 1, checkpoints: [{ id: "drawer-print", gap: 0, order: 0 }] };
  const view = await workspace(t, catalogFixture(), undefined, { initialCaseId: "CT050", initialCapturePlan: capture });
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, false);

  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, false);
  view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.onChange({ target: { value: "CT067" } });
  await view.settle();
  view.find(node => node.type === "input" && node.props["aria-label"] === "Incluir CT067").props.onChange();
  await view.settle();
  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, false);
  assert.deepEqual(plannedIds(view), ["CT067"]);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "drawer-print"), true);

  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.value, "CT067");
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Incluir CT067").props.checked, true);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("the chooser starts with only search and a filter icon, while filtering and bulk selection remain available", async t => {
  const suite = suiteFixture();
  suite.cases.push({ ...suite.cases[0], id: "CT002", title: "Cliente sem nome", status: "FAIL" });
  const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });

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
  assert.deepEqual(plannedIds(view), []);
  await view.click("Filtros dos testes"); await view.click("Limpar filtros");
  assert.equal(view.button("Filtros dos testes").props["data-active"], false);
  assert.equal(view.find(node => node.type === "input" && node.props["aria-label"] === "Buscar CTs").props.value, "CT002", "Clearing filters does not erase the search");
  await view.click("Adicionar filtrados");
  assert.deepEqual(plannedIds(view), ["CT002"]);
  await view.click("Salvar rascunho");
  assert.deepEqual(view.calls.find(call => call.path.endsWith("/plans")).body.items.map(item => item.case_id), ["CT001", "CT002"]);
  assert.equal(view.has("suite-selector-filters"), false);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("filter controls close on Escape or outside click without resetting filters, selection or capture plans", async t => {
  const capture = { version: 1, checkpoints: [{ id: "kept-print", gap: 0, order: 0 }] };
  const view = await workspace(t, suiteFixture(), undefined, { initialCaseId: "CT001", initialCapturePlan: capture });
   await view.click("Filtros dos testes");
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

test("preparation has only the steps rail and uses sidebar cards to switch tests", async t => {
 const suite=suiteFixture();suite.cases.push({...suite.cases[0],id:"CT003",title:"Consultar produto"});
 const view=await workspace(t,suite,undefined,{initialCaseId:"CT001"});
 await view.chat().props.onSend("Adicione CT003",null);await view.settle();
 await view.click("2º na execução: CT003 · Consultar produto");
 assert.equal(view.all().some(n=>n.props["aria-label"]==="Etapas de CT003"),true);
 assert.equal(view.all().some(n=>n.props.className?.includes("suite-selected-list")),false);
 assert.equal(view.all().filter(n=>n.props.className?.includes("suite-horizontal-rail")).length,1);
});


function caseTransfer() {
  let payload = "";
  return { types: ["application/x-autojev-test-case"], effectAllowed: "", dropEffect: "",
    setData(type, value) { assert.equal(type, this.types[0]); payload = value; }, getData() { return payload; } };
}
const draggedCard = (view, id) => view.find(node => node.props["data-selector-case"] === id);
async function beginCaseDrag(view, id, transfer) {
  const card = draggedCard(view, id);
  const button = [...elements(card)].find(node => node.type === "button");
  assert.equal(button.props.draggable, true);
  button.props.onDragStart({ dataTransfer: transfer, stopPropagation() {} }); await view.settle();
}
async function dropCaseOn(view, id, transfer, side) {
  const event = { dataTransfer: transfer, clientY: side === "before" ? 20 : 180,
    currentTarget: { getBoundingClientRect: () => ({ top: 0, height: 200 }) }, preventDefault() {}, stopPropagation() {} };
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
  assert.equal(view.find(n=>n.props["data-selector-case"]==="CT001").props.className.includes("selected"), true);
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
  assert.equal(view.all().filter(node => node.type === "button" && node.props.draggable && !node.props["aria-label"]?.includes("na execução:")).length, 3);
  assert.equal(view.all().filter(node => node.type === "article" && node.props.draggable).length, 3);
  await view.click("Mover Executar SQL db-1 para direita");
  await view.click("Salvar rascunho");
  const saved = view.calls.find(call => call.path.endsWith("/plans")).body.items[0];
  assert.deepEqual(saved.capture_plan.checkpoints, [{ id: "db-2", gap: 0, order: 0 }]);
  assert.deepEqual(saved.planned_integrations, [{ id: "db-3", gap: 0, order: 1, kind: "sws" }, { id: "db-1", gap: 1, order: 0, kind: "sql" }]);
  assert.match(text(view.find(node => node.props.className === "suite-planning-status sr-only")), /2 SWS\/SQL só no rascunho/);
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
    assert.match(text(view.find(node => node.props.className === "suite-planning-status sr-only")), /salvo em Rascunhos/);
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
  assert.match(text(view.find(node => node.props.className === "suite-planning-status sr-only")), /salvo em Rascunhos/);
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
  await view.click("2º na execução: CT002 · Segundo teste");
  assert.equal(view.find(node => node.props["aria-label"] === "Etapas de CT002").props["data-preview-open"], false);
  await view.click("Ver imagem da etapa 1: Imagem B");
  assert.match(view.image().props.src, /run-b\/screenshots\/b\.png$/);
  await view.click("1º na execução: CT001 · Ordenar produtos");
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

  assert.equal(view.find(node => node.props.id === "suite-case-selector").props.hidden, false);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "preserved-print"), true);
  assert.equal(view.button("Salvar rascunho").props.disabled, false);
  await view.click("Revisar execução");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.equal(css.includes("suite-preparation-steps"), false);
});

test("creation and review stay inside the work pane and only Executar authorizes execution", async t => {
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
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
  assert.deepEqual(view.calls.filter(call => call.method === "POST" && !call.path.endsWith("/assistant")).map(call => call.path), ["/test-suites/root-fixture/plans", "/test-suites/rounds/plan-fixture/validate"]);
  assert.deepEqual(view.calls.find(call => call.path.endsWith("/plans")).body.items.map(item => item.case_id), ["CT001"]);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("saving a draft exposes its destination without starting or changing the selected tests", async t => {
  const navigations = [];
  const view = await workspace(t, catalogFixture(), undefined, { initialCaseId: "CT050", navigate: hash => navigations.push(hash) });
  await markCatalog(view, "CT067");
  assert.equal(view.all().some(node => node.props.className === "suite-draft-link text-button"), false);
  await view.click("Salvar rascunho");
  assert.match(text(view.find(node => node.props.className === "suite-planning-status sr-only")), /salvo em Rascunhos/);
  assert.deepEqual(plannedIds(view), ["CT050", "CT067"]);
  const before = view.calls.length;
  await view.click("Ver rascunhos");
  assert.deepEqual(navigations, ["#/execucoes-planejadas"]);
  assert.equal(view.calls.length, before);
  assert.deepEqual(view.calls.find(call => call.method === "POST").body.items.map(item => item.case_id), ["CT050", "CT067"]);
  assert.equal(view.calls.some(call => /\/(start|discover|recordings)$/.test(call.path)), false);
});

test("review is a focused stage and returning to the script preserves checkpoints without starting", async t => {
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
  assert.equal(view.all().some(node => node.type === "label" && text(node).startsWith("Revisei os testes")), false);
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
  await view.click("Ajustar");
  assert.equal(view.has("suite-plan-review"), false);
  assert.equal(view.all().some(node => node.props["data-checkpoint-id"] === "review-print"), true);
  assert.deepEqual(plannedIds(view), ["CT001"]);
  assert.equal(view.focus.at(-1).name, "suite-planning-heading");
  await view.click("Revisar execução");
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
  assert.equal(view.calls.filter(call => call.path.endsWith("/plans")).length, 1);
  assert.equal(view.calls.filter(call => call.path.endsWith("/validate")).length, 2);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("execution name is editable only in review and survives returning to the script", async t => {
  const capture = { version: 1, checkpoints: [{ id: "name-print", gap: 0, order: 0 }] };
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", initialCapturePlan: capture });
  assert.equal(view.all().some(node => node.props["aria-label"] === "Nome da execução"), false);
  await view.click("Revisar execução");
  assert.equal(view.field("Nome da execução").props.value, "Nova execução");
  assert.equal(view.field("Nome da execução").props.maxLength, 160);

  const before = view.calls.length;
  await view.change("Nome da execução", "Regressão do carrinho");
  assert.equal(view.has("suite-plan-review"), true, "Typing must not send the user back to preparation");
  assert.equal(view.button("Executar 1 teste").props.disabled, false, "Renaming is saved and revalidated only when Executar is clicked");
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
  await view.click("Revisar execução"); await view.change("Nome da execução", "Nova regressão");
  await view.click("Executar 1 teste");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.field("Nome da execução").props.value, "Nova regressão");
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
  assert.match(text(view.find(node => node.props.className === "suite-error")), /condições da execução mudaram/);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("revalidation after renaming never starts a newly unavailable test", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001",
    validationReply: checked => checked.revision > 1 ? { ...checked, available: false, items: [{ case_id: "CT001", available: false, reason: "Teste arquivado", code: "archived" }] } : checked,
  });
  await view.click("Revisar execução"); await view.change("Nome da execução", "Minha execução");
  await view.click("Executar 1 teste");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.field("Nome da execução").props.value, "Minha execução");

  assert.equal(view.button("Executar 1 teste").props.disabled, false);
  await view.click("Executar 1 teste");
  assert.ok(view.all().some(node => node.type === "dialog" && node.props["aria-labelledby"] === "suite-unavailable-title"));
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("review checkboxes and unavailable dialog require explicit removal before starting a fresh subset", async t => {
  const suite = suiteFixture(); suite.cases.push({...suite.cases[0],id:"CT002",title:"Unavailable"});
  let navigation = 0;
  const view = await workspace(t, suite, undefined, { navigate(){navigation++;}, validationReply: checked => ({...checked, available: !checked.items.some(item=>item.case_id==="CT002"), items:checked.items.map(item=>item.case_id==="CT002"?{...item,available:false,reason:"Receita incompleta",code:"incomplete"}:item)}) });
  await view.click("Marcar todos"); await view.click("Revisar execução");
  const checkbox = name => view.find(node=>node.type==="input" && node.props["aria-label"]===name);
  assert.equal(checkbox("Selecionar todos os testes da revisão").props.checked,true);
  checkbox("Selecionar todos os testes da revisão").props.onChange(); await view.settle();
  assert.equal(view.button("Executar 0 testes").props.disabled,true);
  checkbox("Selecionar todos os testes da revisão").props.onChange(); await view.settle();
  assert.equal(view.button("Executar 2 testes").props.disabled,false);
  await view.click("Executar 2 testes");
  assert.equal(view.button("Executar").props.disabled,true);
  assert.equal(checkbox("Incluir indisponível CT002").props.checked,true);
  assert.equal(view.calls.some(call=>call.path.endsWith("/start")),false);
  checkbox("Incluir indisponível CT002").props.onChange(); await view.settle();
  assert.equal(view.button("Executar").props.disabled,false);
  checkbox("Incluir indisponível CT002").props.onChange(); await view.settle();
  assert.equal(view.button("Executar").props.disabled,true);
  await view.click("Desmarcar indisponíveis");
  assert.equal(checkbox("Executar CT002").props.checked,false);
  assert.equal(checkbox("Incluir indisponível CT002").props.checked,false);
  assert.equal(view.button("Executar").props.disabled,false);
  await view.click("Executar");
  const update=view.calls.find(call=>call.method==="PUT" && call.path.includes("/plans/"));
  assert.deepEqual(update.body.items.map(item=>item.case_id),["CT001"]);
  assert.equal(view.calls.filter(call=>call.path.endsWith("/validate")).length,2);
  assert.equal(view.calls.filter(call=>call.path.endsWith("/start")).length,1);
  assert.equal(navigation,1);
});

test("subset revalidation cannot start a newly unavailable remaining test", async t => {
  const suite=suiteFixture(); suite.cases.push({...suite.cases[0],id:"CT002",title:"Unavailable"});
  let checks=0;
  const view=await workspace(t,suite,undefined,{validationReply:checked=>{checks++;return {...checked,available:false,items:checked.items.map(item=>({...item,available:checks===1 && item.case_id==="CT001",code:"incomplete"}))};}});
  await view.click("Marcar todos"); await view.click("Revisar execução"); await view.click("Executar 2 testes");
  await view.click("Desmarcar indisponíveis"); await view.click("Executar");
  assert.equal(checks,2);
  assert.equal(view.button("Executar").props.disabled,true);
  assert.equal(view.calls.some(call=>call.path.endsWith("/start")),false);
});

test("all unavailable tests leave modal execution disabled after unchecking", async t => {
 const view=await workspace(t,undefined,undefined,{initialCaseId:"CT001",validationReply:checked=>({...checked,available:false,items:checked.items.map(item=>({...item,available:false,code:"incomplete"}))})});
 await view.click("Revisar execução"); await view.click("Executar 1 teste"); await view.click("Desmarcar indisponíveis");
 assert.equal(view.button("Executar").props.disabled,true);
 assert.equal(view.calls.some(call=>call.path.endsWith("/start")),false);
});

test("failed name saves retain review and allow an explicit execution retry", async t => {
  let reject = true;
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", rejectWrite: (path, options) => reject && options.method === "PUT" });
  await view.click("Revisar execução"); await view.change("Nome da execução", "Nome preservado");
  await view.click("Executar 1 teste");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.field("Nome da execução").props.value, "Nome preservado");
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
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
  await view.change("Nome da execução", "   ");
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
  await view.click("Revisar execução"); await view.change("Nome da execução", "Revalidar com segurança");
  await view.click("Executar 1 teste");
  assert.equal(view.has("suite-plan-review"), true);
  assert.equal(view.field("Nome da execução").props.value, "Revalidar com segurança");
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
  await view.click("Executar 1 teste");
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
   await view.click("Filtros dos testes"); await view.click("Adicionar filtrados"); await view.click("Revisar execução");
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
  assert.match(text(view.find(node => node.props.className === "suite-planning-status sr-only")), /não salvo/);
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
  assert.doesNotMatch(css, /grid-template-rows: minmax\(105px, 3fr\) minmax\(0, 7fr\)/);
  assert.match(css, /@container suite-planner \(max-width: 830px\)/);
  assert.match(css, /data-mobile-pane="plan"[^}]*\.suite-chat\s*\{\s*display: none;/);
  assert.match(css, /data-mobile-pane="chat"[^}]*\.suite-planning-main\s*\{\s*display: none;/);
});

test("archived tests cannot be added by direct chat and active tests remain available", async t => {
  const suite = catalogFixture(); suite.cases[0].archived = true;
  const view = await workspace(t, suite);
  await view.chat().props.onSend("adicione CT050", null); await view.settle();
  assert.match(view.chat().props.messages.at(-1).content, /Não adicionei/);
  assert.deepEqual(plannedIds(view), []);
  await view.chat().props.onSend("adicione CT067", null); await view.settle();
  assert.deepEqual(plannedIds(view), ["CT067"]);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("legacy round links show an inline monitor and keep long results collapsed", async t => {
  const suite = suiteFixture(), navigations = [];
  const execution = { id: "round-fixture", root_id: suite.id, name: "Conferir produtos", status: "completed", revision: 2, created_at: "2026-10-20T12:00:00Z", updated_at: "2026-10-20T12:01:00Z", items: [{ case_id: "CT001", mode: "automatic", definition: suite.cases[0], state: "completed", execution_id: "run-fixture", result: { status: "PASS", actual_result: "Lista e opções conferidas" } }] };
  suite.rounds = [execution, { ...execution, id: "draft-fixture", name: "Não executado", status: "draft" }];
  suite.legacy_runs = [{ id: "legacy-fixture", count: 1, status: "completed", created_at: "2026-10-19T12:00:00Z" }];
  const view = await workspace(t, suite, undefined, { roundId: execution.id, navigate: hash => navigations.push(hash) });
  assert.equal(view.has("suite-planning-heading"), true);
  assert.equal(view.all().some(node => node.props["aria-label"] === "Execuções, mais recentes primeiro"), false);
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
  await reviewAiProposal(view);
  const editor = view.find(node => node.props.id === "suite-case-editor");
  const advanced = [...elements(editor)].find(node => node.props.className === "suite-case-advanced");
  assert.equal(advanced.props.open, undefined);
  assert.match(text(advanced), /URL da aplicação.*Descrição.*Pré-condições/);
  assert.equal(view.field("URL da aplicação").props.value, "https://fixture.invalid");
  await view.change("Título", "Novo caso"); await view.change("Passos", "Abrir página\nConferir resultado");
  await view.change("Resultado esperado", "Resultado visível");
  assert.equal(view.field("Título").props.value, "Novo caso");
  assert.equal(view.field("Resultado esperado").props.value, "Resultado visível");
  assert.equal(view.calls.some(call => /\/(cases|plans|start)$/.test(call.path)), false);
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

test("planner case creation saves to the base and includes the new case without starting execution", async t => {
  const view = await workspace(t);
  await reviewAiProposal(view); await fillManual(view);
  assert.equal(view.chat().props.purpose, "execution");
  await view.submit();
  assert.deepEqual(plannedIds(view), ["CT002"]);
  assert.equal(view.calls.filter(call => call.method !== "GET" && !call.path.endsWith("/assistant")).length, 1);
  assert.equal(view.calls.find(call => call.path.endsWith("/cases")).path, "/test-suites/root-fixture/cases");
  assert.equal(view.calls.some(call => /\/(plans|start|discover)$/.test(call.path)), false);
});

test("manual version edits preserve the ID and cannot inherit approval or alter old evidence", async t => {
  const suite = suiteFixture();
  suite.cases[0] = { ...suite.cases[0], mode: "manual", revision: 3, recorded_at: "2026-10-14T16:22:00Z" };
  suite.rounds = [{ id: "historical", root_id: suite.id, name: "Histórico", status: "completed", created_at: "2026-10-14T16:22:00Z", items: [{ case_id: "CT001", result: { status: "PASS", evidence: "preserved.png" } }] }];
  const historical = structuredClone(suite.rounds);
  const view = await workspace(t, suite);
  await view.click("Editar CT001"); await fillManual(view, "Nova definição"); await view.submit();
  const write = view.calls.find(call => call.method === "PUT");
  assert.equal(write.path, "/test-suites/root-fixture/cases/CT001");
  assert.equal(write.body.expected_revision, 3);
  assert.equal(suite.cases[0].revision, 4);
  assert.notEqual(suite.cases[0].status, "PASS");
  assert.equal(view.button("Editar CT001").props.disabled, false);
  assert.deepEqual(suite.rounds, historical);
  assert.equal(view.calls.some(call => /\/(plans|start)$/.test(call.path)), false);
});

test("unsaved manual text blocks navigation and reload, then saves without executing before leaving", async t => {
  const navigations = [];
  const view = await workspace(t, undefined, undefined, { navigate: hash => navigations.push(hash) });
  await reviewAiProposal(view); await fillManual(view);
  assert.equal(view.unloadBlocked(), true);
  assert.equal(await view.navigate("#/execucoes-planejadas"), false);
  await view.click("Continuar editando");
  assert.equal(view.field("Título").props.value, "Conferir carrinho");
  assert.equal(await view.navigate("#/execucoes-planejadas"), false);
  await view.click("Salvar e continuar");
  assert.deepEqual(navigations, ["#/execucoes-planejadas"]);
  assert.equal(view.calls.filter(call => call.method !== "GET" && !call.path.endsWith("/assistant")).length, 1);
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
  await reviewAiProposal(view); await view.change("Título", "Incompleto"); await view.change("Passos", "");
  await view.navigate("#/execucoes-planejadas"); await view.click("Salvar e continuar");
  assert.equal(view.field("Título").props.value, "Incompleto");
  assert.match(text(view.find(node => node.props.role === "alert")), /Complete título/);
  assert.equal(view.calls.filter(call => call.method !== "GET" && !call.path.endsWith("/assistant")).length, 0);

  const suite = suiteFixture(); suite.cases[0].mode = "manual";
  const edit = await workspace(t, suite, undefined, { rejectWrite: () => true });
  await edit.click("Editar CT001"); await fillManual(edit, "Minha edição"); await edit.submit();
  assert.equal(edit.field("Título").props.value, "Minha edição");
  assert.equal(suite.cases[0].title, "Ordenar produtos");
  assert.match(text(edit.find(node => node.props.role === "alert")), /alterado em outra tela.*alterações foram mantidas/);
});

test("empty and archived-only bases retain preparation and empty filters retain the search", async t => {
  for (const archived of [false, true]) {
    const suite = suiteFixture();
    if (archived) suite.cases[0].archived = true; else suite.cases = [];
    const view = await workspace(t, suite);
    assert.equal(view.has("suite-planning-heading"), true);
    assert.ok(view.button("Adicionar teste"));
    assert.match(text(view.find(node => node.props.role === "status" && text(node).includes("Nenhum CT"))), /Nenhum CT corresponde aos filtros/);
  }
  const filtered = await workspace(t);
  const search = () => filtered.find(node => node.props["aria-label"] === "Buscar CTs");
  search().props.onChange({ target: { value: "nada-corresponde" } }); await filtered.settle();
  assert.equal(search().props.value, "nada-corresponde");
  search().props.onChange({ target: { value: "" } }); await filtered.settle();
  assert.ok(filtered.button("Selecionar CT001: Ordenar produtos"));
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
  assert.ok(view.button("Selecionar CT001: Ordenar produtos"));
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

test("review opens before validation finishes without marking pending tests unavailable", async t => {
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const view = await workspace(t, undefined, undefined, {
    initialCaseId: "CT001", validationReply: async checked => { await pending; return checked; },
  });
  view.button("Revisar execução").props.onClick();
  await view.settle();
  assert.equal(view.has("suite-plan-review"), true);
  const review = view.find(node => node.props.id === "suite-plan-review");
  assert.match(text(review), /Conferindo roteiro/);
  assert.doesNotMatch(text(review), /Indisponível/);
  assert.equal(view.button("Executar 1 teste").props.disabled, true);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
  finish(); await view.settle();
  assert.match(text(view.find(node => node.props.id === "suite-plan-review")), /Pronto/);
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
});

test("private round inputs appear only on demand and never enter saved plans", async t => {
  const navigations = [];
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001", navigate: hash => navigations.push(hash),
    validationReply: checked => ({ ...checked, required_inputs: ["login_alias", "password_alias"], missing_inputs: ["login_alias", "password_alias"] }),
  });
  const inputs = () => view.all().filter(node => node.type === "input" && node.props.type === "password");
  await view.click("Revisar execução");
  assert.equal(inputs().length, 0);
  assert.equal(view.button("Executar 1 teste").props.disabled, false);
  await view.click("Executar 1 teste");
  assert.equal(inputs().length, 2);
  assert.equal(view.find(node => node.type === "dialog" && node.props["aria-labelledby"] === "suite-authentication-title").props.className, "suite-unavailable-dialog suite-authentication-dialog");
  assert.ok(inputs().every(node => node.props.autoComplete === "off" && node.props.required));
  assert.equal(view.button("Autenticar e executar").props.disabled, true);
  inputs()[0].props.onChange({ target: { value: "round-user" } }); await view.settle();
  inputs()[1].props.onChange({ target: { value: "   " } }); await view.settle();
  assert.equal(view.button("Autenticar e executar").props.disabled, true);
  inputs()[1].props.onChange({ target: { value: "round-private-value" } }); await view.settle();
  assert.equal(view.button("Autenticar e executar").props.disabled, false);
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
  const dialog = view.find(node => node.type === "dialog");
  [...elements(dialog)].find(node => node.type === "form").props.onSubmit({ preventDefault() {} }); await view.settle();
  const start = view.calls.find(call => call.path.endsWith("/start"));
  assert.deepEqual(start.body.credentials, { login_alias: "round-user", password_alias: "round-private-value" });
  assert.equal(start.body.confirm, true);
  assert.ok(view.calls.filter(call => !call.path.endsWith("/start")).every(call => !JSON.stringify(call.body || {}).includes("round-private-value")));
  assert.equal(inputs().length, 0);
  assert.equal(navigations.length, 1);
});

test("private round inputs are discarded on cancel and leaving review", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001",
    validationReply: checked => ({ ...checked, required_inputs: ["private_alias"] }),
  });
  const input = () => view.all().find(node => node.type === "input" && node.props.type === "password");
  await view.click("Revisar execução"); await view.click("Executar 1 teste");
  input().props.onChange({ target: { value: "not-retained" } }); await view.settle();
  await view.click("Cancelar");
  assert.equal(input(), undefined);
  await view.click("Executar 1 teste"); assert.equal(input().props.value, "");
  input().props.onChange({ target: { value: "not-retained" } }); await view.settle();
  view.find(node => node.type === "dialog").props.onCancel({ preventDefault() {} }); await view.settle();
  await view.click("Ajustar"); await view.click("Revisar execução"); await view.click("Executar 1 teste");
  assert.equal(input().props.value, "");
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("same-round vault availability starts without requesting private values", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001",
    validationReply: checked => ({ ...checked, required_inputs: ["private_alias"], missing_inputs: [] }),
  });
  await view.click("Revisar execução"); await view.click("Executar 1 teste");
  assert.equal(view.all().some(node => node.type === "dialog"), false);
  assert.deepEqual(view.calls.find(call => call.path.endsWith("/start")).body, { confirm: true, expected_revision: 1 });
});


test("suite detail uses the full viewport and back restores the application navigation", async () => {
  const app = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const workspace = await readFile(new URL("../app/components/TestSuitesWorkspace.tsx", import.meta.url), "utf8");
  assert.ok(app.includes('route.view === "explorations" && route.suiteId ? " suite-fullscreen" : ""'));
  assert.match(css, /\.app-shell\.suite-fullscreen > \.sidebar,[\s\S]*?display: none/);
  assert.match(css, /\.app-shell\.suite-fullscreen > \.app-main\s*\{\s*margin-left: 0/);
  assert.ok(workspace.includes('aria-label="Voltar para Meus testes"'));
  assert.ok(workspace.includes('onClick={() => navigate("#/exploracoes")}'));
});


test("add test moves to the selector footer and replaces view script", async t => {
  const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
  const footer = view.find(node => node.props.className === "suite-selector-footer");
  const add = view.button("Adicionar teste");
  assert.equal([...elements(footer)].includes(add), true);
  assert.equal(view.all().filter(node => node.type === "button" && text(node) === "Adicionar teste").length, 1);
  assert.equal(view.all().some(node => node.type === "button" && /^Ver roteiro \(/.test(text(node))), false);
  assert.equal(add.props.disabled, false);
  await view.click("Adicionar teste");
  assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});


test("planner actions sit next to report without a bottom action bar", async t => {
 const view = await workspace(t, undefined, undefined, { initialCaseId: "CT001" });
 const actions = view.find(node => node.props.className === "suite-detail-actions");
 for (const name of ["Salvar rascunho", "Revisar execução", "Abrir relatório consolidado"]) {
   assert.equal([...elements(actions)].includes(view.button(name)), true);
 }
 assert.equal(view.all().some(node => node.props.className === "suite-plan-footer"), false);
 assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});


test("selector cards drag vertically with numbered execution order and persist the new sequence", async t => {
 const suite = suiteFixture();
 suite.cases.push({ ...suite.cases[0], id: "CT002", title: "Segundo teste" });
 const view = await workspace(t, suite, undefined, { initialCaseId: "CT001" });
 view.find(node => node.props["aria-label"] === "Incluir CT002").props.onChange(); await view.settle();
 const card = id => view.find(node => node.props["data-selector-case"] === id);
 const button = id => [...elements(card(id))].find(node => node.type === "button");
 assert.equal(text([...elements(card("CT001"))].find(n=>n.props.className === "suite-selector-position")), "1");
 assert.equal(text([...elements(card("CT002"))].find(n=>n.props.className === "suite-selector-position")), "2");
 const transfer = caseTransfer();
 button("CT002").props.onDragStart({dataTransfer:transfer,stopPropagation(){}}); await view.settle();
 const event = {dataTransfer:transfer,clientY:5,currentTarget:{getBoundingClientRect:()=>({top:0,height:100})},preventDefault(){},stopPropagation(){}};
 card("CT001").props.onDragOver(event); await view.settle();
 assert.equal(card("CT001").props["data-drop-side"], "before");
 card("CT001").props.onDrop(event); await view.settle();
 assert.deepEqual(plannedIds(view),["CT002","CT001"]);
 assert.equal([...elements(card("CT002"))].find(n=>n.props.className === "suite-selector-position").props["aria-label"], "1º na execução");
 assert.equal([...elements(card("CT001"))].find(n=>n.props.className === "suite-selector-position").props["aria-label"], "2º na execução");
 button("CT002").props.onKeyDown({altKey:true,key:"ArrowDown",preventDefault(){}}); await view.settle();
 assert.deepEqual(plannedIds(view),["CT001","CT002"]);
 await view.click("Salvar rascunho");
 assert.deepEqual(view.calls.find(call=>call.path.endsWith("/plans") && call.method==="POST").body.items.map(item=>item.case_id),["CT001","CT002"]);
 assert.equal(view.calls.some(call=>call.path.endsWith("/start")),false);
});

test("selecting a checkbox restarts the review button glow but unchecking does not", async t => {
 const view = await workspace(t);
 assert.equal(view.button("Revisar execução").props.className.includes("suite-review-highlight"), false);
 const toggle = async () => { view.find(node => node.props["aria-label"] === "Incluir CT001").props.onChange(); await view.settle(); };
 await toggle();
 assert.equal(view.button("Revisar execução").props.className.includes("suite-review-highlight"), true);
 const firstKey = view.button("Revisar execução").key;
 await toggle();
 assert.equal(view.button("Revisar execução").key, firstKey);
 await toggle();
 assert.notEqual(view.button("Revisar execução").key, firstKey);
 assert.equal(view.calls.some(call => call.path.endsWith("/start")), false);
});

test("unselected selector cards can be dragged before selecting tests", async t => {
 const suite = suiteFixture();
 suite.cases.push({...suite.cases[0],id:"CT002",title:"Segundo teste"});
 const view = await workspace(t,suite,undefined,{initialTab:"planner"});
 const card = id => view.find(n=>n.props["data-selector-case"]===id);
 const button = id => [...elements(card(id))].find(n=>n.type==="button");
 assert.equal(button("CT002").props.draggable,true);
 const transfer=caseTransfer();
 button("CT002").props.onDragStart({dataTransfer:transfer,stopPropagation(){}}); await view.settle();
 const event={dataTransfer:transfer,clientY:5,currentTarget:{getBoundingClientRect:()=>({top:0,height:100})},preventDefault(){},stopPropagation(){}};
 card("CT001").props.onDrop(event); await view.settle();
 assert.deepEqual(view.all().filter(n=>n.props["data-selector-case"]).map(n=>n.props["data-selector-case"]),["CT002","CT001"]);
 assert.deepEqual(plannedIds(view),[]);
 button("CT002").props.onClick(); await view.settle();
 button("CT001").props.onClick(); await view.settle();
 assert.deepEqual(plannedIds(view),[]);
 assert.ok(!card("CT001").props.className.includes("selected"));
 assert.ok(!card("CT002").props.className.includes("selected"));
 for (const id of ["CT002", "CT001"]) {
   view.find(n=>n.props["aria-label"]===`Incluir ${id}`).props.onChange(); await view.settle();
 }
 assert.deepEqual(plannedIds(view),["CT002","CT001"]);
 assert.ok(card("CT001").props.className.includes("selected"));
 assert.ok(card("CT002").props.className.includes("selected"));
 button("CT002").props.onClick(); await view.settle();
 assert.deepEqual(plannedIds(view),["CT002","CT001"]);
 view.find(n=>n.props["aria-label"]==="Incluir CT002").props.onChange(); await view.settle();
 assert.deepEqual(plannedIds(view),["CT001"]);
 assert.ok(!card("CT002").props.className.includes("selected"));
});

test("bulk selection marks displayed cards in their order and clears without changing the catalog", async t => {
 const suite = suiteFixture();
 suite.cases.push(...["CT002", "CT003"].map(id => ({...suite.cases[0], id, title:id})));
 const view = await workspace(t, suite, undefined, {initialTab:"planner"});
 const ids = () => view.all().filter(node => node.props["data-selector-case"]).map(node => node.props["data-selector-case"]);
 assert.equal(view.button("Limpar").props.disabled, true);
 await view.click("Marcar todos");
 assert.deepEqual(plannedIds(view), ["CT001", "CT002", "CT003"]);
 assert.equal(view.button("Marcar todos").props.disabled, true);
 await view.click("Limpar");
 assert.deepEqual(plannedIds(view), []);
 assert.deepEqual(ids(), ["CT001", "CT002", "CT003"]);
 view.find(node => node.props["aria-label"] === "Buscar CTs").props.onChange({target:{value:"CT002"}}); await view.settle();
 await view.click("Marcar todos");
 assert.deepEqual(plannedIds(view), ["CT002"]);
 view.find(node => node.props["aria-label"] === "Buscar CTs").props.onChange({target:{value:""}}); await view.settle();
 await view.click("Marcar todos");
 assert.deepEqual(plannedIds(view), ["CT002", "CT001", "CT003"]);
 await view.click("Limpar");
 assert.deepEqual(plannedIds(view), []);
 assert.equal(view.calls.some(call => call.method !== "GET"), false);
});

test("checking and unchecking cases preserves their catalog positions", async t => {
 const suite=suiteFixture();
 suite.cases.push(...["CT002","CT003","CT004"].map(id=>({...suite.cases[0],id,title:id})));
 const view=await workspace(t,suite,undefined,{initialTab:"planner"});
 const ids=()=>view.all().filter(n=>n.props["data-selector-case"]).map(n=>n.props["data-selector-case"]);
 const original=ids();
 for (const id of ["CT003","CT002","CT004","CT002"]) {
   view.find(n=>n.props["aria-label"]===`Incluir ${id}`).props.onChange(); await view.settle();
   assert.deepEqual(ids(),original);
 }
 assert.deepEqual(plannedIds(view),["CT003","CT004"]);
 const badges=view.all().filter(n=>n.props.className==="suite-selector-position");
 assert.deepEqual(badges.map(text),["1","2"]);
});

test("portable import sends HTML without dispatching and refreshes the newest bases", async t => {
  const ui = await workspace(t, undefined, undefined, { rootId: undefined });
  const input = ui.find(node => node.type === "input" && node.props.type === "file");
  assert.equal(input.props.accept, ".html,.htm,text/html");
  const file = new File(["<html>portable report</html>"], "report.html", { type: "text/html" });
  file.text = () => { throw new Error("Must send the File directly"); };
  input.props.onChange({ target: { files: [file], value: "report.html" } });
  await ui.settle();
  const imported = ui.calls.find(call => call.path === "/test-suites/import");
  assert.ok(imported);
  assert.equal(imported.body, file);
  assert.ok(ui.all().some(node => node.props.role === "status" && text(node).includes("Base importada")));
  assert.equal(ui.calls.filter(call => /start|rerun/.test(call.path)).length, 0);
  assert.equal(ui.all().some(node => text(node) === "Exportar pacote JSON"), false);
  assert.ok(ui.button("Importar relatório HTML"));
});

test("portable import accepts large Files and reports backend format errors", async t => {
  const ui = await workspace(t, undefined, undefined, { rootId: undefined, importError: "HTML antigo é apenas apresentação." });
  const file = new File([" ".repeat(9 * 1024 * 1024), "<html></html>"], "large.html", { type: "text/html" });
  file.text = () => { throw new Error("Must not read the entire File"); };
  ui.find(node => node.type === "input" && node.props.type === "file").props.onChange({ target: { files: [file], value: "large.html" } });
  await ui.settle();
  assert.equal(ui.calls.find(call => call.path === "/test-suites/import").body, file);
  assert.ok(ui.all().some(node => node.props.role === "alert" && text(node).includes("apenas apresentação")));
});

test("base report export scopes executions, enforces one radio and downloads only selected HTML", async t => {
  const group = { ...suiteFixture(), created_at: "2026-10-20T12:00:00Z", latest_activity: "2026-10-21T12:00:00Z", initial_status: "completed", case_count: 1, active_count: 0,
    rounds: [
      { id: "round/a", name: "Rodada histórica", count: 2, status: "completed", created_at: "2026-10-21T12:00:00Z" },
      { id: "draft", name: "Não executada", count: 1, status: "draft", created_at: "2026-10-22T12:00:00Z" },
      { id: "live", name: "Em andamento", count: 1, status: "running", created_at: "2026-10-22T12:00:00Z" },
      { id: "empty", name: "Sem testes", count: 0, status: "failed", created_at: "2026-10-22T12:00:00Z" },
    ], legacy_runs: [{ id: "legacy/a", count: 1, status: "completed", created_at: "2026-10-19T12:00:00Z" }] };
  const html = '<!doctype html><html><script id="autojev-portable-v1" type="application/json">{"snapshot":"selected"}</script></html>';
  const view = await workspace(t, group, undefined, { rootId: undefined,
    groupsReply: [group, { ...group, id: "other-base", app_url: "https://other.invalid", rounds: [{ id: "other-round", name: "Other execution", count: 1, status: "completed" }], legacy_runs: [] }],
    exportResponse: () => new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } }),
  });
  const open = async () => view.click(`Exportar relatório de fixture.invalid, criado em ${new Date(group.created_at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}`);
  const radios = () => view.all().filter(node => node.type === "input" && node.props.type === "radio");
  const select = async key => { const radio = radios().find(node => node.props.value === key); assert.ok(radio); assert.ok(!radio.props.disabled); radio.props.onChange(); await view.settle(); };
  await open();
  assert.equal(view.focus.at(-1).name, "export-dialog");
  assert.equal(radios().length, 5);
  assert.ok(!text(view.find(node => node.type === "dialog")).includes("Rascunho"));
  assert.ok(!text(view.find(node => node.type === "dialog")).includes("Não executada"));
  assert.ok(!radios().some(node => node.props.value === "round:draft"));
  assert.ok(!radios().some(node => node.props.value.includes("other")));
  assert.equal(new Set(radios().map(node => node.props.name)).size, 1);
  assert.equal(view.button("Baixar HTML").props.disabled, true);
  for (const id of ["live", "empty"]) {
    const radio = radios().find(node => node.props.value === `round:${id}`);
    assert.equal(radio.props.disabled, true);
    assert.ok(view.find(node => node.props.id === radio.props["aria-describedby"]));
  }
  await select("initial:root-fixture"); await select("round:round/a");
  assert.deepEqual(radios().filter(node => node.props.checked).map(node => node.props.value), ["round:round/a"]);
  await view.click("Fechar");
  assert.ok(!view.all().some(node => node.type === "dialog"));
  assert.equal(view.calls.filter(call => call.path.includes("report")).length, 0);
  await open();
  view.find(node => node.type === "dialog").props.onCancel({ preventDefault() {} }); await view.settle();
  assert.ok(!view.all().some(node => node.type === "dialog"));
  for (const [key, path] of [
    ["round:round/a", "/test-suites/root-fixture/report?scope=round&round_id=round%2Fa"],
    ["initial:root-fixture", "/test-suites/root-fixture/report?scope=initial"],
    ["linked:legacy/a", "/explorations/legacy%2Fa/report"],
  ]) {
    await open(); assert.equal(view.button("Baixar HTML").props.disabled, true);
    await select(key); await view.click("Baixar HTML");
    assert.equal(view.calls.findLast(call => call.path.includes("/report?")).path.replace(/([?&])export_id=[^&]+/, ""), path);
    assert.match(text(view.find(node => node.type === "dialog")), /Relatório salvo/);
    await view.click("Fechar");
  }
  assert.equal(view.downloads.length, 3);
  assert.ok(view.downloads.every(file => file.html === html && file.filename.endsWith(".html")));
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
  assert.equal(view.calls.filter(call => call.path.includes("/report?")).length, 3);
});

test("report export rejects non HTML without downloading or executing", async t => {
  const view = await workspace(t, { ...suiteFixture(), initial_status: "completed" }, undefined, { rootId: undefined,
    exportResponse: () => new Response('{"report":"not HTML"}', { headers: { "content-type": "application/json" } }),
  });
  await view.click("Exportar relatório");
  view.find(node => node.type === "input" && node.props.type === "radio").props.onChange(); await view.settle();
  await view.click("Baixar HTML");
  assert.match(text(view.find(node => node.props.role === "alert")), /HTML não está disponível/);
  assert.equal(view.downloads.length, 0);
  assert.equal(view.calls.filter(call => call.method !== "GET").length, 0);
});

test("export cancellation aborts request, locks selection and prevents parallel exports", async t => {
  let signal;
  const view = await workspace(t, { ...suiteFixture(), initial_status: "completed" }, undefined, { rootId: undefined,
    exportResponse: options => new Promise((_, reject) => {
      signal = options.signal;
      signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    }),
  });
  await view.click("Exportar relatório");
  const radio = view.find(node => node.type === "input" && node.props.type === "radio");
  radio.props.onChange(); await view.settle();
  const start = view.button("Baixar HTML").props.onClick;
  start(); start(); await view.settle();
  assert.equal(view.calls.filter(call => call.path.includes("report")).length, 1);
  assert.equal(view.find(node => node.type === "fieldset").props.disabled, true);
  assert.match(text(view.find(node => node.type === "dialog")), /Preparando o relatório/);
  assert.doesNotMatch(text(view.find(node => node.type === "dialog")), /Tempo decorrido|Tempo restante|bytes recebidos/);
  assert.equal(view.find(node => node.type === "progress").props.value, undefined);
  assert.equal(signal.aborted, false);
  await view.click("Cancelar exportação");
  assert.equal(signal.aborted, true);
  assert.ok(!view.all().some(node => node.type === "dialog"));
  assert.equal(view.downloads.length, 0);
});
