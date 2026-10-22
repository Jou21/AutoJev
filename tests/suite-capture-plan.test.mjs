import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { transpileModule, ModuleKind, ScriptTarget } from "typescript";

const source = await readFile(new URL("../app/components/suite-capture-plan.ts", import.meta.url), "utf8");
const compiled = transpileModule(source, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022 } });
const compiledModule = { exports: {} };
new Function("module", "exports", compiled.outputText)(compiledModule, compiledModule.exports);
const { insertCaptureCheckpoint } = compiledModule.exports;

test("every pause has a usable insertion position before and after it", () => {
  let points = insertCaptureCheckpoint([], "db-1", 1, 0);
  points = insertCaptureCheckpoint(points, "db-2", 1, 1);
  points = insertCaptureCheckpoint(points, "db-3", 1, 1);
  points = insertCaptureCheckpoint(points, "db-4", 1, 0);
  assert.deepEqual(points.map(point => point.id), ["db-4", "db-1", "db-3", "db-2"]);
  assert.deepEqual(points.map(point => [point.gap, point.order]), [[1, 0], [1, 1], [1, 2], [1, 3]]);
});

test("inserting into a saved plan preserves identities and other original gaps without mutating input", () => {
  const points = Object.freeze([
    Object.freeze({ id: "db-8", gap: 2, order: 3 }),
    Object.freeze({ id: "db-7", gap: 1, order: 8 }),
    Object.freeze({ id: "db-6", gap: 1, order: 2 }),
  ]);
  const result = insertCaptureCheckpoint(points, "db-9", 1, 1);
  assert.deepEqual(result, [
    { id: "db-6", gap: 1, order: 0 }, { id: "db-9", gap: 1, order: 1 },
    { id: "db-7", gap: 1, order: 2 }, { id: "db-8", gap: 2, order: 3 },
  ]);
  assert.equal(points[0].id, "db-8");
  assert.equal(points[1].order, 8);
});

test("before-first and after-last original-step gaps remain distinct", () => {
  const points = insertCaptureCheckpoint(insertCaptureCheckpoint([], "db-1", 0, 0), "db-2", 7, 0);
  assert.deepEqual(points, [{ id: "db-1", gap: 0, order: 0 }, { id: "db-2", gap: 7, order: 0 }]);
});
