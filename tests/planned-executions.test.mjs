import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { transpileModule, ModuleKind, JsxEmit, ScriptTarget } from "typescript";

const nativeRequire = createRequire(import.meta.url);
async function compile(path, resolve = nativeRequire) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const result = transpileModule(source, { fileName: path, compilerOptions: {
    module: ModuleKind.CommonJS, target: ScriptTarget.ES2022, jsx: JsxEmit.ReactJSX,
  } });
  const compiled = { exports: {} };
  new Function("require", "module", "exports", result.outputText)(resolve, compiled, compiled.exports);
  return compiled.exports;
}
const helpers = await compile("../app/components/planned-executions.ts");
const { plannedExecutions, filterPlannedExecutions } = helpers;
const components = await compile("../app/components/PlannedExecutions.tsx", name => name.endsWith(".css") ? {}
  : name === "./planned-executions" ? helpers : nativeRequire(name));
const { PlannedExecutionList } = components;

const draft = (id, values = {}) => ({ id, name: `Planejamento ${id}`, status: "draft", count: 2,
  created_at: "2026-10-19T18:00:00Z", updated_at: "2026-10-19T20:00:00Z", ...values });
const groups = [
  { id: "root-one", app_url: "https://fixture.invalid", rounds: [
    draft("older"), draft("recent", { name: "Revisão do carrinho", created_at: "2026-10-18T12:00:00Z", updated_at: "2026-10-19T18:30:00-03:00" }),
    ...["queued", "running", "waiting_manual", "completed", "failed", "cancelled", "partial", "interrupted", "stopping"].map(status => draft(status, { status })),
  ] },
  { id: "root-two", app_url: "https://fixture.invalid", rounds: [draft("new", { updated_at: "2026-10-19T21:00:00Z", count: 1 })] },
  { id: "no-plans", app_url: "https://empty.invalid", rounds: [] },
];

test("saved drafts from every suite sort by last edit, preserving their parent identities", () => {
  const original = structuredClone(groups);
  const entries = plannedExecutions(groups);
  assert.deepEqual(entries.map(entry => entry.id), ["recent", "new", "older"]);
  assert.deepEqual(entries.map(entry => entry.root_id), ["root-one", "root-two", "root-one"]);
  assert.equal(entries[0].href, "#/conjuntos/root-one?plan=recent");
  assert.equal(entries[1].suite_href, "#/conjuntos/root-two");
  assert.deepEqual(groups, original);
});

test("invalid or absent edit dates use creation date and unknown dates sort last", () => {
  const entries = plannedExecutions([{ ...groups[0], rounds: [
    draft("missing", { updated_at: null, created_at: "2026-10-19T21:00:01Z" }),
    draft("invalid", { updated_at: "invalid", created_at: "2026-10-19T21:00:00Z" }),
    draft("unknown", { updated_at: "invalid", created_at: "invalid" }),
    draft("tie", { updated_at: "2026-10-19T18:00:00-03:00" }),
  ] }]);
  assert.deepEqual(entries.map(entry => entry.id), ["missing", "invalid", "tie", "unknown"]);
  assert.equal(entries[0].activity_at, "2026-10-19T21:00:01Z");
  assert.deepEqual(plannedExecutions([]), []);
});

test("search accepts accents, application and suite ID; suite filter never merges equal domains", () => {
  const entries = plannedExecutions(groups);
  assert.deepEqual(filterPlannedExecutions(entries, " revisao ").map(entry => entry.id), ["recent"]);
  assert.equal(filterPlannedExecutions(entries, "fixture.INVALID").length, 3);
  assert.deepEqual(filterPlannedExecutions(entries, "root-two").map(entry => entry.id), ["new"]);
  assert.deepEqual(filterPlannedExecutions(entries, "", "root-one").map(entry => entry.id), ["recent", "older"]);
  assert.equal(filterPlannedExecutions(entries, "revisao", "root-two").length, 0);
  assert.equal(filterPlannedExecutions(entries, "not-found").length, 0);
});

test("plan and parent links encode IDs instead of treating them as route syntax", () => {
  const [entry] = plannedExecutions([{ ...groups[0], id: "root/with ?#", rounds: [draft("plan/with ?#")] }]);
  assert.equal(entry.href, "#/conjuntos/root%2Fwith%20%3F%23?plan=plan%2Fwith%20%3F%23");
  assert.equal(entry.suite_href, "#/conjuntos/root%2Fwith%20%3F%23");
});

test("plan cards render saved metadata safely, with accessible continuation and no execution action", () => {
  const entries = plannedExecutions(groups);
  const html = renderToStaticMarkup(createElement(PlannedExecutionList, { entries, navigate() {} }));
  assert.equal((html.match(/class="panel planned-card"/g) || []).length, 3);
  assert.match(html, /Revisão do carrinho/);
  assert.match(html, /Rascunho salvo/);
  assert.match(html, /1 teste<\/span>/);
  assert.match(html, /2 testes<\/span>/);
  assert.match(html, /<time dateTime="2026-10-19T18:30:00-03:00"/);
  assert.match(html, /aria-label="Continuar planejamento: Revisão do carrinho"/);
  assert.doesNotMatch(html, /Confirmar e executar|\/start|\/validate/);
  const [unsafe] = plannedExecutions([{ ...groups[0], rounds: [draft("safe", { name: '<script>alert("x")</script>', updated_at: "invalid", created_at: "invalid" })] }]);
  const safeHtml = renderToStaticMarkup(createElement(PlannedExecutionList, { entries: [unsafe], navigate() {} }));
  assert.doesNotMatch(safeHtml, /<script>|Invalid Date/);
  assert.match(safeHtml, /Data não registrada/);
});

test("continuing or opening a parent only navigates to the exact saved identity", () => {
  const calls = [];
  const entries = plannedExecutions(groups);
  const list = PlannedExecutionList({ entries, navigate: hash => calls.push(hash) });
  const firstCard = list.props.children[0];
  firstCard.props.children.at(-1).props.onClick();
  firstCard.props.children[1].props.children.at(-1).props.onClick();
  assert.deepEqual(calls, [entries[0].href, entries[0].suite_href]);
});

test("initial page renders a named loading state without initiating requests or models on the server", () => {
  const html = renderToStaticMarkup(createElement(components.default, {
    request() { assert.fail("Rendering must not issue a request"); }, navigate() { assert.fail("Rendering must not navigate"); },
  }));
  assert.match(html, /<h1>Rascunhos<\/h1>/);
  assert.match(html, /Carregando planejamentos/);
  assert.match(html, /aria-busy="true"/);
  assert.match(html, /Nada é iniciado automaticamente/);
  assert.match(html, /aria-label="Buscar planejamentos"/);
  assert.match(html, /aria-label="Filtrar por conjunto"/);
});
