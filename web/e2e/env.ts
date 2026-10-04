/**
 * End-to-end test environment, provided by `scripts/e2e.py` (which starts the fake LLM and the
 * web server). Defaults allow running `npx playwright test` against a manually started stack.
 */
import os from "node:os";
import path from "node:path";

export const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:8000";
/** Base URL of the fake OpenAI-compatible model (null when the tests run against a real model). */
export const FAKE_LLM_URL = process.env.E2E_FAKE_LLM_URL ?? null;

export const ADMIN = {
  username: process.env.E2E_ADMIN_USERNAME ?? "admin",
  password: process.env.E2E_ADMIN_PASSWORD ?? "",
};

/** The model name and API key configured in `config.toml` of the test server. */
export const LLM_MODEL = process.env.E2E_LLM_MODEL ?? "fake-gpt";
export const LLM_API_KEY = process.env.E2E_LLM_API_KEY ?? "";

export const OUTPUT_DIR = process.env.E2E_OUTPUT_DIR ?? path.join(os.tmpdir(), "openmanus-e2e");
/** Signed-in administrator session shared by the test projects. */
export const STORAGE_STATE = path.join(OUTPUT_DIR, "auth", "admin.json");
/** Where the documentation screenshots are written (`scripts/e2e.py --screenshots`). */
export const SCREENSHOTS_DIR = process.env.E2E_SCREENSHOTS_DIR ?? null;
