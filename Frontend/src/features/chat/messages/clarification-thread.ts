/**
 * Clarification thread helpers (specs/14 §4.3) — pure functions shared by the
 * clarification UI and its tests.
 */

/** Drop a trailing " - <previously picked option>" so chained clarification
 *  rounds send "base query - new answer", never "base - old pick - new pick".
 *  Free-typed answers (not matching any prior option) pass through untouched. */
export function stripPriorOptionAnswer(
  content: string,
  priorOptions: string[],
): string {
  for (const option of priorOptions) {
    const suffix = ` - ${option}`;
    if (option && content.endsWith(suffix)) {
      return content.slice(0, content.length - suffix.length);
    }
  }
  return content;
}
