import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

async function loadApp(reactOverride) {
  const { transpileModule, ModuleKind, JsxEmit, ScriptTarget } = await import("typescript");
  const nativeRequire = createRequire(import.meta.url);
  const dependency = (name) => name === "react" && reactOverride ? reactOverride : nativeRequire(name);
  const compile = async (path, fileName) => transpileModule(await readFile(new URL(path, import.meta.url), "utf8"), {
    fileName, compilerOptions: { module: ModuleKind.CommonJS, jsx: JsxEmit.ReactJSX, target: ScriptTarget.ES2022 },
  }).outputText;
  const board = { exports: {} };
  new Function("require", "module", "exports", await compile("../app/components/RerunLiveBoard.tsx", "RerunLiveBoard.tsx"))(
    (name) => name.endsWith(".css") ? {} : dependency(name), board, board.exports,
  );
  const app = { exports: {} };
  new Function("require", "module", "exports", await compile("../app/AutoJevApp.tsx", "AutoJevApp.tsx"))(
    (name) => name === "./components/RerunLiveBoard" ? board.exports : dependency(name), app, app.exports,
  );
  return { ...app.exports, ...board.exports };
}

function hookRunner(react) {
  const slots = [];
  const effects = [];
  let cursor = 0;
  const changed = (next, old) => !old || !next || next.some((value, index) => !Object.is(value, old[index]));
  return {
    react: {
      ...react,
      useState(initial) {
        const index = cursor++;
        slots[index] ??= { value: typeof initial === "function" ? initial() : initial };
        return [slots[index].value, (value) => { slots[index].value = typeof value === "function" ? value(slots[index].value) : value; }];
      },
      useRef(initial) { const index = cursor++; slots[index] ??= { current: initial }; return slots[index]; },
      useId() { cursor++; return "preparation-consent-test"; },
      useCallback(callback, dependencies) {
        const index = cursor++;
        if (changed(dependencies, slots[index]?.dependencies)) slots[index] = { dependencies, callback };
        return slots[index].callback;
      },
      useEffect(effect, dependencies) {
        const index = cursor++;
        if (changed(dependencies, slots[index]?.dependencies)) {
          slots[index]?.cleanup?.();
          slots[index] = { dependencies };
          effects.push(() => { slots[index].cleanup = effect(); });
        }
      },
    },
    render(component, props) { cursor = 0; return component(props); },
    flush() { while (effects.length) effects.shift()(); },
    dispose() { for (const slot of slots) slot?.cleanup?.(); },
  };
}

function find(tree, predicate) {
  if (!tree || typeof tree !== "object") return undefined;
  if (Array.isArray(tree)) return tree.map((node) => find(node, predicate)).find(Boolean);
  return predicate(tree) ? tree : find(tree.props?.children, predicate);
}
function text(tree) {
  if (tree === null || tree === undefined || typeof tree === "boolean") return "";
  if (typeof tree === "string" || typeof tree === "number") return String(tree);
  if (Array.isArray(tree)) return tree.map(text).join("");
  return text(tree.props?.children);
}
const snapshot = (revision, checked, version = 3) => ({ preparation_version: version, revision, checked });
const data = (extra = {}) => ({
  id: "rerun-1", app_url: "https://example.test/inventory.html", status: "waiting_input", phase: "exploring",
  rerun: { running: true, phase: "waiting_preparation", source_scenario_id: "CT001", preparation: {
    version: 3, attempts: 1, reason_code: "authentication_required",
    expected_url: "https://example.test/inventory.html", current_url: "https://example.test/",
    consent: snapshot(0, false),
  } },
  ...extra,
});
const defer = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

async function harness(request) {
  const hooks = hookRunner(await import("react"));
  const app = await loadApp(hooks.react);
  const originalWindow = globalThis.window;
  const intervals = new Map();
  const refreshes = [];
  let timerId = 0;
  globalThis.window = {
    setInterval(callback, delay) { assert.equal(delay, 1000); intervals.set(++timerId, callback); return timerId; },
    clearInterval(id) { intervals.delete(id); },
  };
  const base = {
    loading: false, providers: [], liveRerun: true, request, toast() {}, navigate() {},
    onRefresh() { refreshes.push(true); }, onCancelled() {}, onReport() {}, apiBase: "http://localhost:8000/api", pollInterval: 3000,
  };
  return {
    intervals, refreshes,
    render(exploration = data()) {
      const root = hooks.render(app.ExplorationDetailView, { ...base, exploration });
      const view = find(root, (node) => node.type === app.ScenarioRerunLiveView);
      const panel = find(view?.props.controls, (node) => typeof node.props?.className === "string" && node.props.className.includes("rerun-intervention"));
      hooks.flush();
      return {
        root, panel,
        checkbox: find(panel, (node) => node.type === "input" && node.props.type === "checkbox"),
        continue: find(panel, (node) => node.type === "button" && node.props.className === "button primary"),
      };
    },
    tick() { for (const callback of [...intervals.values()]) callback(); },
    dispose() { hooks.dispose(); if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow; },
  };
}

test("checking/unchecking in the app only synchronizes consent; an explicit continuation is still required", async () => {
  const calls = [];
  const mutation = defer();
  let remote = snapshot(0, false);
  const ui = await harness(async (path, options) => {
    calls.push({ path, options });
    if (options.method === "POST") {
      if (JSON.parse(options.body).checked) return mutation.promise;
      remote = snapshot(2, false); return remote;
    }
    return remote;
  });
  try {
    ui.render(); await settle(); let panel = ui.render();
    assert.equal(panel.checkbox.props.checked, false);
    assert.equal(panel.continue.props.disabled, true);
    panel.checkbox.props.onChange({ target: { checked: true } });
    panel = ui.render();
    assert.equal(panel.checkbox.props.checked, true, "selection feedback is optimistic and immediate");
    assert.equal(panel.checkbox.props.disabled, true);
    assert.equal(panel.continue.props.disabled, true);
    assert.match(text(panel.continue), /^Sincronizando autorização…$/);
    assert.deepEqual(JSON.parse(calls.at(-1).options.body), { preparation_version: 3, expected_revision: 0, checked: true });
    assert.equal(calls.at(-1).path, "/explorations/rerun-1/preparation-consent");
    panel.continue.props.onClick();
    ui.tick(); await settle();
    assert.equal(calls.length, 2, "neither checking nor polling a pending mutation can resume or attempt login");
    remote = snapshot(1, true); mutation.resolve(remote); await settle(); panel = ui.render();
    assert.equal(panel.checkbox.props.checked, true);
    assert.equal(panel.continue.props.disabled, false);
    panel.checkbox.props.onChange({ target: { checked: false } });
    await settle(); panel = ui.render();
    assert.equal(panel.checkbox.props.checked, false);
    assert.equal(panel.continue.props.disabled, true);
    assert.deepEqual(JSON.parse(calls.at(-1).options.body), { preparation_version: 3, expected_revision: 1, checked: false });
    assert.ok(calls.every(({ path, options }) => path.includes("/preparation-consent") && options.cache === "no-store"));
    assert.equal(ui.refreshes.length, 0, "checkbox changes do not perform the explicit continuation workflow");
  } finally { ui.dispose(); }
  assert.equal(ui.intervals.size, 0);
});

test("a box checked in the browser panel is reflected in the app and does not need a second checkbox click", async () => {
  const calls = [];
  const resume = defer();
  const ui = await harness(async (path, options) => {
    calls.push({ path, options });
    return path.endsWith("/resume") ? resume.promise : snapshot(1, true);
  });
  try {
    ui.render(); await settle(); let panel = ui.render();
    assert.equal(panel.checkbox.props.checked, true);
    assert.equal(panel.continue.props.disabled, false);
    assert.equal(calls.filter(({ options }) => options.method === "POST").length, 0);
    panel.continue.props.onClick(); panel.continue.props.onClick();
    panel = ui.render();
    assert.equal(panel.continue.props.disabled, true);
    assert.match(text(panel.continue), /^Entrando e conferindo preparação…$/);
    assert.equal(calls.filter(({ path }) => path.endsWith("/resume")).length, 1);
    assert.equal(calls.filter(({ path, options }) => path.includes("preparation-consent") && options.method === "POST").length, 0);
    assert.equal(ui.intervals.size, 0, "preparation polling stops while explicit continuation is in flight");
    resume.resolve(data()); await settle();
  } finally { ui.dispose(); }
});

test("a stale GET cannot undo a local consent change, and remote unchecking disables continuation", async () => {
  const oldGet = defer();
  let gets = 0;
  let remote = snapshot(2, false);
  const ui = await harness(async (_path, options) => {
    if (options.method === "POST") return snapshot(1, true);
    return ++gets === 1 ? snapshot(0, false) : gets === 2 ? oldGet.promise : remote;
  });
  try {
    ui.render(); await settle(); let panel = ui.render(); ui.tick();
    panel.checkbox.props.onChange({ target: { checked: true } });
    await settle(); panel = ui.render();
    assert.equal(panel.checkbox.props.checked, true);
    oldGet.resolve(snapshot(0, false)); await settle(); panel = ui.render();
    assert.equal(panel.checkbox.props.checked, true, "an earlier GET cannot overwrite the accepted new choice");
    ui.tick(); await settle(); panel = ui.render();
    assert.equal(panel.checkbox.props.checked, false, "the other window can revoke authorization");
    assert.equal(panel.continue.props.disabled, true);
    remote = snapshot(3, true); ui.tick(); await settle(); panel = ui.render();
    assert.equal(panel.checkbox.props.checked, true);
    assert.equal(panel.continue.props.disabled, false);
  } finally { ui.dispose(); }
});

test("a CAS conflict adopts the current shared authorization without auto retry or resume", async () => {
  const calls = [];
  let gets = 0;
  const ui = await harness(async (path, options) => {
    calls.push({ path, options });
    if (options.method === "POST") throw Object.assign(new Error("A autorização mudou em outra janela."), { status: 409 });
    return snapshot(gets++ ? 2 : 0, false);
  });
  try {
    ui.render(); await settle(); let panel = ui.render();
    panel.checkbox.props.onChange({ target: { checked: true } });
    await settle(); panel = ui.render();
    assert.equal(panel.checkbox.props.checked, false);
    assert.equal(panel.continue.props.disabled, true);
    assert.match(text(panel.panel), /A autorização mudou na outra janela/);
    assert.equal(calls.filter(({ options }) => options.method === "POST").length, 1);
    assert.ok(calls.every(({ path }) => path.includes("preparation-consent")));
  } finally { ui.dispose(); }
});

test("changing preparation, run, phase or cancellation discards pending responses and resets scoped authorization", async () => {
  const late = defer();
  const calls = [];
  const ui = await harness(async (path, options) => {
    calls.push({ path, options });
    return path.includes("preparation_version=3") ? late.promise : snapshot(0, false, 4);
  });
  try {
    const first = data(); ui.render(first);
    const next = data({ rerun: { ...first.rerun, preparation: { ...first.rerun.preparation, version: 4, consent: snapshot(0, false, 4) } } });
    ui.render(next); await settle();
    assert.equal(calls[0].options.signal.aborted, true);
    late.resolve(snapshot(5, true)); await settle(); let panel = ui.render(next);
    assert.equal(panel.checkbox.props.checked, false);
    assert.equal(panel.continue.props.disabled, true);
    const validating = { ...next, rerun: { ...next.rerun, phase: "validating_preparation" } };
    panel = ui.render(validating);
    assert.equal(panel.checkbox.props.checked, false);
    assert.equal(panel.checkbox.props.disabled, true);
    assert.equal(panel.continue.props.disabled, true);
    assert.equal(ui.intervals.size, 0);
    const otherRun = { ...next, id: "rerun-2" }; ui.render(otherRun); await settle(); panel = ui.render(otherRun);
    assert.equal(panel.checkbox.props.checked, false);
    assert.ok(calls.some(({ path }) => path.startsWith("/explorations/rerun-2/")));
    ui.render({ ...otherRun, status: "cancelled", phase: "complete", rerun: { ...otherRun.rerun, running: false, phase: "finished" } });
    assert.equal(ui.intervals.size, 0);
    assert.equal(calls.filter(({ options }) => options.method === "POST").length, 0);
  } finally { ui.dispose(); }
});

test("failed consent synchronization cannot enable the CTA and an explicit retry remains a consent-only POST", async () => {
  const calls = [];
  let posts = 0;
  const ui = await harness(async (path, options) => {
    calls.push({ path, options });
    if (options.method === "POST") {
      if (++posts === 1) throw new Error("Conexão indisponível.");
      return snapshot(1, true);
    }
    return snapshot(0, false);
  });
  try {
    ui.render(); await settle(); let panel = ui.render();
    panel.checkbox.props.onChange({ target: { checked: true } });
    await settle(); panel = ui.render();
    assert.equal(panel.checkbox.props.checked, true);
    assert.equal(panel.continue.props.disabled, true);
    assert.match(text(panel.panel), /A autorização ainda não está sincronizada/);
    find(panel.panel, (node) => node.type === "button" && text(node) === "Tentar sincronizar").props.onClick();
    await settle(); panel = ui.render();
    assert.equal(panel.continue.props.disabled, false);
    assert.equal(posts, 2);
    assert.ok(calls.every(({ path }) => path.includes("preparation-consent")));
  } finally { ui.dispose(); }
});

test("fallback preparation copy exactly matches the backend canonical vocabulary and honors provided fields", async () => {
  const { scenarioRerunPreparationPresentation, ExplorationDetailView } = await loadApp();
  const cases = [
    [{}, "waiting_preparation"],
    [{ reason_code: "authentication_required", reason_label: "Login necessário." }, "waiting_preparation"],
    [{ reason_code: "authentication_required" }, "validating_preparation"],
    [{ reason_code: null, manual_login: { status: "ready", reason_code: null } }, "waiting_preparation"],
    [{ reason_label: "Razão anterior", login_attempt: { status: "rejected", reason_label: "Login recusado pelo site." } }, "waiting_preparation"],
  ];
  const canonical = JSON.parse(execFileSync("python3", ["-c", [
    "import json, runpy",
    "module = runpy.run_path('backend/autojev/services/rerun_preparation_presentation.py')",
    `cases = json.loads(${JSON.stringify(JSON.stringify(cases))})`,
    "print(json.dumps([module['preparation_presentation'](preparation, phase) for preparation, phase in cases], ensure_ascii=False))",
  ].join("\n")], { cwd: new URL("../", import.meta.url), encoding: "utf8" }));
  cases.forEach(([preparation, phase], index) => assert.deepEqual(scenarioRerunPreparationPresentation(preparation, phase === "validating_preparation"), canonical[index]));
  const provided = {
    kicker: "Preparação compartilhada", title: "Confirme a página preparada", instruction: "Confira os dados antes de prosseguir.",
    consent_label: "Autorização canônica recebida do servidor.", button_label: "Continuar reexecução",
    details_label: "Detalhes compartilhados", note: "Marcar não executa o teste.", feedback: "O login ainda precisa ser conferido.",
  };
  const original = data();
  const preparation = { ...original.rerun.preparation, reason_label: "Não usar como título", presentation: provided };
  assert.deepEqual(scenarioRerunPreparationPresentation(preparation), provided);
  const html = renderToStaticMarkup(createElement(ExplorationDetailView, {
    exploration: { ...original, rerun: { ...original.rerun, preparation } }, loading: false, providers: [], liveRerun: true,
    request: async () => { throw new Error("SSR cannot mutate authorization"); }, toast() {}, navigate() {}, onRefresh() {}, onCancelled() {}, onReport() {}, apiBase: "http://localhost:8000/api", pollInterval: 3000,
  }));
  for (const copy of Object.values(provided)) assert.ok(html.includes(copy), `missing canonical shared copy: ${copy}`);
  assert.doesNotMatch(html, /Continuar exploração|Preparei a página e autorizo continuar|Não usar como título/);
});
