import type { ReactNode } from "react";

/**
 * Renders the small Markdown subset the terms of use are written in
 * (NFR-47): headings, paragraphs, bulleted and numbered lists, `**bold**`,
 * `*italic*`, `` `code` `` and `[text](https://...)` links. Anything else
 * renders as the literal characters it is written with.
 *
 * It builds React elements and never sets inner HTML, so raw HTML in the text
 * appears as text and cannot run. A link is made only for `http:`, `https:`
 * and `mailto:` targets; any other target is shown as plain text, so a
 * `javascript:` link is not clickable.
 *
 * `headingOffset` pushes every heading down so the page keeps its one `h1`:
 * at the default of 1, a `#` heading becomes an `h2`.
 */

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] };

const HEADING = /^(#{1,6})\s+(.*\S)\s*$/;
const BULLET = /^[-*]\s+(.*)$/;
const NUMBERED = /^\d+[.)]\s+(.*)$/;

function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    if (paragraph.length > 0) {
      blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list !== null) {
      blocks.push({ kind: "list", ...list });
      list = null;
    }
  };

  for (const rawLine of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = rawLine.trim();
    if (line === "") {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading !== null) {
      flushParagraph();
      flushList();
      blocks.push({ kind: "heading", level: heading[1]!.length, text: heading[2]! });
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = bullet === null ? NUMBERED.exec(line) : null;
    const item = bullet ?? numbered;
    if (item !== null) {
      flushParagraph();
      const ordered = numbered !== null;
      if (list !== null && list.ordered !== ordered) {
        flushList();
      }
      list ??= { ordered, items: [] };
      list.items.push(item[1]!);
      continue;
    }
    // A wrapped line after a list item continues that item, not a new block.
    if (list !== null) {
      list.items[list.items.length - 1] += ` ${line}`;
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  flushList();
  return blocks;
}

const INLINE = /\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;
const SAFE_LINK = /^(?:https?:|mailto:)/i;

function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const start = match.index;
    if (start > last) {
      nodes.push(text.slice(last, start));
    }
    const [whole, bold, italic, code, label, target] = match;
    if (bold !== undefined) {
      nodes.push(<strong key={start}>{bold}</strong>);
    } else if (italic !== undefined) {
      nodes.push(<em key={start}>{italic}</em>);
    } else if (code !== undefined) {
      nodes.push(
        <code key={start} className="font-mono">
          {code}
        </code>,
      );
    } else if (label !== undefined && target !== undefined && SAFE_LINK.test(target)) {
      nodes.push(
        <a
          key={start}
          href={target}
          rel="noopener noreferrer"
          className="text-[var(--color-accent)] underline underline-offset-2 hover:text-[var(--color-accent-hover)]"
        >
          {label}
        </a>,
      );
    } else {
      nodes.push(whole);
    }
    last = start + whole.length;
  }
  if (last < text.length) {
    nodes.push(text.slice(last));
  }
  return nodes;
}

const HEADING_CLASS: Record<number, string> = {
  2: "m-0 text-2xl text-[var(--color-text)]",
  3: "m-0 text-xl text-[var(--color-text)]",
  4: "m-0 text-lg text-[var(--color-text)]",
  5: "m-0 text-base font-semibold text-[var(--color-text)]",
  6: "m-0 text-base font-semibold text-[var(--color-text)]",
};

export function Markdown({
  text,
  headingOffset = 1,
}: {
  text: string;
  headingOffset?: number;
}) {
  return (
    <div className="flex flex-col gap-3">
      {parseBlocks(text).map((block, index) => {
        if (block.kind === "heading") {
          const level = Math.min(6, Math.max(2, block.level + headingOffset));
          const Tag = `h${level}` as "h2" | "h3" | "h4" | "h5" | "h6";
          return (
            <Tag key={index} className={HEADING_CLASS[level]}>
              {renderInline(block.text)}
            </Tag>
          );
        }
        if (block.kind === "list") {
          const Tag = block.ordered ? "ol" : "ul";
          return (
            <Tag key={index} className="m-0 flex flex-col gap-1 pl-6">
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>{renderInline(item)}</li>
              ))}
            </Tag>
          );
        }
        return (
          <p key={index} className="m-0">
            {renderInline(block.text)}
          </p>
        );
      })}
    </div>
  );
}
