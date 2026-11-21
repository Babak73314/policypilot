import { Fragment, type ReactNode } from "react";

/**
 * WhatsApp's inline formatting, as the app shows it: `*bold*` and `_italic_`. A marker pair
 * counts only at word boundaries, on one line, around text that does not start or end with a
 * space, so "snake_case_name" and "2 * 3 * 4" stay as they are. Everything is rendered as React
 * text, never as HTML.
 */
const MARKS: { char: "*" | "_"; wrap: (children: ReactNode, key: string) => ReactNode }[] = [
  { char: "*", wrap: (children, key) => <strong key={key}>{children}</strong> },
  { char: "_", wrap: (children, key) => <em key={key}>{children}</em> },
];

function pattern(char: string): RegExp {
  const c = char === "*" ? "\\*" : char; // "\_" is an invalid escape in unicode mode
  // (start or a non-word, non-marker character) marker (non-space ... non-space) marker
  // (end or a non-word, non-marker character)
  return new RegExp(
    `(^|[^\\p{L}\\p{N}${c}])${c}([^\\s${c}](?:[^${c}\\n]*[^\\s${c}])?)${c}(?=$|[^\\p{L}\\p{N}${c}])`,
    "gu",
  );
}

function format(text: string, level: number, keyPrefix: string): ReactNode[] {
  const mark = MARKS[level];
  if (!mark) return [text];
  const { char, wrap } = mark;
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(pattern(char))) {
    const lead = match[1] ?? "";
    const inner = match[2] ?? "";
    const start = match.index + lead.length;
    nodes.push(...format(text.slice(last, start), level + 1, `${keyPrefix}${level}-${last}-`));
    const key = `${keyPrefix}${level}-${start}`;
    nodes.push(wrap(format(inner, level + 1, `${key}-`), key));
    last = start + inner.length + 2;
  }
  nodes.push(...format(text.slice(last), level + 1, `${keyPrefix}${level}-${last}-`));
  return nodes.filter((node) => node !== "");
}

export function WhatsAppText({ text }: { text: string }) {
  return (
    <>
      {format(text, 0, "").map((node, index) => (
        <Fragment key={index}>{node}</Fragment>
      ))}
    </>
  );
}
