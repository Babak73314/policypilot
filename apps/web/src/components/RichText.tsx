import { Fragment, type ReactNode } from "react";

const BULLET = /^\s*[-•*]\s+/;

/**
 * Renders assistant text: blank lines separate blocks, consecutive lines starting with "- ", "• "
 * or "* " form a bullet list, other lines are paragraphs with line breaks, and **bold** is
 * honoured. Everything is rendered as React text, never as HTML.
 */
export function RichText({ text }: { text: string }) {
  const blocks = text.trim().split(/\n{2,}/);
  return <div className="space-y-2">{blocks.flatMap((block, index) => renderBlock(block, index))}</div>;
}

function renderBlock(block: string, blockIndex: number): ReactNode[] {
  const nodes: ReactNode[] = [];
  let paragraph: string[] = [];
  let bullets: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    const lines = paragraph;
    nodes.push(
      <p key={`${blockIndex}-p${nodes.length}`}>
        {lines.map((line, i) => (
          <Fragment key={i}>
            {i > 0 && <br />}
            <Inline text={line} />
          </Fragment>
        ))}
      </p>,
    );
    paragraph = [];
  };

  const flushBullets = () => {
    if (bullets.length === 0) return;
    const items = bullets;
    nodes.push(
      <ul key={`${blockIndex}-ul${nodes.length}`} className="list-disc space-y-1 pl-5">
        {items.map((item, i) => (
          <li key={i}>
            <Inline text={item} />
          </li>
        ))}
      </ul>,
    );
    bullets = [];
  };

  for (const line of block.split("\n")) {
    if (BULLET.test(line)) {
      flushParagraph();
      bullets.push(line.replace(BULLET, ""));
    } else {
      flushBullets();
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushBullets();
  return nodes;
}

function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
          <strong key={i}>{part.slice(2, -2)}</strong>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}
