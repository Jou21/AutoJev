// Exercise the actual overlay installer/CSS through the official Playwright MCP.
// The local fixture and injected UI exist only in this isolated browser context.
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { homedir } from "node:os";
import { resolve } from "node:path";
import { mkdir, writeFile } from "node:fs/promises";

const root = resolve(import.meta.dirname, "..");
const overlay = JSON.parse(execFileSync(resolve(root, "backend/.venv/bin/python"), ["-c",
  "import json;from autojev.adapters.playwright_executor import PlaywrightExecutor as E;print(json.dumps({'css':E._SIDEPANEL_CSS,'installer':E._SIDEPANEL_INSTALLER}))"],
  { cwd: resolve(root, "backend"), encoding: "utf8" }));
const modules = process.env.AUTOJEV_MCP_MODULES || resolve(homedir(), ".local/share/autojev-playwright-mcp/node_modules");
const { Client } = await import(pathToFileURL(resolve(modules, "@modelcontextprotocol/sdk/dist/esm/client/index.js")));
const { StreamableHTTPClientTransport } = await import(pathToFileURL(resolve(modules, "@modelcontextprotocol/sdk/dist/esm/client/streamableHttp.js")));
const client = new Client({ name: "autojev-panel-validation", version: "1.0.0" });
try {
  await client.connect(new StreamableHTTPClientTransport(new URL(process.env.AUTOJEV_MCP_URL || "http://localhost:8931/mcp")));
  const code = `async (page) => {
    const css = ${JSON.stringify(overlay.css)};
    const installer = (${overlay.installer});
    const results = [];
    const check = (name, passed, actual) => results.push({name,status:passed?'PASS':'FAIL',actual});
    await page.route('http://localhost:3000/__autojev_panel_mcp_fixture', route => route.fulfill({contentType:'text/html',body:'<!doctype html><html><head><title>Local overlay fixture</title><style>body{margin:0;background:#eefcf6;height:3000px;font-family:system-ui}button#behind{position:fixed;right:25px;top:30px;width:36px;height:36px}h1{margin:80px}</style></head><body><h1>Fixture local do painel</h1><button id="behind" onclick="window.clicks=(window.clicks||0)+1">Site</button></body></html>'}));
    await page.goto('http://localhost:3000/__autojev_panel_mcp_fixture');
    await page.evaluate(()=>sessionStorage.removeItem('autojev-panel-open'));
    await page.evaluate(installer,css);
    await page.evaluate(()=>window.originalPanel=document.getElementById('autojev-sidepanel'));
    const state = () => page.evaluate(()=>{
      const panel=document.getElementById('autojev-sidepanel'),rail=panel.querySelector('.autojev-panel-rail'),surface=panel.querySelector('.autojev-panel-surface');
      const rect=panel.getBoundingClientRect(),r=rail.getBoundingClientRect();
      return {same:panel===window.originalPanel,panels:document.querySelectorAll('#autojev-sidepanel').length,width:rect.width,top:rect.top,railHeight:r.height,opacity:getComputedStyle(panel).opacity,surfaceOpacity:getComputedStyle(surface).opacity,pinned:panel.dataset.pinned,background:getComputedStyle(panel).backgroundColor,shadow:getComputedStyle(panel).boxShadow};
    });
    for(const width of [1440,390]) {
      await page.setViewportSize({width,height:900});
      await page.evaluate(()=>{const p=document.getElementById('autojev-sidepanel');p.dataset.pinned='false';p.dataset.mode='exploring';p.dataset.automation='false';});
      const collapsed=await state();
      check('Compact rail without full-height shadow '+width,collapsed.railHeight===54||collapsed.railHeight===52,collapsed);
      check('Hidden surface, translucent rail '+width,Number(collapsed.opacity)>0&&Number(collapsed.opacity)<1&&collapsed.surfaceOpacity==='0',collapsed);
      check('Transparent outer area has no shadow '+width,collapsed.background==='rgba(0, 0, 0, 0)'&&collapsed.shadow==='none',collapsed);
      await page.locator('.autojev-panel-rail').click();
      const expanded=await state();
      check('Immediate click opens vibrant panel '+width,expanded.pinned==='true'&&expanded.opacity==='1'&&expanded.surfaceOpacity==='1',expanded);
      await page.mouse.move(width-50,70);
      const samples=[];
      for(let n=0;n<12;n++) {
        await page.evaluate(installer,css);
        await page.evaluate(n=>{
          const p=document.getElementById('autojev-sidepanel');p.dataset.automation=n%2?'true':'false';
          document.getElementById('autojev-panel-title').textContent='Etapa '+n;
          const row=document.createElement('li');row.textContent='Atualização local '+n;document.getElementById('autojev-panel-log').append(row);
        },n);
        samples.push(await state());
      }
      check('No DOM replacement, opacity blink or width change during updates '+width,samples.every(s=>s.same&&s.panels===1&&s.width===expanded.width&&s.opacity==='1'&&s.surfaceOpacity==='1'),samples);
      await page.evaluate(()=>{const p=document.getElementById('autojev-sidepanel');p.dataset.automation='true';});
      await page.locator('#behind').click();
      check('Automation clicks the site without hiding panel '+width,await page.evaluate(()=>window.clicks>0)&&(await state()).opacity==='1',await state());
      await page.evaluate(()=>window.scrollTo(0,1500));
      check('Panel stays anchored while page scrolls '+width,(await state()).top===expanded.top,await state());
      await page.evaluate(()=>{const p=document.getElementById('autojev-sidepanel');p.dataset.automation='false';p.dataset.pinned='false';p.dataset.mode='attention';document.getElementById('autojev-panel-action').hidden=false;});
      check('Manual intervention is visible without pinning '+width,(await state()).opacity==='1'&&(await state()).surfaceOpacity==='1',await state());
      await page.screenshot({path:${JSON.stringify(resolve(root, "data/mcp-validation"))}+'/overlay-attention-'+width+'.png'});
    }
    return {source:'Playwright MCP',scope:'Actual production overlay installer/CSS on an isolated local fixture',counts:{pass:results.filter(x=>x.status==='PASS').length,fail:results.filter(x=>x.status==='FAIL').length},results};
  }`;
  const result = await client.callTool({ name: "browser_run_code_unsafe", arguments: { code } }, undefined, { timeout: 90000 });
  const text = result.content.filter(x => x.type === "text").map(x => x.text).join("\n");
  if (result.isError) throw new Error((text.match(/### Error\s*([^\n]+)/)||[])[1]||"MCP overlay validation failed");
  const measurements=JSON.parse(text.match(/### Result\s*([\s\S]*?)(?:\n###|$)/)[1]);
  await mkdir(resolve(root,"data/mcp-validation"),{recursive:true});
  await writeFile(resolve(root,"data/mcp-validation/overlay-checks.json"),JSON.stringify(measurements,null,2));
  console.log(JSON.stringify({source:measurements.source,counts:measurements.counts,artifact:'data/mcp-validation/overlay-checks.json'}));
  if(measurements.counts.fail) process.exitCode=1;
} finally { await client.close(); }
