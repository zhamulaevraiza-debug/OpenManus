import { describe, expect, it } from "vitest";

import { codeArgument, summarizeArguments } from "./tools";

describe("summarizeArguments", () => {
  it("summarizes well-known tools", () => {
    expect(summarizeArguments("web_search", { query: "e-bikes 2026" })).toBe("e-bikes 2026");
    expect(summarizeArguments("browser_use", { action: "go_to_url", url: "https://example.com" })).toBe(
      "go_to_url · https://example.com",
    );
    expect(summarizeArguments("str_replace_editor", { command: "create", path: "/w/app.py", file_text: "…" })).toBe(
      "create · /w/app.py",
    );
    expect(summarizeArguments("python_execute", { code: "# setup\nimport pandas as pd\nprint(1)" })).toBe(
      "import pandas as pd",
    );
    expect(summarizeArguments("bash", { command: "ls -la" })).toBe("ls -la");
  });

  it("falls back to the first textual value or compact JSON and clips long text", () => {
    expect(summarizeArguments("mcp_weather", { city: "Oslo", days: 3 })).toBe("Oslo");
    expect(summarizeArguments("custom", { nested: { a: 1 } })).toBe('{"nested":{"a":1}}');
    expect(summarizeArguments("custom", {})).toBe("");
    expect(summarizeArguments("web_search", { query: "x".repeat(300) })).toHaveLength(140);
  });
});

describe("codeArgument", () => {
  it("extracts code for code-running tools", () => {
    expect(codeArgument("python_execute", { code: "print(1)" })).toEqual({ code: "print(1)", language: "python" });
    expect(codeArgument("bash", { command: "ls" })).toEqual({ code: "ls", language: "bash" });
    expect(codeArgument("web_search", { query: "q" })).toBeNull();
  });
});
