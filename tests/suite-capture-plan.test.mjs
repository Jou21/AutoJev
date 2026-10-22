import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { transpileModule, ModuleKind, ScriptTarget } from "typescript";

const source = await readFile(new URL("../app/components/suite-capture-plan.ts", import.meta.url), "utf8");
const compiled = transpileModule(source, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022 } });
const compiledModule = { exports: {} };
new Function("module", "exports", compiled.outputText)(compiledModule, compiledModule.exports);
const { insertCaptureCheckpoint, plannedSteps, placePlannedStep, nearestStepDrop } = compiledModule.exports;

test("the whole card width selects its nearest insertion gap, without hitting a +", () => {
  const slots = [0, 1, 2].map(gap => ({ gap, index: 0, left: 100 + gap * 240, width: 28 }));
  // Original cards occupy x=140..332 and x=380..572; all these points miss +.
  for (const [x, gap] of [[50, 0], [145, 0], [300, 1], [332, 1], [380, 1], [550, 2], [900, 2]]) {
    assert.deepEqual(nearestStepDrop(x, slots), { gap, index: 0 });
  }
});

test("an expanded preview keeps its destination stable after the following cards shift", () => {
  const slots = [{ gap: 0, index: 0, left: 100, width: 28 }, { gap: 1, index: 0, left: 340, width: 28 }, { gap: 2, index: 0, left: 778, width: 28 }];
  const current = { gap: 1, index: 0 }, preview = { left: 374, width: 192 };
  for (const x of [374, 450, 555, 566]) assert.deepEqual(nearestStepDrop(x, slots, current, preview), current);
  assert.deepEqual(nearestStepDrop(740, slots, current, preview), { gap: 2, index: 0 });
  assert.deepEqual(nearestStepDrop(170, slots, current, preview), { gap: 0, index: 0 });
});

test("nearest slots distinguish mixed extras, scrolled positions and invalid measurements", () => {
  const slots = [{ gap: 2, index: 0, left: -180, width: 28 }, { gap: 2, index: 1, left: 60, width: 28 }, { gap: 2, index: 2, left: 300, width: 28 }];
  assert.deepEqual(nearestStepDrop(90, slots), { gap: 2, index: 1 });
  assert.deepEqual(nearestStepDrop(280, slots), { gap: 2, index: 2 });
  assert.equal(nearestStepDrop(NaN, slots), null);
  assert.equal(nearestStepDrop(50, [{ gap: -1, index: 0, left: 0, width: 28 }, { gap: 0, index: 0, left: 0, width: 0 }]), null);
  assert.equal(nearestStepDrop(50, []), null);
});

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

test("mixed types share exact insertion positions and same-gap dragging never duplicates a card", () => {
  let points = plannedSteps([{ id: "db-1", gap: 1, order: 0 }], [{ id: "db-2", gap: 1, order: 1, kind: "sws" }]);
  points = placePlannedStep(points, { id: "db-3", gap: 1, order: 0, kind: "sql" }, 1, 1);
  assert.deepEqual(points.map(point => point.kind), ["capture", "sql", "sws"]);
  const before = structuredClone(points);
  assert.deepEqual(placePlannedStep(points, points[1], 1, 1), before);
  assert.deepEqual(placePlannedStep(points, points[1], 1, 2), before);
  const moved = placePlannedStep(points, points[0], 1, 3);
  assert.deepEqual(moved.map(point => point.id), ["db-3", "db-2", "db-1"]);
  assert.deepEqual(moved.map(point => point.order), [0, 1, 2]);
  assert.deepEqual(points, before);
});

test("moving a mixed card before the first or after the last original preserves all other identities and gaps", () => {
  const points = plannedSteps([{ id: "db-1", gap: 1, order: 0 }], [{ id: "db-2", gap: 1, order: 1, kind: "sql" }]);
  const moved = placePlannedStep(points, points[1], 7, 0);
  assert.deepEqual(moved, [{ id: "db-1", gap: 1, order: 0, kind: "capture" }, { id: "db-2", gap: 7, order: 0, kind: "sql" }]);
  assert.deepEqual(placePlannedStep(moved, moved[1], 0, 0).map(point => point.gap), [0, 1]);
  assert.equal(points[1].gap, 1);
});
