// Independent business cross-checks through the installed Playwright MCP.
// Tool transcripts containing login data are deliberately not printed/saved.
import { pathToFileURL } from "node:url";
import { homedir } from "node:os";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";

const root = resolve(import.meta.dirname, "..");
process.loadEnvFile(resolve(root, "backend/.env"));
const username = process.env.LOGIN_SAUCEDEMO;
const password = process.env.PASSWORD_SAUCEDEMO;
if (!username || !password) throw new Error("Application credentials missing");
const modules = process.env.AUTOJEV_MCP_MODULES || resolve(homedir(), ".local/share/autojev-playwright-mcp/node_modules");
const { Client } = await import(pathToFileURL(resolve(modules, "@modelcontextprotocol/sdk/dist/esm/client/index.js")));
const { StreamableHTTPClientTransport } = await import(pathToFileURL(resolve(modules, "@modelcontextprotocol/sdk/dist/esm/client/streamableHttp.js")));
const client = new Client({ name: "autojev-independent-check", version: "1.0.0" });
try {
  await client.connect(new StreamableHTTPClientTransport(new URL(process.env.AUTOJEV_MCP_URL || "http://localhost:8931/mcp")));
  const code = `async (page) => {
    const results = [];
    const check = (name, passed, actual) => { results.push({name, passed, actual}); if (!passed) throw new Error('MCP verification failed: ' + name); };
    await page.goto('https://www.saucedemo.com/');
    await page.locator('[data-test=username]').fill(${JSON.stringify(username)});
    await page.locator('[data-test=password]').fill(${JSON.stringify(password)});
    await page.locator('[data-test=login-button]').click();
    await page.waitForURL('**/inventory.html');
    await page.locator('.inventory_item').first().waitFor({state:'visible'});
    const products = await page.locator('.inventory_item').evaluateAll(cards=>cards.map(card=>({name:card.querySelector('.inventory_item_name').textContent,price:card.querySelector('.inventory_item_price').textContent})));
    check('six products', products.length === 6, products.length);
    await page.locator('[data-test=product-sort-container]').selectOption('lohi');
    const prices = await page.locator('.inventory_item_price').allTextContents();
    const numbers = prices.map(value=>Number(value.replace('$','')));
    check('price sorting', numbers.every((value,index)=>index===0||numbers[index-1]<=value), numbers);
    for (const product of products) {
      await page.getByText(product.name,{exact:true}).click();
      const detail = await page.locator('.inventory_details_name').innerText();
      const price = await page.locator('.inventory_details_price').innerText();
      check('detail '+product.name, detail===product.name&&price===product.price, {detail,price});
      await page.locator('[data-test=back-to-products]').click();
    }
    await page.locator('[data-test=add-to-cart-sauce-labs-backpack]').click();
    await page.locator('[data-test=add-to-cart-sauce-labs-bike-light]').click();
    check('cart badge', (await page.locator('.shopping_cart_badge').innerText())==='2', 2);
    await page.locator('.shopping_cart_link').click();
    await page.locator('.cart_list .inventory_item_name').first().waitFor({state:'visible'});
    const cartNames = await page.locator('.cart_list .inventory_item_name').allTextContents();
    check('cart composition', cartNames.includes('Sauce Labs Backpack')&&cartNames.includes('Sauce Labs Bike Light')&&cartNames.length===2, cartNames);
    await page.locator('[data-test=checkout]').click();
    const fields = ['firstName','lastName','postalCode'];
    for (const empty of fields) {
      for (const field of fields) await page.locator('[data-test='+field+']').fill(field===empty?'':field==='postalCode'?'11111':'AutoJev');
      await page.locator('[data-test=continue]').click();
      check('required '+empty, page.url().includes('checkout-step-one')&&await page.locator('[data-test=error]').isVisible(), await page.locator('[data-test=error]').innerText());
    }
    for (const field of fields) await page.locator('[data-test='+field+']').fill(field==='postalCode'?'11111':'AutoJev');
    await page.locator('[data-test=continue]').click();
    await page.waitForURL('**/checkout-step-two.html');
    const money = async selector => Number((await page.locator(selector).innerText()).match(/[0-9.]+/)[0]);
    const subtotal=await money('.summary_subtotal_label'), tax=await money('.summary_tax_label'), total=await money('.summary_total_label');
    check('subtotal', Math.abs(subtotal-39.98)<0.001, subtotal);
    check('total arithmetic', Math.abs(total-subtotal-tax)<0.011, {subtotal,tax,total});
    await page.locator('[data-test=finish]').click();
    await page.waitForURL('**/checkout-complete.html');
    check('order confirmation', /thank you/i.test(await page.locator('.complete-header').innerText()), await page.locator('.complete-header').innerText());
    await page.screenshot({path:${JSON.stringify(resolve(root, "data/mcp-validation/independent-checkout.png"))}});
    return {source:'Playwright MCP',passed:results.length,results};
  }`;
  const response = await client.callTool({name:"browser_run_code_unsafe",arguments:{code}}, undefined, {timeout:120000});
  const transcript = response.content.filter(item=>item.type==='text').map(item=>item.text).join("\n");
  if (response.isError) {
    const firstLine = (transcript.match(/### Error\s*([^\n]+)/) || [])[1] || "Tool failure";
    const safe = [username, password, JSON.stringify(username), JSON.stringify(password)].reduce((message, secret)=>message.replaceAll(secret,"[REDACTED]"), firstLine).slice(0,160);
    throw new Error(`Independent MCP checks did not complete: ${safe}; full transcript withheld`);
  }
  // Only the returned business measurements are safe to emit, never Ran code.
  const match = transcript.match(/### Result\s*([\s\S]*?)\n###/);
  if (!match) throw new Error("MCP result unavailable; transcript withheld");
  const measurements = JSON.parse(match[1]);
  await writeFile(resolve(root, "data/mcp-validation/independent-business-checks.json"), JSON.stringify(measurements,null,2));
  console.log(JSON.stringify({source:measurements.source,passed:measurements.passed,artifact:"data/mcp-validation/independent-business-checks.json"}));
} finally {
  await client.close();
}
