import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { transpileModule, ModuleKind, ScriptTarget } from "typescript";

const source = await readFile(new URL("../app/components/suite-horizontal-rail.ts", import.meta.url), "utf8");
const compiled = transpileModule(source, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022 } });
const compiledModule = { exports: {} };
new Function("module", "exports", compiled.outputText)(compiledModule, compiledModule.exports);
const { horizontalRailDelta, bindHorizontalRail, moveRailItem } = compiledModule.exports;
const wheel = (changes = {}) => ({ deltaX: 0, deltaY: 100, deltaMode: 0, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false, ...changes });

test("vertical wheels translate to horizontal movement in pixels, lines and pages, bounded by the rail", () => {
  const size = { scrollLeft: 50, scrollWidth: 1000, clientWidth: 300 };
  assert.equal(horizontalRailDelta(wheel(), size), 100);
  assert.equal(horizontalRailDelta(wheel({ deltaY: 2, deltaMode: 1 }), size), 32);
  assert.equal(horizontalRailDelta(wheel({ deltaY: 1, deltaMode: 2 }), size), 300);
  assert.equal(horizontalRailDelta(wheel({ deltaY: -100 }), size), -50);
  assert.equal(horizontalRailDelta(wheel({ deltaY: 1000 }), size), 650);
  assert.equal(horizontalRailDelta(wheel(), { ...size, scrollLeft: 700 }), 0);
  assert.equal(horizontalRailDelta(wheel({ deltaY: -10 }), { ...size, scrollLeft: 0 }), 0);
});

test("native horizontal gestures, Shift-wheel and browser zoom are not intercepted", () => {
  const size = { scrollLeft: 50, scrollWidth: 1000, clientWidth: 300 };
  for (const changes of [{ deltaX: 101 }, { deltaY: 0 }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { metaKey: true }]) {
    assert.equal(horizontalRailDelta(wheel(changes), size), 0);
  }
  assert.equal(horizontalRailDelta(wheel(), { scrollLeft: 0, scrollWidth: 300, clientWidth: 300 }), 0);
});

test("wheel handlers are non-passive, keyboard scrolling is local, and cleanup removes both listeners", () => {
  const handlers = new Map(), removed = [];
  const rail = { scrollLeft: 0, scrollWidth: 1000, clientWidth: 300,
    addEventListener(type, fn, options) { handlers.set(type, { fn, options }); },
    removeEventListener(type, fn) { assert.equal(handlers.get(type).fn, fn); removed.push(type); } };
  const cleanup = bindHorizontalRail(rail);
  assert.deepEqual(handlers.get("wheel").options, { passive: false });
  let prevented = 0;
  handlers.get("wheel").fn({ ...wheel(), preventDefault() { prevented++; } });
  assert.equal(rail.scrollLeft, 100); assert.equal(prevented, 1);
  const press = (key, target = rail, changes = {}) => handlers.get("keydown").fn({ key, target, ...changes, preventDefault() { prevented++; } });
  press("ArrowRight"); assert.equal(rail.scrollLeft, 340);
  press("End"); assert.equal(rail.scrollLeft, 700);
  press("Home"); assert.equal(rail.scrollLeft, 0);
  press("ArrowLeft"); assert.equal(rail.scrollLeft, 0);
  const before = prevented;
  press("ArrowRight", {}); press("ArrowDown"); press("ArrowRight", rail, { ctrlKey: true });
  assert.equal(prevented, before);
  cleanup(); assert.deepEqual(removed, ["wheel", "keydown"]);
});

test("reordering preserves case identities and capture-plan references without changing the source selection", () => {
  const print = { version: 1, checkpoints: [{ id: "print-1", gap: 1, order: 0 }] };
  const items = Object.freeze([Object.freeze({ case_id: "CT001", capture_plan: print }), Object.freeze({ case_id: "CT003" })]);
  const moved = moveRailItem(items, 1, -1);
  assert.deepEqual(moved.map(item => item.case_id), ["CT003", "CT001"]);
  assert.equal(moved[1].capture_plan, print);
  assert.equal(items[0].case_id, "CT001");
  assert.equal(moveRailItem(items, 0, -1), items);
  assert.equal(moveRailItem(items, 1, 1), items);
});

test("both card rails use the available height and never grow into a vertical list", async () => {
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.suite-flow-board\s*\{[^}]*container: suite-flow \/ size;[^}]*grid-template-rows: minmax\(0, 1fr\) minmax\(0, 1fr\)/);
  assert.match(css, /\.suite-flow-board \.suite-horizontal-rail\s*\{[^}]*50cqh[^}]*overflow-x: auto; overflow-y: hidden;/);
  assert.match(css, /\.suite-flow-board \.suite-test-card, \.suite-flow-board \.suite-source-step\s*\{[^}]*width: var\(--rail-card-size\); height: var\(--rail-card-size\)/);
  assert.match(css, /@media \(max-height: 560px\)/, "Extremely short windows retain an explicit, reachable fallback rather than clipping controls");
});
