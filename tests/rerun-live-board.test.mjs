import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
let boardModule;
async function components() {
  if (boardModule) return boardModule;
  const { transpileModule, ModuleKind, JsxEmit, ScriptTarget } = await import("typescript");
  const source = await readFile(new URL("../app/components/RerunLiveBoard.tsx", import.meta.url), "utf8");
  const compiled = transpileModule(source, {
    fileName: "RerunLiveBoard.tsx",
    compilerOptions: { module: ModuleKind.CommonJS, jsx: JsxEmit.ReactJSX, target: ScriptTarget.ES2022 },
  });
  const componentModule = { exports: {} };
  new Function("require", "module", "exports", compiled.outputText)(
    (path) => path.endsWith(".css") ? {} : require(path), componentModule, componentModule.exports,
  );
  boardModule = componentModule.exports;
  return boardModule;
}

function step(id, title, state = "pending", extra = {}) {
  return { id, title, state, kind: "observation", label: "Aguardando sua vez", description: `Descrição: ${title}`, ...extra };
}

async function render(props) {
  const { RerunLiveBoard } = await components();
  return renderToStaticMarkup(createElement(RerunLiveBoard, props));
}

function completedStepsAndWaitingPrint() {
  const titles = ["Antes da ação", "Selecionar opção", "Depois da ação", "Opção selecionada", "Ordenação dos produtos", "Erros durante a ação"];
  return {
    version: 1,
    steps: [
      ...titles.map((title, index) => step(`recorded-${index}`, title, "done", { label: "Concluída" })),
      step("manual-print", "Aguardar print", "waiting", { checkpoint_id: "db-1", label: "Aguardando você", description: "Cole ou escolha uma imagem para continuar." }),
    ],
    completed: 6,
    total: 7,
    current_id: "manual-print",
  };
}

function renderedCards(html) {
  return [...html.matchAll(/<li\b[^>]*data-step-id="([^"]+)"[^>]*>[\s\S]*?<\/li>/g)]
    .map((match) => ({ id: match[1], html: match[0] }));
}

function cssDeclarations(css, selector) {
  const declarations = {};
  for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!match[1].split(",").map((entry) => entry.trim()).includes(selector)) continue;
    for (const declaration of match[2].split(";")) {
      const colon = declaration.indexOf(":");
      if (colon >= 0) declarations[declaration.slice(0, colon).trim()] = declaration.slice(colon + 1).trim();
    }
  }
  return declarations;
}

function rgb(hex) {
  assert.match(hex, /^#[0-9a-f]{6}([0-9a-f]{2})?$/i);
  return [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255);
}

function compositeHex(foreground, background) {
  const alpha = foreground.length === 9 ? parseInt(foreground.slice(7), 16) / 255 : 1;
  const backdrop = rgb(background);
  return rgb(foreground).map((channel, index) => channel * alpha + backdrop[index] * (1 - alpha));
}

function contrastRatio(text, background) {
  const luminance = (channels) => channels
    .map((channel) => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
    .reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
  const light = luminance(rgb(text));
  const dark = luminance(Array.isArray(background) ? background : rgb(background));
  return (Math.max(light, dark) + 0.05) / (Math.min(light, dark) + 0.05);
}

test("live board preserves exact planned DOM order, including the user's inserted print", async () => {
  const steps = [
    step("before", "Antes da ação", "done", { label: "Captura registrada" }),
    step("action", "Selecionar opção", "done", { kind: "action", label: "Ação concluída" }),
    step("print", "Aguardar print", "waiting", { checkpoint_id: "db-1", label: "Aguardando você" }),
    step("after", "Depois da ação"),
    step("option", "Opção selecionada", "pending", { kind: "assertion" }),
    step("order", "Ordenação dos produtos", "pending", { kind: "assertion" }),
    step("errors", "Erros durante a ação", "pending", { kind: "assertion" }),
  ];
  const html = await render({ progress: { version: 1, steps, total: 7, completed: 2, current_id: "print" }, scenarioId: "CT001", active: true });
  assert.deepEqual([...html.matchAll(/data-step-id="([^"]+)"/g)].map((match) => match[1]), steps.map((entry) => entry.id));
  assert.deepEqual([...html.matchAll(/<h3>([^<]+)<\/h3>/g)].map((match) => match[1]), steps.map((entry) => entry.title));
  assert.match(html, /PASSOS E EVIDÊNCIAS/);
  assert.match(html, /CT001/);
  assert.match(html, /2<span> \/ 7<\/span>/);
  assert.match(html, /aria-current="step"[^>]*aria-label="Etapa 3: Aguardar print/);
  assert.match(html, /Seu print/);
  assert.match(html, /--board-row-4:2;--board-column-4:4/);
  assert.match(html, /Concluir uma etapa não significa aprovar o teste/);
});

test("each live state has independent classes, text and accessible icon semantics", async () => {
  const states = ["pending", "active", "waiting", "done", "failed", "blocked", "skipped", "unavailable"];
  const labels = ["Pendente", "Executando", "Envie uma imagem", "Imagem anexada", "Conferência falhou", "Pré-requisito ausente", "Não repetida", "Informação indisponível"];
  const steps = states.map((state, index) => step(`s-${state}`, `Etapa ${state}`, state, { label: labels[index] }));
  const html = await render({ progress: { version: 1, steps, total: 8, completed: 1, current_id: "s-active" }, onSelectStep: () => {} });
  states.forEach((state) => assert.match(html, new RegExp(`data-step-id="s-${state}" data-step-state="${state}"`)));
  labels.forEach((label) => assert.ok(html.includes(label)));
  assert.equal((html.match(/class="rerun-board-cell state-done"/g) || []).length, 1);
  assert.equal((html.match(/aria-current="step"/g) || []).length, 1);
  assert.equal((html.match(/<button type="button" class="rerun-board-card/g) || []).length, 8);
  assert.match(html, /<svg[^>]*aria-hidden="true"/);
});

test("six completed cards stay green while the waiting print alone receives the SUA VEZ badge", async () => {
  const html = await render({ progress: completedStepsAndWaitingPrint(), active: true, onSelectStep: () => {} });
  const cards = renderedCards(html);
  assert.equal(cards.length, 7);
  assert.equal(cards.filter((card) => /data-step-state="done"/.test(card.html)).length, 6);
  assert.equal(cards.filter((card) => /data-step-state="waiting"/.test(card.html)).length, 1);
  assert.equal((html.match(/class="rerun-board-waiting-badge"/g) || []).length, 1);
  for (const card of cards.slice(0, 6)) {
    assert.doesNotMatch(card.html, /rerun-board-waiting-badge|SUA VEZ/);
    assert.match(card.html, /lucide-check/);
  }
  const waiting = cards.find((card) => card.id === "manual-print");
  assert.match(waiting.html, /state-waiting/);
  assert.match(waiting.html, /class="rerun-board-waiting-badge">SUA VEZ<\/span>/);
  assert.match(waiting.html, /aria-current="step"/);
  assert.match(waiting.html, /Aguardando você/);
  assert.match(html, /6<span> \/ 7<\/span>/);
});

test("the same print switches to done and loses its waiting badge only when its actual state changes", async () => {
  const waitingProgress = completedStepsAndWaitingPrint();
  const completedProgress = {
    ...waitingProgress,
    steps: waitingProgress.steps.map((entry) => entry.id === "manual-print" ? { ...entry, state: "done", label: "Imagem anexada" } : entry),
    completed: 7,
    current_id: null,
  };
  const waitingHtml = await render({ progress: waitingProgress, active: true, selectedStepId: "manual-print", onSelectStep: () => {} });
  const doneHtml = await render({ progress: completedProgress, active: false, selectedStepId: "manual-print", onSelectStep: () => {} });
  assert.match(waitingHtml, /rerun-board-waiting-badge/);
  assert.deepEqual(renderedCards(waitingHtml).map((card) => card.id), renderedCards(doneHtml).map((card) => card.id));
  assert.equal(renderedCards(doneHtml).filter((card) => /data-step-state="done"/.test(card.html)).length, 7);
  const donePrint = renderedCards(doneHtml).find((card) => card.id === "manual-print");
  assert.match(donePrint.html, /data-step-state="done"/);
  assert.match(donePrint.html, /Imagem anexada/);
  assert.match(donePrint.html, /lucide-check/);
  assert.doesNotMatch(doneHtml, /rerun-board-waiting-badge|SUA VEZ|aria-current="step"/);
  assert.match(doneHtml, /7<span> \/ 7<\/span>/);
});

test("viewing another card preserves the waiting print's distinct highlight and SUA VEZ badge", async () => {
  const html = await render({ progress: completedStepsAndWaitingPrint(), active: true, selectedStepId: "recorded-0", onSelectStep: () => {} });
  const cards = renderedCards(html);
  const selected = cards.find((card) => card.id === "recorded-0");
  const waiting = cards.find((card) => card.id === "manual-print");
  assert.match(selected.html, /class="rerun-board-card is-selected" aria-pressed="true"/);
  assert.doesNotMatch(selected.html, /aria-current|SUA VEZ/);
  assert.match(waiting.html, /class="rerun-board-cell state-waiting"/);
  assert.match(waiting.html, /class="rerun-board-card" aria-current="step" aria-pressed="false"/);
  assert.match(waiting.html, /class="rerun-board-waiting-badge">SUA VEZ<\/span>/);
  assert.equal((html.match(/rerun-board-waiting-badge/g) || []).length, 1);
  assert.match(html, /class="rerun-board-now" data-step-state="waiting"/);
});

test("selecting card details is explicit and does not mutate or request anything on render", async () => {
  let calls = 0;
  const progress = { version: 1, steps: [step("before", "Antes"), step("action", "Clicar", "active", { kind: "action", label: "Em execução" })], total: 2, completed: 0, current_id: "action" };
  const original = JSON.stringify(progress);
  const html = await render({ progress, active: true, selectedStepId: "before", onSelectStep: () => { calls += 1; } });
  assert.equal(calls, 0);
  assert.equal(JSON.stringify(progress), original);
  assert.match(html, /class="rerun-board-card is-selected" aria-pressed="true"/);
  assert.match(html, /aria-current="step" aria-pressed="false"/);
  assert.match(html, /Ver detalhes/);
  assert.doesNotMatch(html, /Executar agora|Reexecutar teste|Anexar print e continuar/);
});

test("missing or empty live progress never reuses a historical pass result", async () => {
  const missing = await render({ scenarioId: "CT001", active: true });
  const empty = await render({ progress: { version: 1, steps: [], completed: 0, total: 0 }, active: false });
  for (const html of [missing, empty]) {
    assert.match(html, /Preparando os passos do teste/);
    assert.match(html, /Nenhuma etapa foi marcada como concluída/);
    assert.doesNotMatch(html, /data-step-state="done"|Aprovado|100%/);
  }
});

test("backend messages, titles and notices are escaped, with unknown states unavailable", async () => {
  const html = await render({ progress: { version: 1, steps: [step("<unsafe>", '<img src=x onerror="alert(1)">', "unexpected", { label: "<script>hello</script>" })], completed: 0, total: 1, notice: "<svg onload=alert(1)>" } });
  assert.match(html, /data-step-state="unavailable"/);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html, /&lt;script&gt;hello&lt;\/script&gt;/);
  assert.match(html, /&lt;svg onload=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<script>|<img src=x|<svg onload/);
});

test("board styles keep mobile order, keyboard focus, reduced motion and print fallbacks", async () => {
  const css = await readFile(new URL("../app/components/RerunLiveBoard.css", import.meta.url), "utf8");
  assert.match(css, /grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(css, /@container rerun-board \(min-width: 1000px\)/);
  assert.match(css, /grid-template-columns: repeat\(4, minmax\(0, 1fr\)\)/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.match(css, /@media print/);
  assert.match(css, /state-done/);
  assert.match(css, /state-failed/);
  assert.doesNotMatch(css, /order:\s*-[1-9]/);
});

test("waiting amber, executing blue and completed green use distinct real CSS colors and a stronger waiting border", async () => {
  const css = (await readFile(new URL("../app/components/RerunLiveBoard.css", import.meta.url), "utf8")).split("@container")[0];
  const waiting = cssDeclarations(css, ".rerun-board-cell.state-waiting");
  const active = cssDeclarations(css, ".rerun-board-cell.state-active");
  const done = cssDeclarations(css, ".rerun-board-cell.state-done");
  assert.equal(new Set([waiting["--board-state"], active["--board-state"], done["--board-state"]]).size, 3);
  assert.equal(waiting["--board-state"], "#ffcb59");
  assert.equal(active["--board-state"], "#88c9ff");
  assert.equal(done["--board-state"], "#7be5ad");
  const waitingCard = cssDeclarations(css, ".state-waiting .rerun-board-card");
  assert.equal(waitingCard.border, "2px solid var(--board-state-border)");
  assert.equal(waitingCard.background, "#362a14");
  assert.equal(cssDeclarations(css, ".state-waiting .rerun-board-card.is-selected").background, undefined);
  assert.equal(cssDeclarations(css, ".state-waiting .rerun-board-card.is-selected").border, undefined);
  assert.equal(cssDeclarations(css, ".rerun-board-now-dot.state-waiting").background, waiting["--board-state"]);
  assert.equal(cssDeclarations(css, ".rerun-board-legend > span:nth-child(1) svg").color, done["--board-state"]);
  assert.equal(cssDeclarations(css, ".rerun-board-legend > span:nth-child(2) svg").color, active["--board-state"]);
  assert.equal(cssDeclarations(css, ".rerun-board-legend > span:nth-child(3) svg").color, waiting["--board-state"]);
});

test("real waiting card text, status and badge colors meet WCAG AA normal-text contrast", async () => {
  const css = (await readFile(new URL("../app/components/RerunLiveBoard.css", import.meta.url), "utf8")).split("@container")[0];
  const waitingCard = cssDeclarations(css, ".state-waiting .rerun-board-card");
  const waiting = cssDeclarations(css, ".rerun-board-cell.state-waiting");
  const checks = [
    ["waiting title", cssDeclarations(css, ".state-waiting .rerun-board-card h3").color, waitingCard.background],
    ["waiting description", cssDeclarations(css, ".state-waiting .rerun-board-description").color, waitingCard.background],
    ["waiting kind", cssDeclarations(css, ".state-waiting .rerun-board-kind").color, waitingCard.background],
    ["waiting details link", cssDeclarations(css, ".state-waiting .rerun-board-details-hint").color, waitingCard.background],
    ["waiting status label", waiting["--board-state"], waitingCard.background],
  ];
  for (const selector of [".rerun-board-waiting-badge", ".state-waiting .rerun-board-number"]) {
    const rule = cssDeclarations(css, selector);
    checks.push([selector, rule.color, rule.background]);
  }
  const now = cssDeclarations(css, '.rerun-board-now[data-step-state="waiting"]');
  checks.push(["waiting now label", now.color, now.background]);
  checks.push(["waiting now title", cssDeclarations(css, '.rerun-board-now[data-step-state="waiting"] strong').color, now.background]);
  for (const [name, color, background] of checks) {
    const ratio = contrastRatio(color, background);
    assert.ok(ratio >= 4.5, `${name} has ${ratio.toFixed(2)}:1 contrast; expected at least 4.5:1`);
  }
});

test("completed and executing card descriptions and labels meet 4.5:1 with actual alpha-composited CSS backgrounds", async () => {
  const css = (await readFile(new URL("../app/components/RerunLiveBoard.css", import.meta.url), "utf8")).split("@container")[0];
  const baseCard = cssDeclarations(css, ".rerun-board-card");
  const baseBackground = baseCard.background.match(/#[0-9a-f]{6}$/i)?.[0];
  assert.ok(baseBackground, "The card must declare an explicit base background for contrast measurement.");
  for (const state of ["done", "active"]) {
    const rule = cssDeclarations(css, `.rerun-board-cell.state-${state}`);
    const background = compositeHex(rule["--board-state-soft"], baseBackground);
    for (const [name, color] of [
      ["title", baseCard.color],
      ["description", cssDeclarations(css, ".rerun-board-description").color],
      ["kind", cssDeclarations(css, ".rerun-board-kind").color],
      ["status", rule["--board-state"]],
    ]) {
      const ratio = contrastRatio(color, background);
      assert.ok(ratio >= 4.5, `${state} ${name} has ${ratio.toFixed(2)}:1 contrast; expected at least 4.5:1`);
    }
  }
});
