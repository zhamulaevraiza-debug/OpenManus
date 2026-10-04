import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AgentInfo } from "@/api/types";
import { renderWithProviders } from "@/test/render";

import { ModePicker } from "./ModePicker";

const agents: AgentInfo[] = [
  {
    key: "coder",
    name: "Coder",
    description: "Writes code",
    icon: "code",
    available: true,
    reason: null,
    team_member: true,
  },
  {
    key: "sandbox",
    name: "Sandbox",
    description: "Cloud sandbox",
    icon: "box",
    available: false,
    reason: "Daytona API key not configured",
    team_member: false,
  },
];

describe("ModePicker", () => {
  it("lists modes and agents, disables unavailable agents with the reason and selects a mode", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(
      <ModePicker
        value="auto"
        onChange={onChange}
        modes={[{ key: "auto" }, { key: "chat" }, { key: "team" }]}
        agents={agents}
      />,
    );
    const trigger = screen.getByTestId("mode-picker");
    expect(trigger).toHaveAccessibleName("Mode: Auto");
    await user.click(trigger);
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(screen.getByTestId("mode-option-auto")).toHaveAttribute("aria-selected", "true");
    const sandbox = screen.getByTestId("mode-option-sandbox");
    expect(sandbox).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Daytona API key not configured")).toBeInTheDocument();
    await user.click(sandbox);
    expect(onChange).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("mode-option-coder"));
    expect(onChange).toHaveBeenCalledWith("coder");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
