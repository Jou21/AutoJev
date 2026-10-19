import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import * as react from "react";
import { renderToStaticMarkup } from "react-dom/server";

async function components(reactOverride) {
  const { transpileModule, ModuleKind, JsxEmit, ScriptTarget } = await import("typescript");
  const nativeRequire = createRequire(import.meta.url);
  const dependency = (name) => name === "react" && reactOverride ? reactOverride : nativeRequire(name);
  const compile = (source, fileName) => transpileModule(source, {
    fileName, compilerOptions: { module: ModuleKind.CommonJS, jsx: JsxEmit.ReactJSX, target: ScriptTarget.ES2022 },
  }).outputText;
  const board = { exports: {} };
  new Function("require", "module", "exports", compile(
    await readFile(new URL("../app/components/RerunLiveBoard.tsx", import.meta.url), "utf8"), "RerunLiveBoard.tsx",
  ))((name) => name.endsWith(".css") ? {} : dependency(name), board, board.exports);
  const app = { exports: {} };
  new Function("require", "module", "exports", compile(
    await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8"), "AutoJevApp.tsx",
  ))((name) => name === "./components/RerunLiveBoard" ? board.exports : dependency(name), app, app.exports);
  return app.exports;
}

const modulePromise = components();
const document = '<html><head><title>Relatório</title></head><body>Dados preservados</body></html>';
const runtimeOf = (html) => JSON.parse(html.match(/<script id="report-app-runtime" type="application\/json">([\s\S]*?)<\/script>/)[1]);

test("legacy three-argument connection keeps its existing offline-capable runtime shape", async () => {
  const { connectReportToApplication } = await modulePromise;
  const html = connectReportToApplication(document, "source-123", "http://localhost:3000");
  assert.deepEqual(runtimeOf(html), { app_url: "http://localhost:3000", source_exploration_id: "source-123" });
  assert.match(html, /Dados preservados/);
  assert.doesNotMatch(html, /api_url/);
});

test("connection adds only the actual optional configured absolute API base, including an external API", async () => {
  const { connectReportToApplication } = await modulePromise;
  for (const [configured, normalized] of [
    ["http://localhost:8000/api", "http://localhost:8000/api"],
    ["http://127.0.0.1:8001/api/", "http://127.0.0.1:8001/api"],
    ["https://configured-api.example/proxy/api/", "https://configured-api.example/proxy/api"],
    ["https://configured-api.example/", "https://configured-api.example"],
  ]) {
    const html = connectReportToApplication(document, "source-123", "http://localhost:3000", configured);
    assert.deepEqual(runtimeOf(html), {
      app_url: "http://localhost:3000", source_exploration_id: "source-123", api_url: normalized,
    });
  }
});

test("invalid API bases cannot introduce credentials, query parameters, fragments or non-HTTP transport", async () => {
  const { connectReportToApplication } = await modulePromise;
  for (const api of [undefined, null, "", "/api", "//api.example/api", "not a URL", "file:///tmp/api",
    "javascript:alert(1)", "data:text/html,unsafe", "ftp://api.example/api", "https://user:secret@api.example/api",
    "https://user@api.example/api", "https://api.example/api?token=private", "https://api.example/api?",
    "https://api.example/api#private", "https://api.example/api#", " https://api.example/api",
    "https://api.example/api\n", "https://api.example/path with spaces"] ) {
    const html = connectReportToApplication(document, "source-123", "http://localhost:3000", api);
    assert.equal(Object.hasOwn(runtimeOf(html), "api_url"), false, String(api));
    assert.match(html, /Dados preservados/);
  }
});

test("reconnection replaces stale bootstrap data and script-escapes source IDs without dispatching anything", async () => {
  const { connectReportToApplication } = await modulePromise;
  const old = document.replace("<head>", '<head><script id="report-app-runtime" type="application/json">{"api_url":"https://old-api.invalid","app_url":"https://old-app.invalid"}</script>');
  const source = 'source</script><script>alert("not executed")</script>';
  const html = connectReportToApplication(old, source, "http://localhost:3000", "https://configured-api.example/api");
  assert.equal((html.match(/id="report-app-runtime"/g) || []).length, 1);
  assert.doesNotMatch(html, /old-api\.invalid|old-app\.invalid|<script>alert/);
  assert.equal(runtimeOf(html).source_exploration_id, source);
  assert.equal(runtimeOf(html).api_url, "https://configured-api.example/api");
  assert.doesNotMatch(html, /\bfetch\(|XMLHttpRequest|confirmScenarioRerun\(/);
});

function findElement(tree, predicate) {
  if (!tree || typeof tree !== "object") return undefined;
  if (Array.isArray(tree)) return tree.map((value) => findElement(value, predicate)).find(Boolean);
  if (predicate(tree)) return tree;
  return findElement(tree.props?.children, predicate) || findElement(tree.props?.actions, predicate);
}

function textOf(tree) {
  if (tree == null || typeof tree === "boolean") return "";
  if (typeof tree === "string" || typeof tree === "number") return String(tree);
  if (Array.isArray(tree)) return tree.map(textOf).join("");
  return textOf(tree.props?.children);
}

test("the actual open-report handler bootstraps exactly the API base used for its request", async () => {
  const hooks = { ...react, useState: (value) => [typeof value === "function" ? value() : value, () => {}],
    useRef: (value) => ({ current: value }), useMemo: (callback) => callback(), useCallback: (callback) => callback(),
    useEffect() {}, useId: () => "test-report" };
  const { ExplorationDetailView } = await components(hooks);
  const apiBase = "https://configured-api.example/proxy/api";
  const globals = { fetch: globalThis.fetch, window: globalThis.window,
    create: URL.createObjectURL, revoke: URL.revokeObjectURL };
  const calls = [];
  let reportBlob;
  const opened = [];
  try {
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options });
      return new Response(document, { headers: { "content-type": "text/html" } });
    };
    globalThis.window = { location: { origin: "http://localhost:3000" }, open: (...args) => opened.push(args), setTimeout() {} };
    URL.createObjectURL = (blob) => { reportBlob = blob; return "blob:test-report"; };
    URL.revokeObjectURL = () => {};
    const tree = ExplorationDetailView({ exploration: {
      id: "source/run", app_url: "https://example.test/inventory.html", status: "completed", report_available: true,
      created_at: "2026-10-18T12:00:00Z", options: {}, scenarios: [], actions: [], events: [],
    }, loading: false, providers: [], request: async () => { throw new Error("Opening must not execute a CT"); },
    toast: (message) => { throw new Error(message); }, navigate() {}, onRefresh() {}, onCancelled() {}, onReport() {},
    apiBase, pollInterval: 3000 });
    const button = findElement(tree, (node) => node.type === "button" && textOf(node).includes("Abrir relatório"));
    assert.ok(button);
    button.props.onClick();
    for (let index = 0; !reportBlob && index < 20; index++) await new Promise((resolve) => setImmediate(resolve));
    assert.ok(reportBlob);
    assert.deepEqual(calls, [{ url: `${apiBase}/explorations/source%2Frun/report`, options: { cache: "no-store" } }]);
    assert.equal(runtimeOf(await reportBlob.text()).api_url, apiBase);
    assert.deepEqual(opened, [["blob:test-report", "_blank", "noopener,noreferrer"]]);
  } finally {
    globalThis.fetch = globals.fetch;
    globalThis.window = globals.window;
    URL.createObjectURL = globals.create;
    URL.revokeObjectURL = globals.revoke;
  }
});

test("rerun primary report follows the validated root metadata rather than an immediate rerun parent", async () => {
  const hooks = { ...react, useState: (value) => [typeof value === "function" ? value() : value, () => {}],
    useRef: (value) => ({ current: value }), useMemo: (callback) => callback(), useCallback: (callback) => callback(),
    useEffect() {}, useId: () => "test-rerun-root-report" };
  const { ExplorationDetailView } = await components(hooks);
  const original = { fetch: globalThis.fetch, window: globalThis.window, create: URL.createObjectURL, revoke: URL.revokeObjectURL };
  const apiBase = "http://localhost:8000/api";
  try {
    for (const fixture of [
      { ownId: "first-child", sourceId: "root/source", reportId: "root/source", reportScenario: "CT147" },
      { ownId: "second-child", sourceId: "intermediate-child", reportId: "root/source", reportScenario: "CT147" },
      // Without validated ancestry metadata, legacy reruns retain their own
      // report. The immediate parent is not guessed to be the absolute root.
      { ownId: "legacy-child", sourceId: "intermediate-child" },
    ]) {
      const calls = [], opened = [], blobs = [], errors = [];
      globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return new Response(document.replace("Dados preservados", `Versão fresca ${calls.length}`), { headers: { "content-type": "text/html" } });
      };
      globalThis.window = { location: { origin: "http://localhost:3000" }, open: (...args) => opened.push(args), setTimeout() {} };
      URL.createObjectURL = (blob) => { blobs.push(blob); return `blob:test-root-${blobs.length}`; };
      URL.revokeObjectURL = () => {};
      const data = { id: fixture.ownId, app_url: "https://example.test/inventory.html", status: "completed", report_available: true,
        created_at: "2026-10-18T12:00:00Z", options: {}, scenarios: [], actions: [], events: [],
        rerun: { running: false, phase: "finished", source_exploration_id: fixture.sourceId, source_scenario_id: "CT001",
          ...(fixture.reportId ? { report_exploration_id: fixture.reportId, report_scenario_id: fixture.reportScenario } : {}) } };
      const tree = ExplorationDetailView({ exploration: data, loading: false, providers: [],
        request: async () => { throw new Error("Opening cannot reexecute a CT or look up ancestry by mutation"); },
        toast: (...args) => errors.push(args), navigate() {}, onRefresh() {}, onCancelled() {}, onReport() {}, apiBase, pollInterval: 3000 });
      const button = findElement(tree, (node) => node.type === "button" && textOf(node) === "Abrir relatório");
      assert.ok(button);
      assert.match(textOf(tree), /Reexecução de CT001/, "The own live/result workspace must not be replaced with root data");
      for (let attempt = 1; attempt <= 2; attempt++) {
        button.props.onClick();
        for (let index = 0; blobs.length < attempt && index < 20; index++) await new Promise((resolve) => setImmediate(resolve));
        const owner = fixture.reportId || fixture.ownId;
        assert.equal(calls.length, attempt);
        assert.deepEqual(calls[attempt - 1], { url: `${apiBase}/explorations/${encodeURIComponent(owner)}/report`, options: { cache: "no-store" } });
        const html = await blobs[attempt - 1].text();
        assert.equal(runtimeOf(html).source_exploration_id, owner);
        assert.match(html, new RegExp(`Versão fresca ${attempt}`));
        assert.deepEqual(opened[attempt - 1], [
          `blob:test-root-${attempt}${fixture.reportScenario ? `#view/results/${encodeURIComponent(fixture.reportScenario)}` : ""}`,
          "_blank", "noopener,noreferrer",
        ]);
      }
      assert.deepEqual(errors, []);
    }
  } finally {
    globalThis.fetch = original.fetch;
    globalThis.window = original.window;
    URL.createObjectURL = original.create;
    URL.revokeObjectURL = original.revoke;
  }
});

test("rerun provenance explains the latest result and preserved previous versions without changing scope", async () => {
  const { ExplorationDetailView } = await modulePromise;
  const html = renderToStaticMarkup(react.createElement(ExplorationDetailView, {
    exploration: { id: "new-run", app_url: "https://example.test/inventory.html", status: "completed",
      created_at: "2026-10-18T12:00:00Z", options: {}, scenarios: [], actions: [], events: [],
      rerun: { running: false, phase: "finished", source_exploration_id: "source-123", source_scenario_id: "CT001" } },
    loading: false, providers: [], request: async () => { throw new Error("Rendering must remain read only"); },
    toast() {}, navigate() {}, onRefresh() {}, onCancelled() {}, onReport() {}, apiBase: "http://localhost:8000/api", pollInterval: 3000,
  }));
  assert.match(html, /As versões anteriores e suas evidências ficam preservadas no histórico do teste/);
  assert.match(html, /o relatório de origem mostra o resultado mais recente/);
  assert.match(html, /SWS, SQL e análises da LLM não são executados/);
  assert.doesNotMatch(html, /O relatório original não é alterado|o resultado original não será alterado/);
});

test("latest projected CT keeps its root ID while every screenshot uses the real evidence owner", async () => {
  const { ScenarioTimelinePanel } = await modulePromise;
  const scenario = { id: "CT147", evidence_exploration_id: "replay/new-run", title: "Resultado mais recente", status: "PASS",
    execution_revision: { exploration_id: "replay/new-run", scenario_id: "CT001", root_scenario_id: "CT147" },
    evidence: { rerun_recipe: { private: "private-recipe-never-rendered" }, timeline: [
      { id: "before", kind: "observation", title: "Antes da ação", screenshot: "before.png" },
      { id: "action", kind: "action", title: "Selecionar opção", action_preview: {
        screenshot: "action-preview.png", private: "private-preview-never-rendered",
      } },
      { id: "after", kind: "observation", title: "Depois da ação", screenshot: "after.png" },
    ] } };
  const original = structuredClone(scenario);
  const html = renderToStaticMarkup(react.createElement(ScenarioTimelinePanel, {
    scenarios: [scenario], contexts: [], explorationId: "root-source", apiBase: "http://localhost:8000/api",
  }));
  assert.match(html, /CT147/);
  for (const filename of ["before.png", "action-preview.png", "after.png"]) {
    assert.ok(html.includes(`http://localhost:8000/api/explorations/replay%2Fnew-run/screenshots/${filename}`));
  }
  assert.doesNotMatch(html, /explorations\/root-source\/screenshots|private-recipe-never-rendered|private-preview-never-rendered/);
  assert.deepEqual(scenario, original);
});

test("legacy CTs and execution contexts retain correct per-record screenshot ownership", async () => {
  const { ScenarioTimelinePanel, ExecutionObservations } = await modulePromise;
  const apiBase = "http://localhost:8000/api";
  const legacy = renderToStaticMarkup(react.createElement(ScenarioTimelinePanel, {
    scenarios: [{ id: "CT001", title: "Registro anterior", status: "PASS", screenshots: ["legacy.png"] }],
    contexts: [], explorationId: "root-source", apiBase,
  }));
  assert.ok(legacy.includes(`${apiBase}/explorations/root-source/screenshots/legacy.png`));
  const contexts = renderToStaticMarkup(react.createElement(ExecutionObservations, {
    contexts: [{ id: "CTX-1", evidence_exploration_id: "replay/context-run", title: "Contexto", status: "INFO",
      evidence: { timeline: [{ kind: "observation", title: "Observação", screenshot: "context.png" }] } }],
    explorationId: "root-source", apiBase,
  }));
  assert.ok(contexts.includes(`${apiBase}/explorations/replay%2Fcontext-run/screenshots/context.png`));
  assert.doesNotMatch(contexts, /explorations\/root-source\/screenshots/);
});
