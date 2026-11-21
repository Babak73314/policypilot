import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RichText } from "./RichText";

describe("RichText", () => {
  it("turns bullet lines into a list, even after an intro line", () => {
    render(<RichText text={"Here are estimates:\n- Plan A: $73.00\n- Plan B: $75.00\nWant to compare?"} />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["Plan A: $73.00", "Plan B: $75.00"]);
    expect(screen.getByText("Here are estimates:")).toBeInTheDocument();
    expect(screen.getByText("Want to compare?")).toBeInTheDocument();
  });

  it("renders **bold** and never interprets HTML", () => {
    const { container } = render(<RichText text={'**Important** <img src=x onerror="alert(1)">'} />);
    expect(screen.getByText("Important").tagName).toBe("STRONG");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img");
  });
});
