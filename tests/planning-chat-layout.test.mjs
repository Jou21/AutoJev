// Static rendering and layout contracts only: no live browser, LLM or application execution.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { transpileModule, ModuleKind, JsxEmit, ScriptTarget } from "typescript";

const require = createRequire(import.meta.url);
const source = await readFile(new URL("../app/components/PlanningChat.tsx", import.meta.url), "utf8");
const compiled = transpileModule(source, { fileName: "PlanningChat.tsx", compilerOptions: {
  module: ModuleKind.CommonJS, target: ScriptTarget.ES2022, jsx: JsxEmit.ReactJSX,
} }).outputText;
const compiledModule = { exports: {} };
new Function("require", "module", "exports", compiled)(name => name.endsWith(".css") ? {} : require(name), compiledModule, compiledModule.exports);
const { createElement } = require("react"), { renderToStaticMarkup } = require("react-dom/server");
const render = (props = {}) => renderToStaticMarkup(createElement(compiledModule.exports.default, {
  id: "suite-planning-chat", request() { assert.fail("No request while rendering fixtures"); }, model: null,
  onModelChange() {}, messages: [], busy: false, value: "", onValueChange() {},
  onSend() { assert.fail("No automatic message sending"); }, error: "", ...props,
}));

test("the composer and model chooser are outside the transcript's scroll area", () => {
  const html = render();
  assert.match(html, /aria-label="Chat de planejamento com IA"/);
  assert.match(html, /planning-chat-heading[^]*Assistente de testes/);
  const bodyIndex = html.indexOf('class="planning-chat-body"');
  const bottomIndex = html.indexOf('class="planning-chat-bottom"');
  assert.ok(bodyIndex > -1 && bottomIndex > bodyIndex);
  const transcript = html.slice(bodyIndex, bottomIndex), composer = html.slice(bottomIndex);
  assert.match(transcript, /role="log"/);
  assert.doesNotMatch(transcript, /<textarea|planning-chat-model-trigger|planning-chat-send/);
  assert.match(composer, /aria-label="Mensagem para o assistente"/);
  assert.match(composer, /maxLength="4000"/);
  assert.match(composer, /Modelo de IA do chat:/);
  assert.match(composer, /aria-label="Enviar"/);
  assert.match(html, /Só planejamento/);
  assert.doesNotMatch(html, /VAMOS PLANEJAR JUNTOS|<br\/>/);
});

test("long conversations and suggested actions stay in the log without duplicating or hiding the composer", () => {
  const messages = Array.from({ length: 60 }, (_, index) => ({ role: index % 2 ? "assistant" : "user", content: `Mensagem ${index}: ` + "Detalhes do roteiro. ".repeat(20) }));
  const html = render({ messages, value: "Minha próxima pergunta", children: createElement("button", { type: "button" }, "Revisar CT sugerido") });
  assert.equal((html.match(/class="suite-chat-message"/g) || []).length, 60);
  assert.equal((html.match(/<textarea/g) || []).length, 1);
  assert.ok(html.indexOf("Revisar CT sugerido") < html.indexOf('class="planning-chat-bottom"'));
  assert.match(html, /Minha próxima pergunta<\/textarea>/);
  assert.doesNotMatch(html, /suite-chat-welcome/);
});

test("the welcome invites comparing a named screen's CRUD with the existing base without sending automatically", () => {
  for (const purpose of ["base", "execution"]) {
    const html = render({ purpose });
    assert.match(html, /CRUD da tela \[nome\]/);
    assert.match(html, /Comparo com sua base/);
    assert.match(html, /Você revisa antes de adicionar/);
    assert.match(html, /aria-label="Mensagem para o assistente"/);
  }
});

test("busy, error and unsafe model text do not replace the message composer", () => {
  const html = render({ busy: true, error: "A resposta não chegou. Tente novamente.", messages: [
    { role: "assistant", content: '<script>alert("x")</script> **Revise** `CT001`' },
  ] });
  assert.match(html, /aria-busy="true"/);
  assert.match(html, /Organizando seus testes/);
  assert.match(html, /role="alert"/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /<strong>Revise<\/strong>/);
  assert.match(html, /<code>CT001<\/code>/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /<textarea/);
  assert.match(html, /aria-label="Enviar"[^>]*disabled/);
});

test("chat height follows its work pane and only the transcript can grow", async () => {
  const css = await readFile(new URL("../app/components/PlanningChat.css", import.meta.url), "utf8");
  assert.match(css, /\.panel.suite-chat\s*\{[^}]*container: planning-chat \/ size;[^}]*height: 100%;[^}]*min-height: 0;/);
  assert.match(css, /\.planning-chat-body\s*\{[^}]*flex: 1;[^}]*min-height: 0;/);
  assert.match(css, /\.suite-chat-messages\s*\{[^}]*min-height: 0;[^}]*overflow: auto;/);
  assert.match(css, /\.planning-chat-bottom\s*\{[^}]*flex-shrink: 0;/);
  assert.match(css, /textarea\s*\{[^}]*max-height: min\(128px, 18dvh\);[^}]*overflow-y: auto;/);
  assert.doesNotMatch(css, /height:\s*(?:680|700|780)px|min-height:\s*(?:520|560)px/);
  assert.doesNotMatch(css, /@media \(max-width: 1400px\)/, "The chat never falls below all planner content at the old breakpoint");
  assert.match(css, /@container planning-chat \(max-height: 420px\)/);
  assert.match(css, /\.planning-chat-model-menu\s*\{[^}]*max-height: calc\(100cqh - 140px\);/);
  assert.match(css, /\.planning-chat-model-options\s*\{[^}]*min-height: 0;[^}]*overflow: auto;/);
});
