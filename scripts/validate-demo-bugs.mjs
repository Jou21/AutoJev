// Independent known-defect benchmark through actual Playwright MCP.
// Login secrets and full MCP transcripts remain in memory and are never logged.
import { pathToFileURL } from "node:url";
import { homedir } from "node:os";
import { resolve } from "node:path";
import { mkdir, writeFile } from "node:fs/promises";

const root = resolve(import.meta.dirname, "..");
process.loadEnvFile(resolve(root, "backend/.env"));
const password = process.env.PASSWORD_SAUCEDEMO;
if (!password) throw new Error("Application password missing");
const modules = process.env.AUTOJEV_MCP_MODULES || resolve(homedir(), ".local/share/autojev-playwright-mcp/node_modules");
const { Client } = await import(pathToFileURL(resolve(modules, "@modelcontextprotocol/sdk/dist/esm/client/index.js")));
const { StreamableHTTPClientTransport } = await import(pathToFileURL(resolve(modules, "@modelcontextprotocol/sdk/dist/esm/client/streamableHttp.js")));
const client = new Client({ name: "autojev-known-defect-benchmark", version: "1.0.0" });
const artifact = "data/mcp-validation/problem-user-business-checks.json";
const redact = value => String(value).replaceAll(password, "[REDACTED]");

try {
  await client.connect(new StreamableHTTPClientTransport(new URL(process.env.AUTOJEV_MCP_URL || "http://localhost:8931/mcp")));
  const code = `async (page) => {
    page.setDefaultTimeout(5000);
    page.setDefaultNavigationTimeout(20000);
    const password = ${JSON.stringify(password)};
    const results = [];
    const safeError = error => String(error.message || error).replaceAll(password, '[REDACTED]').slice(0,240);
    const record = (name, passed, expected, actual) => results.push({name,status:passed?'PASS':'FAIL',expected,actual});
    const attempt = async (name, work) => {
      try { await work(); } catch (error) { results.push({name,status:'BLOCKED',reason:'Independent check could not execute',error:safeError(error)}); }
    };
    const login = async () => {
      await page.goto('https://www.saucedemo.com/');
      await page.locator('[data-test=username]').fill('problem_user');
      await page.locator('[data-test=password]').fill(password);
      await page.locator('[data-test=login-button]').click();
      await page.waitForURL('**/inventory.html');
      await page.locator('.inventory_item').first().waitFor({state:'visible'});
    };
    const productList = () => page.locator('.inventory_item').evaluateAll(cards => cards.map(card => ({
      name:card.querySelector('.inventory_item_name')?.textContent?.trim(),
      price:card.querySelector('.inventory_item_price')?.textContent?.trim()
    })));
    await login();
    const products = await productList();
    record('Product inventory has six distinct products', products.length===6&&new Set(products.map(product=>product.name)).size===6, '6 distinct products', products);

    for (const [option,direction] of [['lohi','ascending'],['hilo','descending']]) {
      await attempt('Price sorting '+direction, async () => {
        await page.locator('[data-test=product-sort-container]').selectOption(option);
        const ordered = await productList();
        const prices = ordered.map(product => Number(product.price?.replace('$','')));
        const sorted = prices.length===products.length&&prices.every(Number.isFinite)&&prices.every((value,index)=>index===0||(direction==='ascending'?prices[index-1]<=value:prices[index-1]>=value));
        record('Price sorting '+direction,sorted,direction+' numeric prices',{selected:await page.locator('[data-test=product-sort-container]').inputValue(),prices});
      });
    }

    for (const product of products) {
      await attempt('Product detail identity: '+product.name, async () => {
        await login();
        await page.locator('.inventory_item_name').filter({hasText:product.name}).first().click();
        await page.locator('.inventory_details_name').waitFor({state:'visible'});
        const detail = {
          name:(await page.locator('.inventory_details_name').innerText()).trim(),
          price:(await page.locator('.inventory_details_price').innerText()).trim(),
          url:page.url()
        };
        record('Product detail identity: '+product.name,detail.name===product.name&&detail.price===product.price,{name:product.name,price:product.price},detail);
      });
    }

    await attempt('Last Name retains entered text', async () => {
      await login();
      const add = page.locator('[data-test=add-to-cart-sauce-labs-backpack]');
      if (await add.count()) await add.click();
      await page.locator('.shopping_cart_link').click();
      await page.locator('[data-test=checkout]').click();
      await page.locator('[data-test=firstName]').fill('AutoJev');
      await page.locator('[data-test=lastName]').fill('Regression');
      await page.locator('[data-test=postalCode]').fill('11111');
      const actual = {
        firstName:await page.locator('[data-test=firstName]').inputValue(),
        lastName:await page.locator('[data-test=lastName]').inputValue(),
        postalCode:await page.locator('[data-test=postalCode]').inputValue()
      };
      record('Last Name retains entered text',actual.lastName==='Regression',{lastName:'Regression'},actual);
    });

    await attempt('Valid checkout progresses to overview', async () => {
      if (!page.url().includes('checkout-step-one.html')) {
        await login();
        const add = page.locator('[data-test=add-to-cart-sauce-labs-backpack]');
        if (await add.count()) await add.click();
        await page.locator('.shopping_cart_link').click();
        await page.locator('[data-test=checkout]').click();
      }
      for (const [field,value] of [['firstName','AutoJev'],['lastName','Regression'],['postalCode','11111']]) await page.locator('[data-test='+field+']').fill(value);
      await page.locator('[data-test=continue]').click();
      // Read the resulting route/error instead of allowing a timeout to hide a business failure.
      await page.waitForTimeout(400);
      const error = page.locator('[data-test=error]');
      const actual = {url:page.url(),validationError:await error.isVisible()?await error.innerText():null};
      record('Valid checkout progresses to overview',page.url().includes('checkout-step-two.html'),'Checkout overview after filling all required fields',actual);
    });

    await page.screenshot({path:${JSON.stringify(resolve(root, "data/mcp-validation/problem-user-checkout.png"))}});
    return {
      source:'Playwright MCP',profile:'problem_user',application:'https://www.saucedemo.com',
      generatedAt:new Date().toISOString(),
      scope:'Numeric sorting, product identity/price, field retention and checkout progression; no inferred image baseline',
      counts:{pass:results.filter(result=>result.status==='PASS').length,fail:results.filter(result=>result.status==='FAIL').length,blocked:results.filter(result=>result.status==='BLOCKED').length},
      results
    };
  }`;
  const response = await client.callTool({ name: "browser_run_code_unsafe", arguments: { code } }, undefined, { timeout: 180000 });
  const transcript = response.content.filter(item => item.type === "text").map(item => item.text).join("\n");
  if (response.isError) {
    const firstLine = (transcript.match(/### Error\s*([^\n]+)/) || [])[1] || "Tool failure";
    throw new Error(`MCP benchmark did not complete: ${redact(firstLine).slice(0,160)}; full transcript withheld`);
  }
  const match = transcript.match(/### Result\s*([\s\S]*?)(?:\n###|$)/);
  if (!match) throw new Error("MCP business measurements unavailable; full transcript withheld");
  const measurements = JSON.parse(redact(match[1]));
  await mkdir(resolve(root, "data/mcp-validation"), { recursive: true });
  await writeFile(resolve(root, artifact), JSON.stringify(measurements, null, 2));
  console.log(JSON.stringify({ source: measurements.source, profile: measurements.profile, counts: measurements.counts, artifact }));
} finally {
  await client.close();
}
