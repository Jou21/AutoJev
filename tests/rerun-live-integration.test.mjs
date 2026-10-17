import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Load the actual client components without requiring a browser or compiling
// another production bundle. CSS is intentionally inert in these SSR tests.
let componentPromise;
function components() {
  componentPromise ??= (async () => {
    const { transpileModule, ModuleKind, JsxEmit, ScriptTarget } = await import("typescript");
    const nativeRequire = createRequire(import.meta.url);
    const compile = (source, fileName) => transpileModule(source, {
      fileName,
      compilerOptions: { module: ModuleKind.CommonJS, jsx: JsxEmit.ReactJSX, target: ScriptTarget.ES2022 },
    }).outputText;
    const board = { exports: {} };
    new Function("require", "module", "exports", compile(
      await readFile(new URL("../app/components/RerunLiveBoard.tsx", import.meta.url), "utf8"), "RerunLiveBoard.tsx",
    ))((name) => name.endsWith(".css") ? {} : nativeRequire(name), board, board.exports);
    const app = { exports: {} };
    new Function("require", "module", "exports", compile(
      await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8"), "AutoJevApp.tsx",
    ))((name) => name === "./components/RerunLiveBoard" ? board.exports : nativeRequire(name), app, app.exports);
    return { ...app.exports, ...board.exports };
  })();
  return componentPromise;
}

function step(id, kind, title, state = "pending", fields = {}) {
  return {
    id, kind, title, state,
    label: state === "done" ? "Concluída" : state === "waiting" ? "Aguardando você" : state === "failed" ? "Reprovada" : "Pendente",
    description: `${title}: descrição desta tentativa.`,
    source_index: Number(id.replace(/\D/g, "")) || 0,
    ...fields,
  };
}

function progress(steps, fields = {}) {
  return {
    version: 1,
    steps,
    current_id: steps.find((entry) => ["waiting", "active"].includes(entry.state))?.id || null,
    completed: steps.filter((entry) => entry.state === "done").length,
    total: steps.length,
    ...fields,
  };
}

function exploration(fields = {}) {
  return {
    id: "replay-123", app_url: "https://example.test/inventory.html", status: "waiting_input",
    created_at: "2026-10-16T15:00:00Z", started_at: "2026-10-16T15:00:00Z",
    options: { auxiliary_evidence_mode: "text" }, scenarios: [], actions: [], events: [],
    rerun: {
      running: true, phase: "waiting_preparation", source_exploration_id: "source-123", source_scenario_id: "CT001",
      preparation: { version: 1, attempts: 0, expected_url: "https://example.test/inventory.html", current_url: "https://example.test/" },
    },
    ...fields,
  };
}

async function detailMarkup(data, liveRerun = true) {
  const { ExplorationDetailView } = await components();
  const calls = [];
  const html = renderToStaticMarkup(createElement(ExplorationDetailView, {
    exploration: data, loading: false, providers: [], liveRerun,
    request: async (...args) => { calls.push(args); throw new Error("SSR must not issue requests"); },
    toast() {}, navigate() {}, onRefresh() {}, onCancelled() {}, onReport() {},
    apiBase: "http://localhost:8000/api", pollInterval: 3000,
  }));
  assert.deepEqual(calls, [], "rendering or selecting the live screen must not dispatch, resume, cancel or upload");
  return html;
}

test("live rerun route retains the ordinary detail loader and decodes the run id", async () => {
  const { parseAppHash } = await components();
  assert.deepEqual(parseAppHash("#/exploracoes/run%2F123/acompanhar"), { view: "detail", explorationId: "run/123", liveRerun: true });
  assert.deepEqual(parseAppHash("#/exploracoes/run-123"), { view: "detail", explorationId: "run-123" });
  assert.deepEqual(parseAppHash("#/exploracoes/%E0%A4%A/acompanhar"), { view: "dashboard" });
  assert.deepEqual(parseAppHash("#/exploracoes/run-123/acompanhar/extra"), { view: "dashboard" });
});

test("live screen preserves a single preparation consent and continuation control", async () => {
  const data = exploration();
  const html = await detailMarkup(data);
  assert.match(html, /Acompanhamento dos passos da reexecução/);
  assert.match(html, /Seu teste,/);
  assert.equal((html.match(/class="panel intervention-card /g) || []).length, 1);
  assert.equal((html.match(/Autorizo tentar entrar com o login preenchido/g) || []).length, 1);
  assert.equal((html.match(/>Continuar reexecução<\/button>/g) || []).length, 1);
  assert.match(html, /disabled=""[^>]*>[\s\S]*?Continuar reexecução<\/button>/);
  assert.match(html, /id="rerun-live-controls"/);
  assert.doesNotMatch(html, /class="execution-command panel/);
  assert.doesNotMatch(html, /role="tablist" aria-label="Detalhes da exploração"/);
});

test("standard view retains its results workspace and offers the separate live screen", async () => {
  const html = await detailMarkup(exploration(), false);
  assert.doesNotMatch(html, /aria-label="Acompanhamento dos passos da reexecução"/);
  assert.match(html, /role="tablist" aria-label="Detalhes da exploração"/);
  assert.match(html, /class="execution-command panel/);
  assert.match(html, /Acompanhar reexecução/);
  assert.match(html, /Visão geral e resultados/);
  assert.equal((html.match(/class="panel intervention-card /g) || []).length, 1);
});

test("waiting print live screen keeps the existing upload controls exactly once", async () => {
  const base = exploration();
  const data = exploration({ rerun: {
    ...base.rerun, phase: "waiting_capture", capture_count: 1, captures_completed: 0,
    capture_checkpoint: { id: "db-1", gap: 2, order: 0, version: 1 },
    step_progress: progress([
      step("source-0", "observation", "Antes da ação", "done"),
      step("source-1", "action", "Selecionar opção", "done"),
      step("capture-db-1", "capture", "Aguardar print", "waiting"),
      step("source-2", "observation", "Depois da ação", "done"),
      step("source-3", "assertion", "Opção selecionada", "done"),
    ]),
  } });
  const html = await detailMarkup(data);
  assert.equal((html.match(/aria-label="Aguardar print"/g) || []).length, 1);
  assert.equal((html.match(/type="file"/g) || []).length, 1);
  assert.equal((html.match(/>Anexar print e continuar<\/button>/g) || []).length, 1);
  assert.match(html, /Ctrl/);
  assert.match(html, /arraste um arquivo/);
  assert.match(html, /Posição planejada: após a etapa 2/);
  assert.doesNotMatch(html, /Autorizo tentar entrar com o login preenchido/);
  assert.match(html, /aria-controls="rerun-live-controls"/);
  assert.match(html, /Aguardando seu print/);
});

test("live details select the active print by default without changing planned source order", async () => {
  const { ScenarioRerunLiveView } = await components();
  const steps = [
    step("source-0", "observation", "Antes da ação", "done"),
    step("source-1", "action", "Selecionar opção", "done"),
    step("capture-db-1", "capture", "Aguardar print", "waiting", { description: "Cole qualquer imagem para continuar esta tentativa." }),
    step("source-2", "observation", "Depois da ação", "done"),
    step("source-3", "assertion", "Opção selecionada", "done"),
  ];
  const html = renderToStaticMarkup(createElement(ScenarioRerunLiveView, {
    rerun: { source_scenario_id: "CT001", phase: "waiting_capture", step_progress: progress(steps) }, active: true,
  }));
  assert.match(html, /class="rerun-live-step-detail" data-step-state="waiting"/);
  assert.match(html, /Etapa atual/);
  assert.match(html, /Cole qualquer imagem para continuar esta tentativa/);
  const cardsEnd = html.indexOf('class="rerun-live-step-detail"');
  const cards = html.slice(0, cardsEnd);
  let previous = -1;
  for (const title of steps.map((entry) => entry.title)) {
    const index = cards.indexOf(title, previous + 1);
    assert.ok(index > previous, `expected source/planned order for ${title}`);
    previous = index;
  }
});

test("a selected failed check displays its own new result and never a completed detail check", async () => {
  const { ScenarioRerunLiveView } = await components();
  const steps = [
    step("source-0", "observation", "Antes da ação", "done"),
    step("source-1", "action", "Selecionar opção", "done"),
    step("source-2", "assertion", "Opção selecionada", "failed", {
      description: "A opção encontrada nesta tentativa é diferente da solicitada.",
      occurred_at: "2026-10-16T15:00:02Z",
    }),
  ];
  const html = renderToStaticMarkup(createElement(ScenarioRerunLiveView, {
    rerun: { source_scenario_id: "CT001", phase: "finished", step_progress: progress(steps) },
    selectedStepId: "source-2", active: false,
  }));
  assert.match(html, /class="rerun-live-step-detail" data-step-state="failed"/);
  assert.match(html, /A opção encontrada nesta tentativa é diferente da solicitada/);
  assert.match(html, /datetime="2026-10-16T15:00:02Z"/i);
  assert.doesNotMatch(html, /class="rerun-live-step-detail" data-step-state="done"/);
  assert.doesNotMatch(html, /Atualização a cada/);
});

test("rerun live view without a real rerun does not invent source cards or hide normal results", async () => {
  const html = await detailMarkup(exploration({ rerun: undefined }), true);
  assert.doesNotMatch(html, /aria-label="Acompanhamento dos passos da reexecução"/);
  assert.doesNotMatch(html, /aria-label="Telas da reexecução"/);
  assert.match(html, /role="tablist" aria-label="Detalhes da exploração"/);
});

test("an old rerun without a preserved progress plan shows no invented successful stages", async () => {
  const { ScenarioRerunLiveView } = await components();
  const html = renderToStaticMarkup(createElement(ScenarioRerunLiveView, {
    rerun: { source_scenario_id: "CT001", phase: "finished" }, active: false,
  }));
  assert.match(html, /rerun-board-empty/);
  assert.doesNotMatch(html, /data-step-state="done"/);
  assert.doesNotMatch(html, /data-step-id=/);
  assert.doesNotMatch(html, /rerun-live-step-detail/);
});

test("the same live route keeps the terminal report polling grace and does not poll indefinitely", async () => {
  const { explorationDetailState } = await components();
  const now = Date.parse("2026-10-16T15:05:00Z");
  const completed = exploration({ status: "completed", report_available: false, finished_at: "2026-10-16T15:04:50Z", rerun: { phase: "finished" } });
  assert.equal(explorationDetailState(completed.id, completed, false, completed.id, now).shouldPoll, true);
  assert.equal(explorationDetailState(completed.id, { ...completed, report_available: true }, false, completed.id, now).shouldPoll, false);
  assert.equal(explorationDetailState(completed.id, completed, false, completed.id, now + 120000).shouldPoll, false);
});
