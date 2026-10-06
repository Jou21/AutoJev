// A small client for the official Microsoft Playwright MCP server.
// No browser automation is performed outside the MCP protocol here.
import { pathToFileURL } from "node:url";
import { homedir } from "node:os";
import { resolve } from "node:path";
import { createInterface } from "node:readline";

const modules = process.env.AUTOJEV_MCP_MODULES || resolve(homedir(), ".local/share/autojev-playwright-mcp/node_modules");
const { Client } = await import(pathToFileURL(resolve(modules, "@modelcontextprotocol/sdk/dist/esm/client/index.js")));
const { StreamableHTTPClientTransport } = await import(pathToFileURL(resolve(modules, "@modelcontextprotocol/sdk/dist/esm/client/streamableHttp.js")));
const client = new Client({ name: "autojev-validation", version: "1.0.0" });
const transport = new StreamableHTTPClientTransport(new URL(process.env.AUTOJEV_MCP_URL || "http://localhost:8931/mcp"));
try {
  await client.connect(transport);
  const name = process.argv[2] || "list";
  const run = async (name, args) => {
    const result = await client.callTool({ name, arguments: args });
    for (const part of result.content || []) {
      if (part.type === "text") console.log(part.text);
      else if (part.type === "image") console.log(`[image ${part.mimeType}; use the screenshot file for visual inspection]`);
    }
    if (result.isError) console.log("MCP_TOOL_ERROR");
    console.log("MCP_TOOL_COMPLETE");
  };
  if (name === "list") {
    const result = await client.listTools();
    console.log(JSON.stringify(result.tools.map(({ name, inputSchema }) => ({ name, inputSchema })), null, 2));
  } else if (name === "session") {
    console.log("MCP_SESSION_READY");
    const lines = createInterface({ input: process.stdin, terminal: false });
    for await (const line of lines) {
      if (line === "exit") break;
      try {
        const { name, arguments: args = {} } = JSON.parse(line);
        await run(name, args);
      } catch (error) { console.log(`MCP_ERROR: ${error.message}`); }
    }
  } else {
    const args = JSON.parse(process.argv[3] || "{}");
    await run(name, args);
  }
} finally {
  await client.close();
}
