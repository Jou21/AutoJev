import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { transpileModule, ModuleKind, ScriptTarget } from "typescript";

const source = await readFile(new URL("../app/components/suite-horizontal-rail.ts", import.meta.url), "utf8");
const compiled = transpileModule(source, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022 } });
const compiledModule = { exports: {} };
new Function("module", "exports", compiled.outputText)(compiledModule, compiledModule.exports);
const { horizontalRailDelta, bindHorizontalRail, bindHorizontalRailPan, moveRailItem, placeRailItem } = compiledModule.exports;
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

function panFixture({ overflow = true, targetKind = "source" } = {}) {
  const handlers = new Map(), captures = new Set(), removed = [];
  let left = 200, prevented = 0, stopped = 0;
  const surface = { dataset: {}, setPointerCapture(id) { captures.add(id); },
    hasPointerCapture(id) { return captures.has(id); }, releasePointerCapture(id) { captures.delete(id); },
    addEventListener(type, callback, options) { handlers.set(type, { callback, options }); },
    removeEventListener(type, callback, options) { assert.equal(handlers.get(type).callback, callback); assert.equal(options, type === "click" ? true : undefined); removed.push(type); handlers.delete(type); } };
  const rail = { closest() { return surface; }, addEventListener() {}, scrollWidth: overflow ? 1400 : 400, clientWidth: 400, clientHeight: 180,
    get scrollLeft() { return left; }, set scrollLeft(value) { left = value; },
    getBoundingClientRect() { return { top: 100 }; }, focus(options) { assert.deepEqual(options, { preventScroll: true }); this.focused = true; } };
  const target = targetKind === "scrollbar" ? rail : { closest(selector) {
    if (selector.startsWith("a, ")) return targetKind === "protected" ? this : null;
    if (selector === "button") return ["source", "control"].includes(targetKind) ? this : null;
    if (selector === "button[data-original-step]") return targetKind === "source" ? this : null;
    return null;
  } };
  const cleanup = bindHorizontalRailPan(rail);
  const fire = (type, changes = {}) => {
    const event = { pointerType: "mouse", pointerId: 1, isPrimary: true, button: 0, buttons: 1, clientX: 500, clientY: 180,
      detail: 1, target, preventDefault() { prevented++; }, stopPropagation() { stopped++; }, ...changes };
    handlers.get(type)?.callback(event); return event;
  };
  return { rail, surface, handlers, captures, removed, fire, cleanup, get prevented() { return prevented; }, get stopped() { return stopped; } };
}

test("dragging the stage browses horizontally in either direction without moving beyond its ends", () => {
  const fixture = panFixture();
  assert.equal(fixture.surface.dataset.railPan, "true");
  assert.deepEqual(fixture.handlers.get("pointermove").options, { passive: false });
  assert.equal(fixture.handlers.get("click").options, true, "Stop the post-drag click before React opens an image");
  fixture.fire("pointerdown"); fixture.fire("pointermove", { clientX: 380, clientY: 185 });
  assert.equal(fixture.rail.scrollLeft, 320); assert.equal(fixture.surface.dataset.panning, "true");
  assert.equal(fixture.rail.focused, true); assert.ok(fixture.captures.has(1));
  fixture.fire("pointermove", { clientX: -2000 }); assert.equal(fixture.rail.scrollLeft, 1000);
  fixture.fire("pointermove", { clientX: 2000 }); assert.equal(fixture.rail.scrollLeft, 0);
  fixture.fire("pointerup"); fixture.fire("click");
  assert.equal(fixture.stopped, 1); assert.equal(fixture.captures.size, 0); assert.equal(fixture.surface.dataset.panning, undefined);
  fixture.cleanup(); assert.equal(fixture.surface.dataset.railPan, undefined);
  assert.equal(fixture.removed.length, 6);
});

test("a normal or shaky click still opens a source card, and keyboard clicks are never consumed", () => {
  const fixture = panFixture();
  fixture.fire("pointerdown"); fixture.fire("pointermove", { clientX: 497, clientY: 181 });
  fixture.fire("pointerup"); fixture.fire("click");
  assert.equal(fixture.rail.scrollLeft, 200); assert.equal(fixture.prevented, 0); assert.equal(fixture.stopped, 0);
  fixture.fire("pointerdown"); fixture.fire("pointermove", { clientX: 450 }); fixture.fire("pointerup");
  fixture.fire("click", { detail: 0 }); assert.equal(fixture.stopped, 0);
  fixture.fire("pointerdown"); fixture.fire("pointerup"); fixture.fire("click");
  assert.equal(fixture.stopped, 0, "An old drag cannot swallow the next intentional click");
  fixture.cleanup();
});

test("touch, scrollbar, insertion buttons, draggable additions and image viewers retain native behavior", () => {
  for (const setup of [{ event: { pointerType: "touch" } }, { event: { button: 1 } }, { event: { button: 2 } },
    { event: { isPrimary: false } }, { event: { ctrlKey: true } }, { event: { altKey: true } }, { event: { metaKey: true } },
    { overflow: false }, { targetKind: "control" }, { targetKind: "protected" }, { targetKind: "scrollbar", event: { clientY: 285 } }]) {
    const fixture = panFixture(setup);
    fixture.fire("pointerdown", setup.event); fixture.fire("pointermove", { clientX: 400 }); fixture.fire("pointerup"); fixture.fire("click");
    assert.equal(fixture.rail.scrollLeft, 200, JSON.stringify(setup));
    assert.equal(fixture.prevented, 0); assert.equal(fixture.stopped, 0); assert.equal(fixture.captures.size, 0);
    fixture.cleanup();
  }
});

test("background drags work and vertical-only motion does not activate horizontal panning", () => {
  const fixture = panFixture({ targetKind: "background" });
  fixture.fire("pointerdown"); fixture.fire("pointermove", { clientX: 498, clientY: 150 });
  assert.equal(fixture.rail.scrollLeft, 200); assert.equal(fixture.surface.dataset.panning, undefined);
  fixture.fire("pointermove", { clientX: 450, clientY: 180 }); assert.equal(fixture.rail.scrollLeft, 250);
  fixture.cleanup();
});

test("panning releases capture on cancellation, lost capture, released buttons and unmount", () => {
  for (const type of ["pointercancel", "lostpointercapture", "pointermove", "cleanup"]) {
    const fixture = panFixture();
    fixture.fire("pointerdown"); fixture.fire("pointermove", { clientX: 400 });
    fixture.fire("pointermove", { pointerId: 2, clientX: 100 }); fixture.fire("pointerup", { pointerId: 2 });
    assert.equal(fixture.rail.scrollLeft, 300); assert.ok(fixture.captures.has(1));
    if (type === "cleanup") fixture.cleanup(); else fixture.fire(type, { buttons: 0 });
    assert.equal(fixture.surface.dataset.panning, undefined); assert.equal(fixture.captures.size, 0);
    if (type !== "cleanup") fixture.cleanup();
  }
});

test("failed capture leaves cards clickable and missing rails require no listeners", () => {
  const fixture = panFixture();
  fixture.surface.setPointerCapture = () => { throw new Error("Released pointer"); };
  fixture.fire("pointerdown"); fixture.fire("pointermove", { clientX: 400 }); fixture.fire("pointerup"); fixture.fire("click");
  assert.equal(fixture.rail.scrollLeft, 200); assert.equal(fixture.stopped, 0); assert.equal(fixture.surface.dataset.panning, undefined);
  fixture.cleanup();
  bindHorizontalRailPan(null)(); bindHorizontalRailPan({})();
  bindHorizontalRailPan({ addEventListener() {}, closest() { return null; } })();
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

test("drag insertion supports either side and moving to the first or final slot", () => {
  const items = ["CT001", "CT003", "CT005", "CT009"];
  assert.deepEqual(placeRailItem(items, 2, 0), ["CT005", "CT001", "CT003", "CT009"]);
  assert.deepEqual(placeRailItem(items, 0, 3), ["CT003", "CT005", "CT001", "CT009"]);
  assert.deepEqual(placeRailItem(items, 0, 4), ["CT003", "CT005", "CT009", "CT001"]);
  assert.deepEqual(placeRailItem(items, 3, 2), ["CT001", "CT003", "CT009", "CT005"]);
  assert.deepEqual(items, ["CT001", "CT003", "CT005", "CT009"]);
});

test("dropping next to itself or using stale and invalid positions is a true no-op", () => {
  const items = ["CT001", "CT003"];
  for (const [from, slot] of [[0, 0], [0, 1], [1, 1], [1, 2], [-1, 0], [2, 0], [0, -1], [0, 3], [0.5, 0], [0, NaN]]) assert.equal(placeRailItem(items, from, slot), items);
  assert.deepEqual(placeRailItem([], 0, 0), []);
});

test("dragging keeps every frozen recipe, capture plan and integration attached to the same case", () => {
  const plan = Object.freeze({ version: 1, checkpoints: Object.freeze([{ id: "db-1", gap: 1, order: 0 }]) });
  const points = Object.freeze([{ id: "db-2", gap: 0, order: 0, kind: "sql" }]);
  const items = Object.freeze([Object.freeze({ case_id: "CT001", capture_plan: plan, source_exploration_id: "original" }), Object.freeze({ case_id: "CT003", planned_integrations: points })]);
  const reordered = placeRailItem(items, 0, 2);
  assert.equal(reordered[1], items[0]); assert.equal(reordered[0], items[1]);
  assert.equal(reordered[1].capture_plan, plan); assert.equal(reordered[0].planned_integrations, points);
  assert.equal(reordered[1].source_exploration_id, "original");
});

test("the editable step rail fills the remaining board height while the test rail fits its cards", async () => {
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.suite-flow-board\s*\{[^}]*container: suite-flow \/ size;[^}]*grid-template-rows: max-content minmax\(0, 1fr\)/);
  assert.match(css, /\.suite-flow-board \.suite-selected-list\s*\{[^}]*flex: none;[^}]*height: calc\(var\(--rail-card-size\) \+ 20px\);/);
  assert.match(css, /\.suite-flow-board \.suite-horizontal-rail\s*\{[^}]*70cqh[^}]*overflow-x: auto; overflow-y: hidden;/);
  assert.match(css, /\.suite-flow-board \.suite-test-card, \.suite-flow-board \.suite-source-step\s*\{[^}]*width: var\(--rail-card-size\); height: var\(--rail-card-size\)/);
  assert.match(css, /@media \(max-height: 560px\)/, "Extremely short windows retain an explicit, reachable fallback rather than clipping controls");
  assert.match(css, /@container suite-flow \(max-width: 640px\)/, "The palette becomes horizontal when the canvas is narrow");
});

test("step tools are vertically centered without making overflowing tools or narrow horizontal palettes unreachable", async () => {
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\.suite-step-palette\s*\{[^}]*flex-direction: column;[^}]*justify-content: safe center;[^}]*padding: 6px 8px 6px 0;[^}]*overflow-y: auto;/);
  assert.match(css, /@container suite-flow \(max-width: 640px\)\s*\{[^]*?\.suite-step-palette\s*\{[^}]*flex-direction: row;[^}]*justify-content: flex-start; align-items: center;[^}]*overflow-x: auto;/);
});
