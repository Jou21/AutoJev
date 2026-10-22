import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { transpileModule, ModuleKind, JsxEmit, ScriptTarget } from "typescript";

const require = createRequire(import.meta.url), module = { exports: {} };
const source = await readFile(new URL("../app/components/StepDragOverlay.tsx", import.meta.url), "utf8");
const result = transpileModule(source, { compilerOptions: { module: ModuleKind.CommonJS, jsx: JsxEmit.ReactJSX, target: ScriptTarget.ES2022 } });
new Function("require", "module", "exports", result.outputText)(require, module, module.exports);
const { stepDragPosition, default: Overlay } = module.exports;

test("the full-size drag card follows the pointer while remaining inside desktop and narrow viewports", () => {
  assert.deepEqual(stepDragPosition(300, 400, 192, 1366, 768), { left: 316, top: 332.8 });
  for (const [width, height, size] of [[1366, 768, 192], [390, 700, 98], [320, 500, 160]]) {
    for (const [x, y] of [[0, 0], [width - 1, height - 1], [-500, -100], [width * 2, height * 2]]) {
      const position = stepDragPosition(x, y, size, width, height);
      assert.ok(position.left >= 8 && position.top >= 8);
      assert.ok(position.left + size <= width - 8 && position.top + size <= height - 8);
    }
  }
});

test("the transient browser-only overlay does not introduce a portal during server rendering", () => {
  assert.equal(renderToStaticMarkup(createElement(Overlay, { preview: { kind: "capture", title: "Aguardar print", size: 160, x: 20, y: 20, caseId: "CT001" }, onCancel() {} })), "");
});
