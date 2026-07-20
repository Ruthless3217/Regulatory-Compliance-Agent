import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ChatPage from "@/app/(workspace)/submissions/[id]/chat/page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "sub-1" }),
}));

describe("Chat screen", () => {
  it("streams the assistant's reply after sending a message", async () => {
    render(<ChatPage />);

    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "Why was this flagged?" } });
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    // The user's message renders immediately.
    expect(await screen.findByText("Why was this flagged?")).toBeInTheDocument();

    // simulateChat streams tokens (~60ms apart); wait for one to land, then
    // for the full sentence once streaming completes.
    expect(await screen.findByText(/Based/)).toBeInTheDocument();
    expect(await screen.findByText(/IRDAI precedent/i)).toBeInTheDocument();
  });
});
