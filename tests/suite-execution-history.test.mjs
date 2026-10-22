import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { transpileModule, ModuleKind, ScriptTarget } from "typescript";

const source = await readFile(new URL("../app/components/suite-execution-history.ts", import.meta.url), "utf8");
const compiled = transpileModule(source, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022 } });
const compiledModule = { exports: {} };
new Function("module", "exports", compiled.outputText)(compiledModule, compiledModule.exports);
const { suiteExecutionHistory } = compiledModule.exports;

const group = {
  id: "root", created_at: "2026-10-14T19:22:00Z", initial_count: 151, initial_status: "completed",
  rounds: [
    { id: "older-round", name: "Rodada antiga", count: 1, status: "completed", created_at: "2026-10-16T08:00:00Z" },
    { id: "new-round", name: "Nova rodada", count: 2, status: "completed", created_at: "2026-10-19T23:53:00Z" },
    { id: "draft", name: "Rascunho", count: 3, status: "draft", created_at: "2026-10-19T23:54:00Z" },
  ],
  legacy_runs: [
    { id: "old-child", count: 1, status: "partial", created_at: "2026-10-15T18:00:00Z" },
    { id: "new-child", count: 1, status: "completed", created_at: "2026-10-18T15:22:00Z" },
  ],
};

test("mixed initial, round, draft and linked executions sort newest first without pinning types", () => {
  const before = structuredClone(group);
  const entries = suiteExecutionHistory(group);
  assert.deepEqual(entries.map(item => item.id), ["draft", "new-round", "new-child", "older-round", "old-child", "root"]);
  assert.deepEqual(group, before);
  assert.equal(entries.find(item => item.id === "root").count, 151);
  assert.equal(entries.find(item => item.id === "new-child").status, "completed");
  assert.equal(entries.find(item => item.id === "draft").href, "#/conjuntos/root?plan=draft");
  assert.equal(entries.find(item => item.id === "new-round").href, "#/conjuntos/root?round=new-round");
  assert.equal(entries.find(item => item.id === "old-child").href, "#/exploracoes/old-child");
});

test("date ordering includes seconds, midnight, month changes and timezone offsets", () => {
  const entries = suiteExecutionHistory({ ...group, created_at: "2026-09-30T23:59:59Z", legacy_runs: [], rounds: [
    { ...group.rounds[0], id: "early", created_at: "2026-10-19T21:00:00+03:00" },
    { ...group.rounds[0], id: "late", created_at: "2026-10-19T16:00:01-03:00" },
    { ...group.rounds[0], id: "one-second-earlier", created_at: "2026-10-19T19:00:00Z" },
    { ...group.rounds[0], id: "month-boundary", created_at: "2026-10-01T00:00:00Z" },
  ] });
  assert.deepEqual(entries.map(item => item.id), ["late", "one-second-earlier", "early", "month-boundary", "root"]);
});

test("invalid dates are last and equal timestamps retain a stable order", () => {
  const entries = suiteExecutionHistory({ ...group, created_at: "", legacy_runs: [], rounds: [
    { ...group.rounds[0], id: "first", created_at: "2026-10-19T19:00:00Z" },
    { ...group.rounds[0], id: "second", created_at: "2026-10-19T16:00:00-03:00" },
    { ...group.rounds[0], id: "unknown", created_at: "not-a-date" },
  ] });
  assert.deepEqual(entries.map(item => item.id), ["first", "second", "root", "unknown"]);
});
