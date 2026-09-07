/**
 * Inline markdown for model-written copy (answers, clarification questions).
 *
 * The model emits lightweight markers (`**bold**`, `*italic*`, `` `code` ``,
 * `~~struck~~`) that must render formatted instead of literally. This is
 * deliberately inline-only (no lists/blocks): it composes with AnswerProse's
 * citation splitting, which needs plain string parts to find `[n]` markers.
 * Unmatched markers pass through as literal text.
 */
import { Fragment } from 'react';
import type { ReactNode } from 'react';

const INLINE_PATTERN =
  /(`[^`\n]+`|\*\*[^*\n]+\*\*|~~[^~\n]+~~|\*([^* \n][^*\n]*[^* \n]|[^* \n])\*)/g;

function renderToken(token: string, key: string): ReactNode {
  if (token.startsWith('`') && token.endsWith('`') && token.length >= 2) {
    return <code key={key}>{token.slice(1, -1)}</code>;
  }
  if (token.startsWith('**') && token.endsWith('**') && token.length >= 5) {
    return <strong key={key}>{token.slice(2, -2)}</strong>;
  }
  if (token.startsWith('~~') && token.endsWith('~~') && token.length >= 5) {
    return <del key={key}>{token.slice(2, -2)}</del>;
  }
  if (token.startsWith('*') && token.endsWith('*') && token.length >= 3) {
    return <em key={key}>{token.slice(1, -1)}</em>;
  }
  return <Fragment key={key}>{token}</Fragment>;
}

export function renderInlineMarkdown(text: string, keyPrefix: string): ReactNode {
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const start = match.index ?? 0;
    if (start > last) {
      nodes.push(
        <Fragment key={`${keyPrefix}-${key++}`}>
          {text.slice(last, start)}
        </Fragment>,
      );
    }
    nodes.push(renderToken(match[0], `${keyPrefix}-${key++}`));
    last = start + match[0].length;
  }
  if (last < text.length) {
    nodes.push(
      <Fragment key={`${keyPrefix}-${key++}`}>{text.slice(last)}</Fragment>,
    );
  }
  return nodes;
}
