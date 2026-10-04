import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { jsonResponse, mockFetch, renderWithProviders } from "@/test/render";

import { LoginPage } from "./LoginPage";

const user = { id: "u1", username: "alice", is_admin: false, disabled: false, created_at: "2026-01-01T00:00:00Z" };

function setup(loginStatus: number, allowRegistration = false) {
  const calls = mockFetch((url, init) => {
    if (url === "/api/auth/me") return jsonResponse({ detail: "Not authenticated" }, 401);
    if (url === "/api/auth/config") return jsonResponse({ allow_registration: allowRegistration });
    if (url === "/api/auth/login" && init?.method === "POST") {
      return loginStatus === 200 ? jsonResponse(user) : jsonResponse({ detail: "Invalid credentials" }, loginStatus);
    }
    return undefined;
  });
  renderWithProviders(
    <Routes>
      <Route path="/login" element={<LoginPage mode="login" />} />
      <Route path="/" element={<p>Home page</p>} />
    </Routes>,
    { route: "/login" },
  );
  return calls;
}

describe("LoginPage", () => {
  it("signs in and navigates home", async () => {
    const actor = userEvent.setup();
    const calls = setup(200);
    await actor.type(screen.getByTestId("login-username"), " alice ");
    await actor.type(screen.getByTestId("login-password"), "secret123");
    await actor.click(screen.getByTestId("login-submit"));
    expect(await screen.findByText("Home page")).toBeInTheDocument();
    const login = calls.find((call) => call.url === "/api/auth/login");
    expect(JSON.parse(String(login?.init?.body))).toEqual({ username: "alice", password: "secret123" });
  });

  it("shows a friendly error on wrong credentials", async () => {
    const actor = userEvent.setup();
    setup(401);
    await actor.type(screen.getByTestId("login-username"), "alice");
    await actor.type(screen.getByTestId("login-password"), "wrong");
    await actor.click(screen.getByTestId("login-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect username or password.");
  });

  it("explains throttling", async () => {
    const actor = userEvent.setup();
    setup(429);
    await actor.type(screen.getByTestId("login-username"), "alice");
    await actor.type(screen.getByTestId("login-password"), "wrong");
    await actor.click(screen.getByTestId("login-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts");
  });

  it("offers registration only when enabled", async () => {
    setup(200, true);
    await waitFor(() => expect(screen.getByRole("link", { name: "Create account" })).toBeInTheDocument());
  });
});
