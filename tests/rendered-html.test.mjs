import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const root = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the AutoJev application shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>AutoJev — Exploração inteligente de aplicações<\/title>/i);
  assert.match(html, /AutoJev/);
  assert.match(html, /JEV no comando|Boa exploração começa/);
  assert.match(html, /Nova exploração/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("removes disposable starter assets and metadata", async () => {
  const [page, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(page, /AutoJevApp/);
  assert.match(layout, /pt-BR/);
  assert.match(layout, /AutoJev/);
  assert.doesNotMatch(layout, /codex-preview|Starter Project/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.match(packageJson, /lucide-react/);
  await assert.rejects(access(new URL("app/_sites-preview", root)));
});

test("offers an optional focused exploration scope", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");

  assert.match(source, /useState<"all" \| "focused">\("all"\)/);
  assert.match(source, /Tela, menu ou submenu/);
  assert.match(source, /Toda a aplicação/);
  assert.match(source, /Tela\/menu específico/);
  assert.match(source, /Configurações &gt; Usuários &gt; Permissões|Configurações > Usuários > Permissões/);
  assert.match(source, /focusMode === "focused" && !focusArea\.trim\(\)/);
  assert.match(source, /if \(normalizedFocusArea\) explorationOptions\.focus_area = normalizedFocusArea/);
  assert.match(source, /explorationFocusArea\(exploration\.options\)/);
  assert.match(source, /max_steps: null/);
  assert.match(source, /max_duration_seconds: null/);
  assert.match(source, /Sem teto fixo de ações ou duração/);
  assert.match(source, /Ao esgotar a cobertura/);
});

test("shows test-case creation running alongside browser exploration", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");

  assert.match(source, /Explorar/);
  assert.match(source, /Criar CTs/);
  assert.match(source, /scenariosRunningInParallel/);
  assert.match(source, /parallelScenarioPipelineStarted/);
  assert.match(source, /terminalOutcomeVisible && completedWithRestrictions/);
  assert.match(source, /Exploração \+ criação contínua de CTs/);
  assert.match(source, /preservados ao vivo/);
  assert.match(source, /phase === "documenting" \|\| scenariosRunningInParallel/);
  assert.match(source, /CT \{Math\.min\(phaseProgress\.current/);
  assert.match(source, /O número 67 representa os CTs gerados/);
  assert.match(source, /Linha do tempo explicada/);
  assert.match(source, /detailTab === "evidence"/);
  assert.match(source, /coverage_complete_with_restrictions/);
  assert.match(source, /Concluída com restrições/);
  assert.match(source, /sinais técnicos/);
  assert.match(source, /Cada ocorrência aparece na linha do tempo do CT/);
});

test("offers economical text and optional visual auxiliary evidence modes", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");

  assert.match(source, /useState<"text" \| "visual">\("text"\)/);
  assert.match(source, /auxiliary_evidence_mode: auxiliaryEvidenceMode/);
  assert.match(source, /Econômico · texto/);
  assert.match(source, /Visual · texto \+ imagem/);
  assert.match(source, /imagem continua salva localmente/);
  assert.match(source, /modelAcceptsImages/);
  assert.match(source, /available\.filter\(modelAcceptsImages\)/);
  assert.match(source, /somente modelos que aceitam imagens/);
  assert.match(source, /Nenhum modelo com visão neste provedor/);
  assert.match(source, /visualAuxiliaryModelMissing/);
  assert.match(source, /supports_vision: modelAcceptsImages\(model\)/);
  assert.match(source, /modelInputCapabilitiesKnown/);
  assert.match(source, /provider\.models\?\.some\(\(model\) => !modelInputCapabilitiesKnown\(model\)\)/);
  assert.match(source, /Compatibilidade visual dos modelos atualizada/);
  assert.match(source, /explorationEvidenceMode\(exploration\.options\)/);
});

async function timelineComponents() {
  const { transpileModule, ModuleKind, JsxEmit, ScriptTarget } = await import("typescript");
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");
  const compiled = transpileModule(source, {
    fileName: "AutoJevApp.tsx",
    compilerOptions: { module: ModuleKind.CommonJS, jsx: JsxEmit.ReactJSX, target: ScriptTarget.ES2022 },
  });
  const componentModule = { exports: {} };
  new Function("require", "module", "exports", compiled.outputText)(
    createRequire(import.meta.url), componentModule, componentModule.exports,
  );
  return componentModule.exports;
}

test("renders each CT timeline with its captures, assertions and technical signals", async () => {
  const { ScenarioTimelinePanel } = await timelineComponents();
  const signal = {
    kind: "http", message: "POST returned HTTP 401", status_code: 401,
    url: "https://events.example.test/submit", classification: "external_telemetry",
    label: "Telemetria externa", impact: "Não confirma falha funcional do produto.",
    affects_verdict: false, association: { kind: "during_action", scenario_id: "CT001" },
  };
  const html = renderToStaticMarkup(createElement(ScenarioTimelinePanel, {
    explorationId: "run-1", apiBase: "http://localhost:8000/api", scenarios: [{
      id: "CT001", title: "Ordenar os produtos", status: "FAIL",
      expected_result: "Produtos em ordem crescente", actual_result: "A ordenação mudou corretamente",
      evidence: {
        verdict: { kind: "technical_failure", label: "Verificação técnica falhou", historical: true, explanation: "O resultado histórico não comprova um defeito funcional." },
        technical_signals: [signal],
        timeline: [
          { kind: "observation", title: "Antes da ação", screenshot: "001-before.png" },
          { kind: "action", title: "Selecionar ordem", message: "Ordenação executada" },
          { kind: "signal", title: "Sinal de rede", signal },
          { kind: "observation", title: "Depois da ação", screenshot: "002-after.png" },
          { kind: "assertion", title: "Mudança observada", assertion: { kind: "state_change", expected: true, actual: true, passed: true } },
        ],
      },
    }], contexts: [{
      id: "context-startup", title: "Preparação do navegador", status: "INFO",
      evidence: { timeline: [{ kind: "signal", signal }], technical_signals: [signal] },
    }],
  }));
  assert.match(html, /<details class="scenario-case-card verdict-warning"/);
  assert.match(html, /FAIL · Verificação técnica falhou/);
  assert.match(html, /1 de 1 CTs/);
  assert.match(html, /2 capturas/);
  assert.match(html, /\/run-1\/screenshots\/001-before\.png/);
  assert.match(html, /\/run-1\/screenshots\/002-after\.png/);
  assert.ok(html.indexOf("Antes da ação") < html.indexOf("Ordenação executada"));
  assert.ok(html.indexOf("Ordenação executada") < html.indexOf("POST returned HTTP 401"));
  assert.ok(html.indexOf("POST returned HTTP 401") < html.indexOf("Depois da ação"));
  assert.match(html, /Não reprova automaticamente o CT/);
  assert.match(html, /Esperado: true/);
  assert.match(html, /Contexto · não é um teste/);
  assert.match(html, /fora de um teste executado/);
  assert.doesNotMatch(html, /Sinais técnicos observados \(/);
});

test("filters CTs by verdict or signal and constrains screenshot URLs to the local API", async () => {
  const { filterScenarioCases, scenarioScreenshotUrl, ScenarioTimelinePanel } = await timelineComponents();
  const cases = [
    { id: "CT001", title: "Ordenar", status: "PASS" },
    { id: "CT002", title: "Abrir carrinho", status: "FAIL", evidence: { technical_signals: [{ message: "HTTP 500", label: "Resposta da aplicação" }] } },
    { id: "CT003", title: "Preparar estado", status: "SKIPPED" },
  ];
  assert.deepEqual(filterScenarioCases(cases, "", "PASS").map((entry) => entry.id), ["CT001"]);
  assert.deepEqual(filterScenarioCases(cases, "", "signals").map((entry) => entry.id), ["CT002"]);
  assert.deepEqual(filterScenarioCases(cases, "http 500", "all").map((entry) => entry.id), ["CT002"]);
  assert.equal(scenarioScreenshotUrl("http://localhost:8000/api", "run/1", "screenshots/after.png"), "http://localhost:8000/api/explorations/run%2F1/screenshots/after.png");
  assert.equal(scenarioScreenshotUrl("http://localhost:8000/api", "run-1", "../secret.png"), null);
  assert.equal(scenarioScreenshotUrl("http://localhost:8000/api", "run-1", "after.svg"), null);
  assert.equal(scenarioScreenshotUrl("javascript:alert(1)", "run-1", "after.png"), null);
  const html = renderToStaticMarkup(createElement(ScenarioTimelinePanel, {
    scenarios: [{ id: "CT004", title: "<script>alert(1)</script>", status: "SKIPPED", evidence: { timeline: [{ kind: "observation", title: "Captura", url: "javascript:alert(1)", screenshot: "../private.png" }] } }],
    contexts: [], explorationId: "run-1", apiBase: "http://localhost:8000/api",
  }));
  assert.doesNotMatch(html, /href="javascript:/);
  assert.doesNotMatch(html, /<script>alert\(1\)<\/script>/);
  assert.doesNotMatch(html, /<img\b/);
  assert.match(html, /SKIPPED · Não validado/);
});
