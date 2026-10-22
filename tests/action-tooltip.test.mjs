import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { transpileModule, ModuleKind, JsxEmit, ScriptTarget } from "typescript";

const require = createRequire(import.meta.url);
const source = await readFile(new URL("../app/components/ActionTooltip.tsx", import.meta.url), "utf8");
const compiled = transpileModule(source, { fileName: "ActionTooltip.tsx", compilerOptions: {
  module: ModuleKind.CommonJS, target: ScriptTarget.ES2022, jsx: JsxEmit.ReactJSX,
} }).outputText;
const compiledModule = { exports: {} };
new Function("require", "module", "exports", compiled)(name => name.endsWith(".css") ? {} : require(name), compiledModule, compiledModule.exports);
const { createElement } = require("react"), { renderToStaticMarkup } = require("react-dom/server");

test("tooltip preserves the direct icon button, its accessible name and disabled state without opening or clicking", () => {
  const html = renderToStaticMarkup(createElement(compiledModule.exports.default, { text: "Arquivar sem apagar resultados." },
    createElement("button", { type: "button", className: "icon-button", disabled: true, "aria-label": "Arquivar CT001", onClick() { assert.fail("Rendering cannot apply an action"); } }, "Ícone")));
  assert.match(html, /^<button /);
  assert.match(html, /class="icon-button"/);
  assert.match(html, /disabled=""/);
  assert.match(html, /aria-label="Arquivar CT001"/);
  assert.doesNotMatch(html, /role="tooltip"|<span|<div/);
});

test("an existing description remains attached and no delayed native title competes with the custom tooltip", () => {
  const html = renderToStaticMarkup(createElement(compiledModule.exports.default, { text: "Duplicar para revisar." },
    createElement("button", { "aria-describedby": "existing-description", title: "Native title" }, "Duplicar")));
  assert.match(html, /aria-describedby="existing-description"/);
  assert.doesNotMatch(html, /title=|role="tooltip"/);
});
