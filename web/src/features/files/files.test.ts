import { describe, expect, it } from "vitest";

import type { FileEntry } from "@/api/types";

import { codeLanguage, previewKind } from "./fileTypes";
import { buildFileTree, treeSize } from "./tree";

function file(path: string, size = 10, isDir = false): FileEntry {
  return {
    path,
    name: path.split("/").pop() as string,
    is_dir: isDir,
    size,
    modified_at: "2026-01-01T00:00:00Z",
    mime: null,
  };
}

describe("buildFileTree", () => {
  it("nests entries, sorts folders first and synthesizes missing parents", () => {
    const tree = buildFileTree([
      file("b.txt"),
      file("uploads", 0, true),
      file("uploads/photo.png", 100),
      file("out/chart/plot.svg", 5),
      file("a.md"),
    ]);
    expect(tree.map((node) => node.entry.path)).toEqual(["out", "uploads", "a.md", "b.txt"]);
    const out = tree[0];
    expect(out.entry.is_dir).toBe(true);
    expect(out.children[0].entry.path).toBe("out/chart");
    expect(out.children[0].children[0].entry.name).toBe("plot.svg");
    expect(treeSize(tree[1])).toBe(100);
  });
});

describe("previewKind", () => {
  it("classifies by extension and mime", () => {
    expect(previewKind("index.html", null)).toBe("html");
    expect(previewKind("logo.svg", null)).toBe("svg");
    expect(previewKind("photo.JPG", null)).toBe("image");
    expect(previewKind("report.md", null)).toBe("markdown");
    expect(previewKind("doc.pdf", null)).toBe("pdf");
    expect(previewKind("main.py", null)).toBe("code");
    expect(previewKind("data.csv", null)).toBe("text");
    expect(previewKind("song.mp3", null)).toBe("audio");
    expect(previewKind("clip.webm", null)).toBe("video");
    expect(previewKind("archive.zip", null)).toBe("none");
    expect(previewKind("blob", "text/plain")).toBe("text");
    expect(codeLanguage("Dockerfile")).toBe("dockerfile");
    expect(codeLanguage("x.unknown")).toBe("plaintext");
  });
});
