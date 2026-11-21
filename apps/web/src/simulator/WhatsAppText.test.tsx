import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WhatsAppText } from "./WhatsAppText";

function html(text: string): string {
  return render(<WhatsAppText text={text} />).container.innerHTML;
}

describe("WhatsAppText", () => {
  it("renders *bold* and _italic_ as formatting, not raw characters", () => {
    expect(html("1. *Tollgate Standard Driver* - Roadside assistance: _Yes_")).toBe(
      "1. <strong>Tollgate Standard Driver</strong> - Roadside assistance: <em>Yes</em>",
    );
  });

  it("supports italic inside bold", () => {
    expect(html("*an _estimate_ only*")).toBe("<strong>an <em>estimate</em> only</strong>");
  });

  it("leaves markers that are not WhatsApp formatting alone", () => {
    expect(html("sim_user_1 and snake_case_name")).toBe("sim_user_1 and snake_case_name");
    expect(html("2 * 3 * 4 and a lone * star")).toBe("2 * 3 * 4 and a lone * star");
    expect(html("* not bold *")).toBe("* not bold *");
    expect(html("*no\nbold*")).toBe("*no\nbold*");
  });

  it("never renders the text as HTML", () => {
    expect(html("*<img src=x onerror=alert(1)>*")).toBe("<strong>&lt;img src=x onerror=alert(1)&gt;</strong>");
  });
});
