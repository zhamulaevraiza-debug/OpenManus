import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/render";

import { AskHumanCard } from "./AskHumanCard";

describe("AskHumanCard", () => {
  it("shows the question and submits the trimmed answer", async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn().mockResolvedValue(true);
    renderWithProviders(
      <AskHumanCard question={{ question_id: "q1", question: "Which **city**?" }} onAnswer={onAnswer} />,
    );
    expect(screen.getByText("The agent needs your input")).toBeInTheDocument();
    const send = screen.getByTestId("ask-human-send");
    expect(send).toBeDisabled();
    await user.type(screen.getByTestId("ask-human-input"), "  Paris  ");
    await user.click(send);
    expect(onAnswer).toHaveBeenCalledWith("Paris");
    await waitFor(() => expect(screen.getByTestId("ask-human-input")).toHaveValue(""));
  });

  it("keeps the text when sending fails", async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn().mockResolvedValue(false);
    renderWithProviders(<AskHumanCard question={{ question_id: "q1", question: "Why?" }} onAnswer={onAnswer} />);
    await user.type(screen.getByTestId("ask-human-input"), "Because{Enter}");
    expect(onAnswer).toHaveBeenCalledWith("Because");
    expect(screen.getByTestId("ask-human-input")).toHaveValue("Because");
  });
});
