import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("renders the primary library workspace", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Library" })).toBeInTheDocument();
    expect(screen.getByLabelText("Player")).toBeInTheDocument();
  });
});
