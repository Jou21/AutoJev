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

test("keeps application credentials independent from accordion visibility", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /if \(credentialsOpen && \(username \|\| password\)\)/);
  assert.match(source, /if \(username \|\| password\) body\.credentials = \{ username: username\.trim\(\), password \}/);
  assert.match(source, /\(username \|\| password\) && \(!username\.trim\(\) \|\| !password\)/);
  assert.match(source, /mesmo com esta seção recolhida/);
  assert.match(source, /Sua senha não é enviada à IA nem salva no banco/);
});

test("defaults auxiliary reviews to the requested Gemini without changing the JEV agent", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");
  assert.match(source, /const AUXILIARY_MODEL_PREFERENCE = \["google\/gemini-2\.5-flash-lite"\]/);
  assert.match(source, /const JEV_MODEL_PREFERENCE = \["typesafe\/jev-1\.13", "typesafe\/jev-latest"\]/);
  assert.match(source, /if \(preferredModel\) return preferredModel/);
  assert.match(source, /available\.filter\(modelAcceptsImages\)/);
});

test("shows test-case creation running alongside browser exploration", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");

  assert.match(source, /Explorar/);
  assert.match(source, /CTs \+ revisão em paralelo/);
  assert.match(source, /Analisando as capturas dos CTs/);
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

test("does not retain another exploration's CT search and filters", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");
  assert.match(source, /<ScenarioTimelinePanel key=\{exploration\.id\}/);
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

test("requires explicit cost consent and a finite budget before resuming visual reviews", async () => {
  const { VisualReviewResumeControls } = await timelineComponents();
  let calls = 0;
  const html = renderToStaticMarkup(createElement(VisualReviewResumeControls, {
    pending: 57, initiallyExpanded: true, initialBudget: 120,
    onResume: async () => { calls += 1; }, onCancel: async () => {},
  }));
  assert.match(html, /Retomar revisões visuais pendentes/);
  assert.match(html, /Novas chamadas podem gerar custo na LLM/);
  assert.match(html, /até 3 tentativas por revisão, contando a primeira/i);
  assert.match(html, /min="1" max="2000"/);
  assert.match(html, /Entendo que esta retomada pode gerar custo adicional/);
  assert.match(html, /type="submit"[^>]*disabled=""/);
  assert.match(html, /sem abrir o navegador nem repetir a exploração/);
  assert.equal(calls, 0);
  assert.equal(renderToStaticMarkup(createElement(VisualReviewResumeControls, {
    pending: 0, onResume: async () => {}, onCancel: async () => {},
  })), "");
});

test("shows independent visual review progress and cancellation without cancelling the exploration", async () => {
  const { VisualReviewResumeControls, explorationDetailState } = await timelineComponents();
  const state = { running: true, total: 58, completed: 4, failed: 1, pending: 53,
    requests_used: 7, request_budget: 120, max_attempts: 3, context_limited_count: 2 };
  const html = renderToStaticMarkup(createElement(VisualReviewResumeControls, {
    state, pending: 53, onResume: async () => {}, onCancel: async () => {},
  }));
  assert.match(html, /4 de 58 revisões concluídas/);
  assert.match(html, /7 de 120 chamadas utilizadas/);
  assert.match(html, /Parar revisões visuais/);
  assert.match(html, /contexto textual limitado/);
  assert.doesNotMatch(html, /Iniciar revisões com este limite/);
  assert.equal(explorationDetailState("run", { id: "run", status: "completed", visual_review_resume: state }, false, "run").shouldPoll, true);
  assert.equal(explorationDetailState("run", { id: "run", status: "completed", visual_review_resume: { running: false } }, false, "run").shouldPoll, false);
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");
  assert.match(source, /visual-reviews\/resume/);
  assert.match(source, /visual-reviews\/cancel/);
  assert.match(source, /auxiliary_max_requests: requestBudget/);
  assert.match(source, /auxiliary_review_max_attempts: 3/);
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

test("renders separate JEV, auxiliary LLM and total costs without losing precision", async () => {
  const { CostBreakdown } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(CostBreakdown, {
    usage: { cost: 0.038586, cost_known: true, cost_complete: true, by_role: {
      jev: { cost: 0.000101, cost_known: true, cost_complete: true },
      auxiliary: { cost: 0.038485, cost_known: true, cost_complete: true },
    } },
  }));
  assert.match(html, /Custos da execução em dólares americanos/);
  assert.match(html, /Custo JEV<\/span><strong>\$0\.000101/);
  assert.match(html, /Custo LLM<\/span><strong>\$0\.038485/);
  assert.match(html, /Custo total<\/span><strong>\$0\.038586/);
  assert.equal((html.match(/class="cost-breakdown-item"/g) || []).length, 3);
  assert.doesNotMatch(html, /parcial|não informado|não registrada/);
});

test("distinguishes reported zero cost from unavailable costs", async () => {
  const { CostBreakdown } = await timelineComponents();
  const free = { cost: 0, cost_known: true, cost_complete: true };
  const html = renderToStaticMarkup(createElement(CostBreakdown, {
    usage: { ...free, by_role: { jev: free, auxiliary: free } },
  }));
  assert.equal((html.match(/\$0\.000000/g) || []).length, 3);
  assert.doesNotMatch(html, /—|parcial|não informado|não registrada/);
});

test("does not substitute zero for costs missing from the provider", async () => {
  const { CostBreakdown } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(CostBreakdown, {
    usage: { cost: null, cost_known: false, by_role: {
      jev: { cost: null, cost_known: false },
      auxiliary: { cost: null, cost_known: true },
    } },
  }));
  assert.equal((html.match(/<strong>—<\/strong>/g) || []).length, 3);
  assert.equal((html.match(/Custo não informado pelo provedor/g) || []).length, 3);
  assert.doesNotMatch(html, /\$0\.000000|não registrada/);
});

test("rejects negative and non-finite costs instead of displaying invalid charges", async () => {
  const { CostBreakdown } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(CostBreakdown, {
    usage: { cost: Infinity, cost_known: true, by_role: {
      jev: { cost: -0.01, cost_known: true },
      auxiliary: { cost: NaN, cost_known: true },
    } },
  }));
  assert.equal((html.match(/<strong>—<\/strong>/g) || []).length, 3);
  assert.equal((html.match(/Custo não informado pelo provedor/g) || []).length, 3);
  assert.doesNotMatch(html, /\$|NaN|Infinity/);
});

test("preserves historical total costs when role attribution was not recorded", async () => {
  const { CostBreakdown } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(CostBreakdown, {
    usage: { cost: 0.038586, cost_known: true },
  }));
  assert.match(html, /Custo JEV<\/span><strong>—/);
  assert.match(html, /Custo LLM<\/span><strong>—/);
  assert.match(html, /Custo total<\/span><strong>\$0\.038586/);
  assert.equal((html.match(/Divisão por modelo não registrada nesta execução/g) || []).length, 2);
  assert.doesNotMatch(html, /\$0\.000000/);
});

test("labels known partial costs instead of presenting them as complete totals", async () => {
  const { CostBreakdown } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(CostBreakdown, {
    usage: { cost: 0.01, cost_known: true, cost_complete: false, by_role: {
      jev: { cost: 0.01, cost_known: true, cost_complete: false },
      auxiliary: { cost: null, cost_known: false, cost_complete: false },
    } },
  }));
  assert.match(html, /Custo JEV<\/span><strong>\$0\.010000 \(parcial\)/);
  assert.match(html, /Custo total<\/span><strong>\$0\.010000 \(parcial\)/);
  assert.match(html, /Custo LLM<\/span><strong>—/);
  assert.equal((html.match(/Valor parcial: há chamadas sem custo informado/g) || []).length, 2);
});

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
      evidence: { timeline: [{ kind: "signal", signal: { kind: "http", message: "Startup request returned HTTP 503", status_code: 503 } }] },
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
  assert.match(html, /<details class="execution-observations"><summary>/);
  assert.match(html, /Observações da execução/);
  assert.match(html, /Startup request returned HTTP 503/);
  assert.equal((html.match(/class="scenario-case-card /g) || []).length, 1);
  assert.ok(html.indexOf('class="execution-observations"') > html.indexOf('class="scenario-case-list"'));
  assert.doesNotMatch(html, /Contexto · não é um teste|Contexto \/ navegação de apoio|Evidência de contexto|context-startup/);
  assert.doesNotMatch(html, /Sinais técnicos observados \(/);
});

test("shows orphan occurrences as execution observations with page provenance and request facts", async () => {
  const { ExecutionObservations } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(ExecutionObservations, {
    explorationId: "run", apiBase: "http://localhost:8000/api", contexts: [{
      id: "context-page", title: "Old context card title", status: "INFO", evidence: { timeline: [{
        id: "request-401", kind: "signal", occurred_at: "2026-10-06T21:59:22Z", screenshot: "startup.png",
        url: "https://events.example.test/submit",
        observation: {
          page_url: "https://shop.test/inventory.html", page_url_source: "event", phase: "Carregamento da página",
          summary: "Ocorreu HTTP 401 durante o carregamento da página de produtos.",
          method: "POST", status_code: 401, request_url: "https://events.example.test/submit",
        },
        signal: { kind: "http", message: "POST returned HTTP 401", status_code: 401, url: "https://events.example.test/submit", impact: "external_integration", affects_verdict: false },
      }, {
        kind: "signal", url: "https://shop.test/cart.html",
        observation: { page_url: "https://shop.test/cart.html", page_url_source: "observation", request_url: null, summary: "Na observação da página do carrinho, foi registrada uma mensagem de console." },
        signal: { kind: "console", message: "Console error", url: "https://shop.test/cart.html" },
      }] },
    }],
  }));
  assert.match(html, /Observações da execução/);
  assert.match(html, /2 registros/);
  assert.match(html, /Ocorreu HTTP 401 durante o carregamento da página de produtos/);
  assert.match(html, /Mensagem registrada: POST returned HTTP 401/);
  assert.match(html, /Mensagem registrada: Console error/);
  assert.match(html, /<dt>Página<\/dt><dd><a href="https:\/\/shop.test\/inventory.html"/);
  assert.match(html, /<dt>Etapa<\/dt><dd>Carregamento da página/);
  assert.match(html, /dateTime="2026-10-06T21:59:22Z"/i);
  assert.match(html, /<dt>Requisição<\/dt><dd><span>POST · <\/span><span>HTTP 401/);
  assert.match(html, /href="https:\/\/events.example.test\/submit"/);
  assert.equal((html.match(/<dt>Requisição<\/dt>/g) || []).length, 1);
  assert.match(html, /\/run\/screenshots\/startup.png/);
  assert.match(html, /Ocorrência em uma integração externa; sem defeito funcional confirmado/);
  assert.doesNotMatch(html, /Old context card title|scenario-case-card|scenario-verdict|CONTEXTO|INFO|<details[^>]* open/);
});

test("retains original exception details and recognizes legacy backend network types in observations", async () => {
  const { ExecutionObservations } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(ExecutionObservations, {
    explorationId: "run", apiBase: "http://localhost:8000/api", contexts: [{
      id: "unowned", status: "INFO", evidence: { timeline: [
        { kind: "signal", observation: { summary: "Foi registrada uma exceção JavaScript.", request_url: null },
          signal: { type: "pageerror", message: "TypeError: Cannot read properties of undefined" } },
        { kind: "signal", signal: { type: "network", method: "GET", url: "https://example.test/api", message: "net::ERR_CONNECTION_RESET" } },
      ] },
    }],
  }));
  assert.match(html, /Mensagem registrada: TypeError: Cannot read properties of undefined/);
  assert.match(html, /net::ERR_CONNECTION_RESET/);
  assert.match(html, /<dt>Requisição<\/dt><dd><span>GET · <\/span>/);
  assert.match(html, /href="https:\/\/example.test\/api"/);
  assert.doesNotMatch(html, /scenario-case-card|scenario-verdict/);
});

test("does not invent an observation page or phase from a legacy request endpoint or count it as a CT", async () => {
  const { ScenarioTimelinePanel } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(ScenarioTimelinePanel, {
    explorationId: "run", apiBase: "http://localhost:8000/api", contexts: [], scenarios: [{
      id: "context-legacy", status: "INFO", title: "Evidência de contexto", url: "https://unknown.test/",
      evidence: { technical_signals: [{ kind: "http", message: "<script>bad</script> HTTP 401", status_code: 401, url: "javascript:alert(1)" }] },
    }],
  }));
  assert.match(html, /0 de 0 CTs/);
  assert.match(html, /Observações da execução/);
  assert.match(html, /Página não registrada/);
  assert.match(html, /Momento não registrado/);
  assert.match(html, /&lt;script&gt;bad&lt;\/script&gt; HTTP 401/);
  assert.doesNotMatch(html, /<dt>Etapa<\/dt>|Carregamento da página|href="javascript:|href="https:\/\/unknown.test|class="scenario-case-card|<script>bad<\/script>|Evidência de contexto/);
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");
  assert.match(source, /<ExecutionObservations contexts=\{observations\}/);
  assert.doesNotMatch(source, /filteredContexts/);
});

test("distinguishes business validation from technical interaction and lists coverage restrictions", async () => {
  const { VerificationStrengthSummary, CoverageRestrictionsPanel } = await timelineComponents();
  const strength = renderToStaticMarkup(createElement(VerificationStrengthSummary, {
    counts: { business_verified: 30, interaction_only: 57, execution_failures: 1 },
  }));
  assert.match(strength, /Negócio verificado/);
  assert.match(strength, /Só interação técnica/);
  assert.match(strength, /não todos os cenários possíveis/);
  const restrictions = renderToStaticMarkup(createElement(CoverageRestrictionsPanel, {
    restrictions: [{ name: "<script>bad</script>", reason: "no_safe_restore_route", source_url: "https://shop.test/cart", action: "click" }],
  }));
  assert.match(restrictions, /rota segura/);
  assert.match(restrictions, /&lt;script&gt;/);
  assert.doesNotMatch(restrictions, /<script>/);
  const historic = renderToStaticMarkup(createElement(CoverageRestrictionsPanel, { restrictions: [], blockedCount: 48 }));
  assert.match(historic, /48 controles bloqueados/);
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

test("distinguishes visual-model failures and unavailable visual review from business PASS", async () => {
  const { ScenarioTimelinePanel } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(ScenarioTimelinePanel, {
    explorationId: "run", apiBase: "http://localhost:8000/api", contexts: [], scenarios: [{
      id: "CT001", title: "Conferir imagem do produto", status: "FAIL", evidence: {
        verdict: { kind: "visual_failure", label: "Indício visual · revisão necessária", explanation: "Não é um bug funcional confirmado." },
        visual_assessment: { requested: true, status: "assessed", accepted_count: 1 },
        timeline: [{ kind: "assertion", title: "Revisão visual", screenshot: "after.png", assertion: { kind: "visual_model", passed: false, message: "Produto exibe uma imagem incompatível", actual: { confidence: 0.98, region: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 } } } }],
      },
    }, {
      id: "CT002", title: "Abrir produto correto", status: "PASS", evidence: {
        verdict: { kind: "validated", label: "Verificações aprovadas" },
        visual_assessment: { requested: true, status: "unavailable", reason: "auxiliary_rate_limited" },
      },
    }],
  }));
  assert.match(html, /FAIL · Indício visual · revisão necessária/);
  assert.match(html, /1 indício\(s\) visual\(is\) · revisão necessária/);
  assert.match(html, /Falhou · indício visual da LLM/);
  assert.match(html, /Análise visual indisponível · CT baseado nas verificações locais/);
  assert.match(html, /O provedor limitou as chamadas/);
  assert.match(html, /PASS · Verificações aprovadas/);
});

test("makes missing visual reviews visible without converting local PASS to visual approval", async () => {
  const { VisualReviewSummary, visualReviewCounts } = await timelineComponents();
  const scenarios = [
    { id: "CT001", status: "PASS", evidence: { visual_assessment: { status: "unavailable", reason: "auxiliary_rate_limited" } } },
    { id: "CT002", status: "PASS", evidence: { visual_assessment: { status: "unavailable", reason: "visual_assessment_not_returned" } } },
  ];
  assert.deepEqual(visualReviewCounts(scenarios), {
    total: 2, assessed: 0, pending: 0, unavailable: 2, rateLimited: 1, incompleteResponse: 1, contextLimited: 0,
    unavailableReasons: [
      { reason: "auxiliary_rate_limited", count: 1, label: "limitados pelo provedor" },
      { reason: "visual_assessment_not_returned", count: 1, label: "com resposta visual ausente ou inválida" },
    ],
  });
  const html = renderToStaticMarkup(createElement(VisualReviewSummary, { scenarios }));
  assert.match(html, /Análise visual não realizada/);
  assert.match(html, /0 de 2 CTs receberam uma análise visual válida/);
  assert.match(html, /1 com resposta visual ausente ou inválida/);
  assert.match(html, /Análise visual indisponível não significa aprovação visual/);
  assert.match(renderToStaticMarkup(createElement(VisualReviewSummary, { scenarios: [] })), /Análise visual não realizada/);
  assert.match(renderToStaticMarkup(createElement(VisualReviewSummary, { scenarios: [], active: true })), /Análise visual em andamento/);
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");
  assert.match(source, /explorationEvidenceMode\(exploration.options\) === "visual" && <VisualReviewSummary/);
});

test("keeps completed legacy image reviews visibly distinct from full-semantic visual analysis", async () => {
  const { VisualReviewSummary, visualReviewCounts, ScenarioTimelinePanel } = await timelineComponents();
  const scenarios = [
    { id: "CT001", title: "Legacy", status: "PASS", evidence: { visual_assessment: { requested: true, status: "assessed", context_limited: true } } },
    { id: "CT002", title: "Legacy scoped", status: "PASS", evidence: { visual_assessment: { requested: true, status: "assessed", review_scope: "saved_capture_only_legacy" } } },
    { id: "CT003", title: "Full semantics", status: "PASS", evidence: { visual_assessment: { requested: true, status: "assessed", context_limited: false } } },
  ];
  assert.equal(visualReviewCounts(scenarios).contextLimited, 2);
  const html = renderToStaticMarkup(createElement(VisualReviewSummary, { scenarios }));
  assert.match(html, /Capturas analisadas · contexto parcial/);
  assert.match(html, /2 revisões com contexto textual limitado/);
  assert.match(html, /DOM e o texto visível originais não foram preservados/);
  assert.match(html, /não equivalem a uma análise com o contexto semântico completo/);
  const apiHtml = renderToStaticMarkup(createElement(VisualReviewSummary, { scenarios: [], summary: {
    total: 3, assessed: 3, pending: 0, unavailable: 0, context_limited: 2, unavailable_reasons: [],
  } }));
  assert.match(apiHtml, /2 revisões com contexto textual limitado/);
  const caseHtml = renderToStaticMarkup(createElement(ScenarioTimelinePanel, {
    scenarios, contexts: [], explorationId: "run", apiBase: "http://localhost:8000/api",
  }));
  assert.match(caseHtml, /Contexto textual limitado: esta revisão usa as capturas salvas/);
});

test("accounts for every unavailable visual review including timeout and local tail limit", async () => {
  const { VisualReviewSummary, visualReviewCounts } = await timelineComponents();
  const scenarios = ["documentation_time_limit", "documentation_time_limit", "TimeoutError", "unknown_reason", ""].map((reason, index) => ({
    id: `CT${index}`, status: "PASS", evidence: { visual_assessment: { status: "unavailable", reason } },
  }));
  const counts = visualReviewCounts(scenarios);
  assert.equal(counts.unavailable, 5);
  assert.equal(counts.unavailableReasons.reduce((sum, item) => sum + item.count, 0), 5);
  assert.equal(counts.rateLimited, 0);
  const html = renderToStaticMarkup(createElement(VisualReviewSummary, { scenarios }));
  assert.match(html, /2 sem revisão antes do limite de espera das revisões auxiliares/);
  assert.match(html, /1 com tempo de resposta da LLM auxiliar excedido/);
  assert.match(html, /Código: unknown_reason/);
  assert.match(html, /Código: reason_not_recorded/);
  assert.doesNotMatch(html, /0 limitados pelo provedor/);
  const shared = renderToStaticMarkup(createElement(VisualReviewSummary, { scenarios: [], summary: {
    total: 5, assessed: 0, pending: 0, unavailable: 5, rate_limited: 0, incomplete_response: 0,
    unavailable_reasons: [{ reason: "documentation_time_limit", count: 5, label: "sem revisão antes do limite de espera das revisões auxiliares" }],
  } }));
  assert.match(shared, /0 de 5 CTs/);
  assert.match(shared, /5 sem revisão antes do limite de espera/);
});

test("total execution duration includes postprocessing and prefers final timestamps over old events", async () => {
  const { explorationDuration } = await timelineComponents();
  const run = { id: "run", status: "completed", app_url: "http://fixture.test", started_at: "2026-10-04T03:22:54Z",
    finished_at: "2026-10-04T03:28:48Z", updated_at: "2026-10-04T03:38:49Z", events: [{ created_at: "2026-10-04T03:38:49Z" }] };
  assert.equal(explorationDuration(run), "15m 55s");
  assert.equal(explorationDuration({ ...run, finished_at: "2026-10-04T03:38:50Z" }), "15m 56s");
  assert.equal(explorationDuration({ ...run, duration_summary: { started_at: run.started_at, ended_at: "2026-10-04T03:38:50Z", total_seconds: 956 } }), "15m 56s");
  assert.equal(explorationDuration({ ...run, status: "postprocessing" }, Date.parse("2026-10-04T03:39:54Z")), "17m 0s");
  assert.equal(explorationDuration({ ...run, started_at: "invalid" }), "—");
  assert.equal(explorationDuration({ ...run, events: [], updated_at: "invalid", finished_at: undefined }), "—");
});

test("changing the detail route never renders or stops polling for a previous completed run", async () => {
  const { explorationDetailState } = await timelineComponents();
  const old = { id: "old", status: "completed", app_url: "http://fixture.test" };
  const changing = explorationDetailState("new", old, false, "old");
  assert.equal(changing.exploration, null);
  assert.equal(changing.loading, true);
  assert.equal(changing.shouldPoll, true);
  const loaded = explorationDetailState("new", { ...old, id: "new", status: "running" }, false, "new");
  assert.equal(loaded.exploration.id, "new");
  assert.equal(loaded.loading, false);
  assert.equal(loaded.shouldPoll, true);
  assert.equal(explorationDetailState("old", old, false, "old").shouldPoll, false);
  assert.equal(explorationDetailState("missing", null, false, "missing").loading, false);
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");
  assert.match(source, /activeExplorationId\.current === id\) setSelectedExploration\(data\)/);
  assert.match(source, /key=\{route.explorationId\}/);
  assert.match(source, /exploration=\{explorationDetailState/);
});

test("coverage explains the attempted masked field and its blocking dependency", async () => {
  const { CoverageRestrictionsPanel } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(CoverageRestrictionsPanel, { restrictions: [{
    id: "masked-postal", name: "Continue", field_name: "Zip/Postal Code", reason: "required_field_validation_masked",
    validation_probe: { blocking_fields: [{ name: "<script>Last Name</script>" }] },
  }] }));
  assert.match(html, /Zip\/Postal Code · Continue/);
  assert.match(html, /A tentativa foi executada/);
  assert.match(html, /Campos que impediram a preparação/);
  assert.match(html, /&lt;script&gt;Last Name&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>Last Name<\/script>/);
});

test("resolved route debt shows actual evidence without declaring the test passed", async () => {
  const { CoverageRestrictionsPanel } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(CoverageRestrictionsPanel, {
    restrictions: [], explorationId: "run", apiBase: "http://localhost:8000/api",
    resolvedRestrictions: [{ id: "restored", name: "<script>Add to cart</script>",
      resolution: { action_step: 19, screenshot: "019-click.png", assertions_passed: false } }],
  }));
  assert.match(html, /Restrições de retomada resolvidas · 1/);
  assert.match(html, /Executado na etapa 19/);
  assert.match(html, /houve verificação reprovada/);
  assert.match(html, /http:\/\/localhost:8000\/api\/explorations\/run\/screenshots\/019-click.png/);
  assert.match(html, /&lt;script&gt;Add to cart&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>Add to cart/);
});

test("unresolved cart coverage explains the actual failed prerequisite", async () => {
  const { CoverageRestrictionsPanel } = await timelineComponents();
  const html = renderToStaticMarkup(createElement(CoverageRestrictionsPanel, {
    explorationId: "run", apiBase: "http://localhost:8000/api",
    restrictions: [{ id: "backpack-add", name: "Add to cart", reason: "no_safe_restore_route",
      dependency: { kind: "failed_inverse_cart_toggle", source: "actual_business_assertion",
        name: "Remove", item_id: "4", action_step: 21, screenshot: "021-click.png" } }],
  }));
  assert.match(html, /Pré-condição impedida por uma falha já observada: Remove \(produto 4\), etapa 21/);
  assert.match(html, /screenshots\/021-click.png/);
  assert.doesNotMatch(html, /Restrições de retomada resolvidas/);
});

test("screenshot preview keeps originals intact and changes only HTTP cache-busters on retry", async () => {
  const { ScreenshotPreview, screenshotRequestUrl } = await timelineComponents();
  const src = "http://localhost:8000/api/explorations/run/screenshots/after.png?keep=yes";
  const html = renderToStaticMarkup(createElement(ScreenshotPreview, { src, revision: "updated", alt: "Captura da exploração" }));
  assert.match(html, /Captura da exploração/);
  assert.match(html, /keep=yes&amp;v=updated/);
  assert.match(screenshotRequestUrl(src, "updated", 1), /v=updated-retry-1/);
  assert.equal(screenshotRequestUrl("data:image/png;base64,fixture", "updated", 1), "data:image/png;base64,fixture");
  assert.equal(screenshotRequestUrl("blob:http://localhost:3000/fixture", "updated", 1), "blob:http://localhost:3000/fixture");
  const empty = renderToStaticMarkup(createElement(ScreenshotPreview, { alt: "Sem captura" }));
  assert.match(empty, /Aguardando captura/);
  const config = await readFile(new URL("../vite.config.ts", import.meta.url), "utf8");
  assert.match(config, /forwardConsole: false/);
});
