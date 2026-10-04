import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { FileEntry } from "@/api/types";
import { WIDE_QUERY } from "@/hooks/useMediaQuery";
import { jsonResponse, mockFetch, mockMediaQueries, renderWithProviders } from "@/test/render";

import { FilesPanel } from "./FilesPanel";

const files: FileEntry[] = [
  { path: "uploads", name: "uploads", is_dir: true, size: 0, modified_at: "2026-01-01T00:00:00Z", mime: null },
  {
    path: "uploads/data.csv",
    name: "data.csv",
    is_dir: false,
    size: 20,
    modified_at: "2026-01-01T00:00:00Z",
    mime: "text/csv",
  },
  {
    path: "notes.md",
    name: "notes.md",
    is_dir: false,
    size: 12,
    modified_at: "2026-01-01T00:00:00Z",
    mime: "text/markdown",
  },
  {
    path: "site/index.html",
    name: "index.html",
    is_dir: false,
    size: 40,
    modified_at: "2026-01-01T00:00:00Z",
    mime: "text/html",
  },
];

function Harness() {
  const [selected, setSelected] = useState<string | null>(null);
  return <FilesPanel conversationId="c1" selectedPath={selected} onSelect={setSelected} onClose={() => undefined} />;
}

describe("FilesPanel", () => {
  it("lists the tree, previews files and deletes after confirmation", async () => {
    mockMediaQueries([WIDE_QUERY]);
    let current = files;
    const calls = mockFetch((url, init) => {
      if (url === "/api/conversations/c1/files" && (!init?.method || init.method === "GET"))
        return jsonResponse(current);
      if (url === "/api/conversations/c1/files/notes.md" && init?.method === "DELETE") {
        current = files.filter((file) => file.path !== "notes.md");
        return new Response(null, { status: 204 });
      }
      if (url === "/api/conversations/c1/files/notes.md")
        return new Response("# Title\n\nSome **notes**", { status: 200 });
      return undefined;
    });
    const user = userEvent.setup();
    renderWithProviders(<Harness />);

    const panel = await screen.findByTestId("files-panel");
    await waitFor(() => expect(screen.getAllByTestId("file-row")).toHaveLength(3));
    expect(within(panel).getByText("3 files")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Download all (.zip)" })).toHaveAttribute(
      "href",
      "/api/conversations/c1/files.zip",
    );

    // Markdown preview renders, with a source toggle.
    await user.click(within(panel).getByText("notes.md"));
    expect(await screen.findByRole("heading", { name: "Title" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Source" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Back to files" }));

    // HTML is previewed in a sandboxed iframe pointing at the file URL.
    await user.click(within(panel).getByText("index.html"));
    const frame = await screen.findByTitle("index.html");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-popups");
    expect(frame).toHaveAttribute("src", "/api/conversations/c1/files/site/index.html");
    await user.click(screen.getByRole("button", { name: "Back to files" }));

    // Delete with confirmation.
    await user.click(screen.getByRole("button", { name: "Delete notes.md" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.getAllByTestId("file-row")).toHaveLength(2));
    expect(calls.some((call) => call.init?.method === "DELETE" && call.url.endsWith("/notes.md"))).toBe(true);
  });
});
