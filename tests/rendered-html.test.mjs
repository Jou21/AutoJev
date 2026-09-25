import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the AutoJev application shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>AutoJev — Exploração inteligente de aplicações<\/title>/i);
  assert.match(html, /AutoJev/);
  assert.match(html, /JEV no comando|Boa exploração começa/);
  assert.match(html, /Nova exploração/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("removes disposable starter assets and metadata", async () => {
  const [page, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(page, /AutoJevApp/);
  assert.match(layout, /pt-BR/);
  assert.match(layout, /AutoJev/);
  assert.doesNotMatch(layout, /codex-preview|Starter Project/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.match(packageJson, /lucide-react/);
  await assert.rejects(access(new URL("app/_sites-preview", root)));
});

test("offers an optional focused exploration scope", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");

  assert.match(source, /useState<"all" \| "focused">\("all"\)/);
  assert.match(source, /Tela, menu ou submenu/);
  assert.match(source, /Toda a aplicação/);
  assert.match(source, /Tela\/menu específico/);
  assert.match(source, /Configurações &gt; Usuários &gt; Permissões|Configurações > Usuários > Permissões/);
  assert.match(source, /focusMode === "focused" && !focusArea\.trim\(\)/);
  assert.match(source, /if \(normalizedFocusArea\) explorationOptions\.focus_area = normalizedFocusArea/);
  assert.match(source, /explorationFocusArea\(exploration\.options\)/);
});
