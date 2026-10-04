import { describe, expect, it } from "vitest";

import { en } from "./en";
import { translate, type Dictionary } from "./index";
import { ru } from "./ru";

function flatten(node: unknown, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();

describe("translations", () => {
  const flatEn = flatten(en);
  const flatRu = flatten(ru);

  it("ru and en have exactly the same keys", () => {
    expect(Object.keys(flatRu).sort()).toEqual(Object.keys(flatEn).sort());
  });

  it("no translation is empty", () => {
    for (const [key, value] of [...Object.entries(flatEn), ...Object.entries(flatRu)]) {
      expect(value.trim(), key).not.toBe("");
    }
  });

  it("placeholders match between languages", () => {
    for (const key of Object.keys(flatEn)) {
      expect(placeholders(flatRu[key]), key).toEqual(placeholders(flatEn[key]));
    }
  });

  it("Russian strings are actually translated (contain Cyrillic) unless they are names/codes", () => {
    // Product and technology names stay as they are.
    const names =
      /^(common\.appName|agents\.manus\.name|tools\.python_execute|settings\.model\.apiTypes\.|settings\.mcp\.(sse|url))/;
    const untranslatable = new Set(Object.keys(flatRu).filter((key) => names.test(key)));
    const english = Object.entries(flatRu).filter(
      ([key, value]) => !untranslatable.has(key) && !/[а-яё]/i.test(value) && /[a-z]{3,}/i.test(value),
    );
    expect(english.map(([key]) => key)).toEqual([]);
  });
});

describe("translate", () => {
  it("interpolates variables and leaves unknown placeholders", () => {
    expect(translate(en as Dictionary, "en", "chat.uploading", { percent: 42 })).toBe("Uploading 42%");
    expect(translate(en as Dictionary, "en", "chat.uploading")).toBe("Uploading {percent}%");
  });

  it("selects Russian plural forms", () => {
    const forms = [1, 2, 5, 11, 21, 22].map((count) => translate(ru, "ru", "files.count", { count }));
    expect(forms).toEqual(["1 файл", "2 файла", "5 файлов", "11 файлов", "21 файл", "22 файла"]);
  });

  it("selects English plural forms", () => {
    expect(translate(en as Dictionary, "en", "files.count", { count: 1 })).toBe("1 file");
    expect(translate(en as Dictionary, "en", "files.count", { count: 3 })).toBe("3 files");
  });

  it("falls back to the key for unknown paths", () => {
    expect(translate(en as Dictionary, "en", "nope.missing")).toBe("nope.missing");
  });
});
