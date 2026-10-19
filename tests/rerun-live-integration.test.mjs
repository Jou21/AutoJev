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
  componentPromise ??= loadComponents();
  return componentPromise;
}

async function loadComponents(reactOverride) {
    const { transpileModule, ModuleKind, JsxEmit, ScriptTarget } = await import("typescript");
    const nativeRequire = createRequire(import.meta.url);
    const requireDependency = (name) => name === "react" && reactOverride ? reactOverride : nativeRequire(name);
    const compile = (source, fileName) => transpileModule(source, {
      fileName,
      compilerOptions: { module: ModuleKind.CommonJS, jsx: JsxEmit.ReactJSX, target: ScriptTarget.ES2022 },
    }).outputText;
    const board = { exports: {} };
    new Function("require", "module", "exports", compile(
      await readFile(new URL("../app/components/RerunLiveBoard.tsx", import.meta.url), "utf8"), "RerunLiveBoard.tsx",
    ))((name) => name.endsWith(".css") ? {} : requireDependency(name), board, board.exports);
    const app = { exports: {} };
    new Function("require", "module", "exports", compile(
      await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8"), "AutoJevApp.tsx",
    ))((name) => name === "./components/RerunLiveBoard" ? board.exports : requireDependency(name), app, app.exports);
    return { ...app.exports, ...board.exports };
}

function captureHooks(react) {
  const state = [];
  let cursor = 0;
  const pendingEffects = [];
  const changed = (next, old) => !old || !next || next.some((value, index) => !Object.is(value, old[index]));
  return {
    react: {
      ...react,
      useState(initial) {
        const index = cursor++;
        if (!state[index]) state[index] = { value: typeof initial === "function" ? initial() : initial };
        return [state[index].value, (value) => { state[index].value = typeof value === "function" ? value(state[index].value) : value; }];
      },
      useRef(initial) {
        const index = cursor++;
        state[index] ??= { current: initial };
        return state[index];
      },
      useId() { cursor++; return "capture-test-chooser"; },
      useCallback(callback) { cursor++; return callback; },
      useEffect(effect, dependencies) {
        const index = cursor++;
        if (changed(dependencies, state[index]?.dependencies)) {
          state[index]?.cleanup?.();
          state[index] = { dependencies };
          pendingEffects.push(() => { state[index].cleanup = effect(); });
        }
      },
    },
    render(component, props) { cursor = 0; return component(props); },
    flushEffects() { while (pendingEffects.length) pendingEffects.shift()(); },
    dispose() { for (const slot of state) slot?.cleanup?.(); },
  };
}

function findElement(tree, predicate) {
  if (!tree || typeof tree !== "object") return undefined;
  if (Array.isArray(tree)) return tree.map((node) => findElement(node, predicate)).find(Boolean);
  if (predicate(tree)) return tree;
  return findElement(tree.props?.children, predicate);
}

function elementText(tree) {
  if (tree === null || tree === undefined || typeof tree === "boolean") return "";
  if (typeof tree === "string" || typeof tree === "number") return String(tree);
  if (Array.isArray(tree)) return tree.map(elementText).join("");
  return elementText(tree.props?.children);
}

function elementPath(tree, predicate, path = []) {
  if (!tree || typeof tree !== "object") return undefined;
  if (Array.isArray(tree)) {
    for (const [index, child] of tree.entries()) {
      const found = elementPath(child, predicate, [...path, index]);
      if (found) return found;
    }
    return undefined;
  }
  if (predicate(tree)) return { element: tree, path };
  return elementPath(tree.props?.children, predicate, [...path, "children"]);
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
  assert.ok(html.indexOf('id="rerun-live-controls"') < html.indexOf('class="rerun-live-board'), "preparation controls belong before the board in DOM order");
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
  assert.match(html, /<section class="panel rerun-provenance" aria-label="Origem da reexecução">/);
  assert.match(html, /class="rerun-origin-details"/);
  assert.match(html, /class="icon-button rerun-compact-back"/);
  assert.match(html, /<h1>Reexecução de CT001<\/h1>/);
});

test("both rerun screens keep the exact same compact header, collapsed provenance and navigation shell", async () => {
  const base = exploration();
  const states = [
    base,
    exploration({ rerun: { ...base.rerun, phase: "waiting_capture", capture_count: 1, captures_completed: 0,
      capture_checkpoint: { id: "print-1", gap: 2, order: 0, version: 1 } } }),
    exploration({ status: "completed", report_available: true, finished_at: "2026-10-16T15:01:00Z",
      rerun: { ...base.rerun, running: false, phase: "finished", capture_count: 1, captures_completed: 1 } }),
    exploration({ status: "failed", error: "A execução encontrou um erro.",
      rerun: { ...base.rerun, running: false, phase: "failed" } }),
  ];
  const extract = (html, expression, description) => {
    const match = html.match(expression);
    assert.ok(match, `missing ${description}`);
    return match[0];
  };
  const normalizeNavigation = (html) => html.replace(/ class="(?:active)?"/g, "").replace(/ aria-current="page"/g, "");
  for (const data of states) {
    const live = await detailMarkup(data, true);
    const overview = await detailMarkup(data, false);
    for (const html of [live, overview]) {
      assert.match(html, /class="view-stack detail-view rerun-detail\b/,
        "both screens need the same compact sizing and spacing scope");
      assert.match(html, /<h1>Reexecução de CT001<\/h1>/);
      assert.equal((html.match(/aria-label="Voltar para explorações"/g) || []).length, 1);
      const provenance = extract(html, /<details class="rerun-origin-details"[\s\S]*?<\/details>/, "rerun provenance");
      assert.doesNotMatch(provenance.split(">")[0], /\bopen(?:=|\s|$)/,
        "switching screens must not expand provenance and move the view switch");
      assert.ok(html.indexOf('<header class="detail-header"') < html.indexOf('<nav class="rerun-view-switch"'),
        "the view switch must follow the common header in both screens");
    }
    assert.match(live, /\brerun-live-detail\b/);
    assert.doesNotMatch(overview, /class="view-stack detail-view[^"]*rerun-live-detail/);
    assert.equal(extract(live, /<header class="detail-header">[\s\S]*?<\/header>/, "live header"),
      extract(overview, /<header class="detail-header">[\s\S]*?<\/header>/, "overview header"),
      `header title, URL, status and actions must stay unchanged in phase ${data.rerun.phase}`);
    assert.equal(extract(live, /<details class="rerun-origin-details"[\s\S]*?<\/details>/, "live provenance"),
      extract(overview, /<details class="rerun-origin-details"[\s\S]*?<\/details>/, "overview provenance"));
    assert.equal(normalizeNavigation(extract(live, /<nav class="rerun-view-switch"[\s\S]*?<\/nav>/, "live navigation")),
      normalizeNavigation(extract(overview, /<nav class="rerun-view-switch"[\s\S]*?<\/nav>/, "overview navigation")),
      "only the active/aria-current tab may change, never the switch geometry or labels");
  }
});

test("ordinary explorations retain the original non-rerun heading and navigation", async () => {
  for (const liveRerun of [true, false]) {
    const html = await detailMarkup(exploration({ rerun: undefined }), liveRerun);
    assert.match(html, /class="detail-breadcrumb"/);
    assert.match(html, /<h1>example\.test<\/h1>/);
    assert.match(html, /class="app-avatar large"/);
    assert.doesNotMatch(html, /\brerun-detail\b|rerun-origin-details|rerun-compact-back|aria-label="Telas da reexecução"/);
    assert.match(html, /role="tablist" aria-label="Detalhes da exploração"/);
  }
});

test("live dashboard collapses provenance by default and passes compact presentation to its board", async () => {
  const { ScenarioRerunLiveView, RerunLiveBoard } = await components();
  const html = await detailMarkup(exploration());
  const origin = html.match(/<details\b[^>]*class="rerun-origin-details"[^>]*>/);
  assert.ok(origin, "live provenance should be available through a compact disclosure");
  assert.doesNotMatch(origin[0], /\bopen(?:=|\s|>)/, "provenance must start collapsed, saving vertical room");
  assert.equal((html.match(/class="rerun-origin-details"/g) || []).length, 1);
  assert.match(html, /Sobre esta reexecução/);
  assert.match(html, /As versões anteriores e suas evidências ficam preservadas no histórico do teste/);
  assert.match(html, /o relatório de origem mostra o resultado mais recente/);
  assert.equal((html.match(/aria-label="Voltar para explorações"/g) || []).length, 1);
  assert.match(html, /class="rerun-live-workspace"/);
  assert.match(html, /class="rerun-live-board is-compact"/);
  const view = ScenarioRerunLiveView({ rerun: exploration().rerun, active: true, controls: createElement("div", { id: "rerun-live-controls" }) });
  const board = findElement(view, (node) => node.type === RerunLiveBoard);
  assert.ok(board);
  assert.equal(board.props.compact, true, "the live workspace should request the compact board, not just hide it with overflow");
});

test("preparation continuation still requires fresh consent after validation and a rejected login", async () => {
  const hooks = captureHooks(await import("react"));
  const { ExplorationDetailView, ScenarioRerunLiveView } = await loadComponents(hooks.react);
  const initial = exploration();
  const calls = [];
  const request = async (...values) => { calls.push(values); return initial; };
  const props = {
    loading: false, providers: [], liveRerun: true, request,
    toast() {}, navigate() {}, onRefresh() {}, onCancelled() {}, onReport() {},
    apiBase: "http://localhost:8000/api", pollInterval: 3000,
  };
  const renderPreparation = (data) => {
    const parent = hooks.render(ExplorationDetailView, { ...props, exploration: data });
    const view = findElement(parent, (node) => node.type === ScenarioRerunLiveView);
    const panel = findElement(view.props.controls, (node) => typeof node.props?.className === "string" && node.props.className.includes("rerun-intervention"));
    assert.ok(panel, "the preparation panel should remain available in the live control slot");
    return {
      checkbox: findElement(panel, (node) => node.type === "input" && node.props.type === "checkbox"),
      continue: findElement(panel, (node) => node.type === "button" && node.props.className === "button primary"),
    };
  };
  let panel = renderPreparation(initial);
  assert.equal(panel.checkbox.props.checked, false);
  assert.equal(panel.checkbox.props.disabled, false);
  assert.equal(panel.continue.props.disabled, true);
  panel.continue.props.onClick();
  assert.deepEqual(calls, [], "clicking without consent must never attempt login or resume");
  panel.checkbox.props.onChange({ target: { checked: true } });
  panel = renderPreparation(initial);
  assert.equal(panel.continue.props.disabled, false);
  assert.deepEqual(calls, [], "checking the consent box is not itself confirmation to resume");

  const validating = { ...initial, rerun: { ...initial.rerun, phase: "validating_preparation" } };
  panel = renderPreparation(validating);
  assert.equal(panel.checkbox.props.disabled, true);
  assert.equal(panel.continue.props.disabled, true);
  panel.continue.props.onClick();
  assert.deepEqual(calls, [], "validation in progress cannot be submitted again");

  const rejected = { ...initial, rerun: { ...initial.rerun, preparation: {
    ...initial.rerun.preparation, version: 2, attempts: 1,
    login_attempt: { status: "rejected", reason: "login_rejected", reason_label: "O site recusou o login. Confira usuário e senha." },
  } } };
  panel = renderPreparation(rejected);
  assert.equal(panel.checkbox.props.checked, false, "the previous preparation version cannot authorize a rejected-login retry");
  assert.equal(panel.checkbox.props.disabled, false);
  assert.equal(panel.continue.props.disabled, true);
  panel.continue.props.onClick();
  assert.deepEqual(calls, []);
  hooks.dispose();
});

test("compact preparation preserves long page URLs and full instructions without expanding its default guidance", async () => {
  const base = exploration();
  const expected = `https://example.test/${"very-long-original-segment-".repeat(20)}/inventory.html`;
  const current = `https://example.test/${"very-long-login-segment-".repeat(20)}/login.html`;
  const instruction = "Prepare também o carrinho original e confira as opções antes de continuar. <script>not executable</script>";
  const data = exploration({ rerun: { ...base.rerun, preparation: {
    ...base.rerun.preparation, expected_url: expected, current_url: current,
    reason_code: "before_url_changed", reason_label: "A página aberta não é a mesma do teste original.", instruction,
  } } });
  const html = await detailMarkup(data);
  assert.match(html, /class="rerun-preparation-guide is-compact"/);
  assert.ok(html.includes(`<strong title="${expected}">${expected}</strong>`), "the full expected URL remains available in its title, even when visually ellipsized");
  assert.ok(html.includes(`<strong title="${current}">${current}</strong>`));
  assert.match(html, /Nenhuma ação do CT foi executada/);
  assert.match(html, /class="rerun-preparation-next-step"/);
  assert.match(html, /Prepare a página e os dados iniciais do teste/);
  const details = html.match(/<details\b[^>]*class="rerun-preparation-instructions"[^>]*>/);
  assert.ok(details);
  assert.doesNotMatch(details[0], /\bopen(?:=|\s|>)/);
  assert.match(html, /Ver detalhes da preparação/);
  assert.match(html, /Prepare também o carrinho original/);
  assert.match(html, /&lt;script&gt;not executable&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|before_url_changed/);
  assert.equal((html.match(/>Continuar reexecução<\/button>/g) || []).length, 1);

  const standard = await detailMarkup(data, false);
  assert.match(standard, /class="rerun-preparation-guide"/);
  assert.doesNotMatch(standard, /rerun-preparation-next-step/);
  assert.match(standard, /Ver detalhes da preparação/);
  assert.match(standard, /Prepare também o carrinho original/);
});

test("a detected login gives a specific heading and direct instructions instead of a wrong-page warning", async () => {
  const base = exploration();
  const login = exploration({ rerun: { ...base.rerun, preparation: {
    ...base.rerun.preparation, reason_code: "authentication_required",
    reason_label: "Você está na tela de login. Preencha usuário e senha para continuar.",
  } } });
  for (const live of [true, false]) {
    const html = await detailMarkup(login, live);
    assert.match(html, /<h2>Faça login para continuar<\/h2>/);
    assert.match(html, /Você está na tela de login/);
    assert.match(html, /Marque a autorização e faça login pelo próprio site/);
    assert.match(html, /continua automaticamente após conferir a página e os pré-requisitos/);
    assert.match(html, /Ver detalhes da preparação/);
    assert.doesNotMatch(html, /A página aberta não é a mesma|Ajuste a página e tente novamente/);
    assert.match(html, /Nenhuma ação do CT foi executada/);
    assert.match(html, /<input type="checkbox"/);
    assert.match(html, /Autorizo tentar entrar com o login preenchido/);
    assert.match(html, /<button class="button primary" disabled="">[\s\S]*?Continuar reexecução<\/button>/);
  }
  const incorrectPage = await detailMarkup(exploration({ rerun: { ...base.rerun, preparation: {
    ...base.rerun.preparation, reason_code: "before_url_changed", reason_label: "A página aberta não é a mesma do teste original.",
  } } }));
  assert.match(incorrectPage, /Prepare a página para continuar/);
  assert.match(incorrectPage, /A página aberta não é a mesma/);
  assert.doesNotMatch(incorrectPage, /Você está na tela de login|Ver página de destino após o login/);
});

test("compact validation and rejected-login feedback stay clear without granting continuation or claiming a test passed", async () => {
  const base = exploration();
  const validating = await detailMarkup(exploration({ rerun: { ...base.rerun, phase: "validating_preparation" } }));
  assert.match(validating, /Aguarde a conferência do login, da página e dos pré-requisitos/);
  assert.match(validating, /Não é necessário confirmar novamente/);
  assert.match(validating, /<input type="checkbox" disabled=""/);
  assert.match(validating, /<button class="button primary" disabled="">[\s\S]*?Entrando e conferindo preparação…<\/button>/);
  assert.equal((validating.match(/class="panel intervention-card /g) || []).length, 1);

  const rejected = await detailMarkup(exploration({ rerun: { ...base.rerun, preparation: {
    ...base.rerun.preparation, version: 2, attempts: 1,
    login_attempt: { status: "rejected", reason: "login_rejected", reason_label: "O site recusou o login. Confira usuário e senha." },
  } } }));
  assert.match(rejected, /Login não realizado/);
  assert.match(rejected, /O site recusou o login/);
  assert.match(rejected, /<button class="button primary" disabled="">[\s\S]*?Continuar reexecução<\/button>/);
  assert.doesNotMatch(rejected, /login_rejected|CT aprovado|Teste aprovado|Login realizado/);
});

test("sidebar preparation styles are vertically scoped and keep its consent and action in normal flow", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const rule = (selector) => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
    assert.ok(match, `missing scoped preparation rule: ${selector}`);
    return match[1];
  };
  const panel = rule(".rerun-live-controls .rerun-intervention");
  assert.match(panel, /display:\s*flex/);
  assert.match(panel, /flex-direction:\s*column/);
  assert.match(panel, /align-items:\s*stretch/);
  assert.match(panel, /min-width:\s*0/);
  assert.match(panel, /max-width:\s*100%/);
  assert.doesNotMatch(panel, /position:\s*(?:absolute|fixed)|overflow(?:-x|-y)?:\s*(?:hidden|clip)/);
  assert.match(rule(".rerun-live-controls .rerun-intervention > *"), /min-width:\s*0/);
  assert.match(rule(".rerun-live-controls .rerun-page-comparison"), /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  const url = rule(".rerun-live-controls .rerun-page-comparison strong");
  assert.match(url, /text-overflow:\s*ellipsis/);
  assert.match(url, /white-space:\s*nowrap/);
  const button = rule(".rerun-live-controls .rerun-intervention > .button");
  assert.match(button, /position:\s*static/);
  assert.match(button, /width:\s*100%/);
  assert.match(button, /white-space:\s*normal/);
  assert.match(rule(".intervention-wide"), /grid-template-columns:\s*auto\s+minmax\(0,\s*1fr\)\s+auto/, "the normal full-width layout is not globally replaced");
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
  assert.ok(html.indexOf('id="rerun-live-controls"') < html.indexOf('class="rerun-live-board'), "the print uploader retains its accessible DOM position before the board");
  assert.ok(html.indexOf('aria-label="Aguardar print"') < html.indexOf('class="rerun-live-board'));
  assert.doesNotMatch(html, /class="rerun-live-attention"/);
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

test("selecting another card changes the inspected detail but not the waiting checkpoint or upload controls", async () => {
  const { ScenarioRerunLiveView, ScenarioRerunCapture } = await components();
  const checkpoint = { id: "db-1", gap: 2, order: 0, version: 3 };
  const steps = [
    step("source-0", "observation", "Antes da ação", "done"),
    step("source-1", "action", "Selecionar opção", "done"),
    step("checkpoint-db-1", "capture_wait", "Aguardar print", "waiting", { checkpoint_id: "db-1" }),
    step("source-2", "observation", "Depois da ação", "done"),
  ];
  const rerun = { phase: "waiting_capture", source_scenario_id: "CT001", capture_checkpoint: checkpoint, step_progress: progress(steps) };
  const unchanged = structuredClone(rerun);
  const submissions = [];
  const controls = createElement("div", { id: "rerun-live-controls" }, createElement(ScenarioRerunCapture, {
    checkpoint, total: 1, onCapture: (image) => submissions.push(image),
  }));
  for (const selectedStepId of ["checkpoint-db-1", "source-1", "source-2"]) {
    const html = renderToStaticMarkup(createElement(ScenarioRerunLiveView, { rerun, active: true, selectedStepId, controls }));
    assert.match(html, /data-step-id="checkpoint-db-1" data-step-state="waiting"/);
    assert.equal((html.match(/aria-label="Aguardar print"/g) || []).length, 1);
    assert.equal((html.match(/type="file"/g) || []).length, 1);
    assert.equal((html.match(/>Anexar print e continuar<\/button>/g) || []).length, 1);
    assert.ok(html.indexOf('id="rerun-live-controls"') < html.indexOf('class="rerun-live-board'));
  }
  assert.deepEqual(rerun, unchanged);
  assert.deepEqual(submissions, [], "inspecting any card must not consume or confirm its print checkpoint");
});

test("staging a print requires explicit submission and survives polls of the same checkpoint", async () => {
  const react = await import("react");
  const hooks = captureHooks(react);
  const { ScenarioRerunCapture, scenarioRerunCaptureKey } = await loadComponents(hooks.react);
  const originals = Object.fromEntries(["window", "document", "Element", "FileReader"].map((key) => [key, globalThis[key]]));
  const listeners = new Map();
  globalThis.window = {
    addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name, callback) { if (listeners.get(name) === callback) listeners.delete(name); },
    matchMedia() { return { matches: true }; },
  };
  globalThis.document = { activeElement: null, querySelector() { return null; }, getElementById() { return null; } };
  globalThis.Element = class {};
  const imageDataUrl = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==";
  globalThis.FileReader = class {
    readAsDataURL() { queueMicrotask(() => { this.result = imageDataUrl; this.onload?.(); }); }
  };
  const checkpoint = { id: "db-1", gap: 2, order: 0, version: 3 };
  const submitted = [];
  const scrolls = [];
  const props = { checkpoint, total: 1, onCapture: (value) => submitted.push(value) };
  const render = (extra = {}) => {
    const tree = hooks.render(ScenarioRerunCapture, { ...props, ...extra });
    const section = findElement(tree, (node) => node.type === "section" && node.props.ref);
    if (section) section.props.ref.current = { scrollIntoView: (options) => scrolls.push(options) };
    const heading = findElement(tree, (node) => node.type === "h2" && node.props.ref);
    if (heading) heading.props.ref.current = { focus() {} };
    hooks.flushEffects();
    return tree;
  };
  try {
    let tree = render();
    const file = {
      name: "chosen-user-print.png", size: 16, type: "image/png",
      slice() { return { arrayBuffer: async () => Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10]).buffer }; },
    };
    findElement(tree, (node) => node.type === "input" && node.props.type === "file").props.onChange({ target: { files: [file], value: "chosen-user-print.png" } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    tree = render();
    assert.equal(findElement(tree, (node) => node.type === "img")?.props.src, imageDataUrl);
    assert.match(elementText(tree), /Print selecionado\. Falta confirmar o envio\./);
    assert.match(elementText(tree), /Selecionado, ainda não enviado/);
    assert.doesNotMatch(elementText(tree), /print enviado|imagem anexada|enviado com sucesso/i);
    assert.deepEqual(submitted, [], "file selection is local staging, not confirmation");
    const feedbackScrolls = scrolls.length;

    tree = render({ checkpoint: { ...checkpoint }, completed: 0 });
    assert.equal(findElement(tree, (node) => node.type === "img")?.props.src, imageDataUrl, "a fresh polling object with identical id/version must keep the selected image");
    assert.equal(scenarioRerunCaptureKey({ id: "run", rerun: { capture_checkpoint: checkpoint } }), scenarioRerunCaptureKey({ id: "run", rerun: { capture_checkpoint: { ...checkpoint } } }));
    assert.deepEqual(submitted, []);
    assert.equal(scrolls.length, feedbackScrolls, "polling must not repeatedly scroll or focus the uploader");

    findElement(tree, (node) => typeof node.props?.onDrop === "function").props.onDrop({
      preventDefault() {}, dataTransfer: { files: [file] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    tree = render();
    assert.equal(findElement(tree, (node) => node.type === "img")?.props.src, imageDataUrl);
    assert.deepEqual(submitted, [], "dropping an image must not confirm it");

    listeners.get("paste")({ target: null, clipboardData: { files: [file] }, preventDefault() {} });
    await new Promise((resolve) => setTimeout(resolve, 0));
    tree = render({ error: "O envio falhou. Tente novamente." });
    assert.equal(findElement(tree, (node) => node.type === "img")?.props.src, imageDataUrl, "an API error must keep the staged image for an explicit retry");
    assert.deepEqual(submitted, [], "pasting an image must not confirm it");

    const confirm = findElement(tree, (node) => node.type === "button" && node.props.className === "button primary");
    assert.equal(confirm.props.disabled, false);
    confirm.props.onClick();
    confirm.props.onClick();
    assert.deepEqual(submitted, [imageDataUrl], "only the explicit confirmation CTA submits the selected image");

    render({ checkpoint: { ...checkpoint, version: 4 } });
    tree = render({ checkpoint: { ...checkpoint, version: 4 } });
    assert.equal(findElement(tree, (node) => node.type === "img"), undefined, "a genuinely new checkpoint version cannot reuse the old selection");
    assert.deepEqual(submitted, [imageDataUrl], "changing the checkpoint never auto-submits");
  } finally {
    hooks.dispose();
    for (const [key, value] of Object.entries(originals)) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});

test("switching live and standard views keeps the upload component in the same React position and key", async () => {
  const hooks = captureHooks(await import("react"));
  const { ExplorationDetailView, ScenarioRerunLiveView, ScenarioRerunCapture } = await loadComponents(hooks.react);
  const base = exploration();
  const checkpoint = { id: "db-1", gap: 2, order: 0, version: 3 };
  const data = exploration({ rerun: {
    ...base.rerun, phase: "waiting_capture", capture_checkpoint: checkpoint, capture_count: 1,
  } });
  const props = {
    exploration: data, loading: false, providers: [], request() { throw new Error("navigation must not submit"); },
    toast() {}, navigate() {}, onRefresh() {}, onCancelled() {}, onReport() {},
    apiBase: "http://localhost:8000/api", pollInterval: 3000,
  };
  let preserved;
  for (const liveRerun of [true, false, true]) {
    const parent = hooks.render(ExplorationDetailView, { ...props, liveRerun });
    const view = elementPath(parent, (node) => node.type === ScenarioRerunLiveView);
    assert.ok(view, "the same live-view component must stay mounted in standard mode");
    assert.equal(view.element.props.showLiveContent, liveRerun);
    const renderedView = ScenarioRerunLiveView(view.element.props);
    assert.equal(renderedView.type, "section", "the root element type must not change across modes");
    assert.equal(renderedView.props["aria-label"], liveRerun ? "Acompanhamento dos passos da reexecução" : undefined);
    const controls = elementPath(renderedView, (node) => node.props?.id === "rerun-live-controls");
    const capture = elementPath(renderedView, (node) => node.type === ScenarioRerunCapture);
    assert.ok(controls);
    assert.ok(capture);
    assert.equal(capture.element.props.checkpoint, checkpoint);
    const identity = { parent: view.path, controls: controls.path, capture: capture.path, captureKey: capture.element.key };
    if (preserved) assert.deepEqual(identity, preserved, "moving the uploader between branches would remount it and discard a staged print");
    else preserved = identity;
  }
  hooks.dispose();
  const standard = await detailMarkup(data, false);
  assert.equal((standard.match(/type="file"/g) || []).length, 1);
  assert.equal((standard.match(/>Anexar print e continuar<\/button>/g) || []).length, 1);
  assert.match(standard, /class="rerun-standard-page"/);
  assert.doesNotMatch(standard, /class="rerun-live-board\b/);
  assert.match(standard, /class="rerun-standard-workspace"/);
  assert.doesNotMatch(standard, /class="rerun-live-workspace"/);
  assert.doesNotMatch(standard, /aria-label="Acompanhamento dos passos da reexecução"/);
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
