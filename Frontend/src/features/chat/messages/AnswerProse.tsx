/**
 * AnswerProse — the answer paragraph with citation superscripts.
 *
 * The model may cite its evidence as `[1]`, `[2]` … in `answer`. Those
 * markers render as small amber superscripts linking down to the matching
 * numbered card in `DataSources` (`#source-1` …). Markers pointing at no
 * known source render as plain text — never a dead link. Answers without
 * markers render as a plain paragraph.
 */
import { Fragment } from 'react';

const CITATION_PATTERN = '\\[(\\d+)\\]';

export function AnswerProse({
  answer,
  sourceCount,
}: {
  answer: string;
  sourceCount: number;
}) {
  if (answer.length === 0) return null;

  const parts: Array<{ key: string; index: number | null; text: string }> = [];
  let last = 0;
  let key = 0;
  const matches = answer.matchAll(new RegExp(CITATION_PATTERN, 'g'));
  for (const match of matches) {
    const start = match.index;
    if (start > last) {
      parts.push({ key: `t-${key++}`, index: null, text: answer.slice(last, start) });
    }
    parts.push({ key: `c-${key++}`, index: Number(match[1]), text: match[0] });
    last = start + match[0].length;
  }
  if (last < answer.length) {
    parts.push({ key: `t-${key++}`, index: null, text: answer.slice(last) });
  }
  if (parts.length === 0) {
    return <p className="message__answer-prose">{answer}</p>;
  }

  return (
    <p className="message__answer-prose">
      {parts.map((part) => {
        if (part.index === null) return <Fragment key={part.key}>{part.text}</Fragment>;
        const valid = part.index >= 1 && part.index <= sourceCount;
        if (!valid) return <Fragment key={part.key}>{part.text}</Fragment>;
        return (
          <a
            key={part.key}
            className="answer-cite"
            href={`#source-${part.index}`}
            aria-label={`Source ${part.index}`}
          >
            {part.index}
          </a>
        );
      })}
    </p>
  );
}
