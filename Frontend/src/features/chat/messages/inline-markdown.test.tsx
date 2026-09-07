import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderInlineMarkdown } from './inline-markdown'

function htmlOf(text: string): string {
  const { container } = render(<>{renderInlineMarkdown(text, 't')}</>)
  return container.innerHTML
}

describe('renderInlineMarkdown', () => {
  it('renders **bold** as strong', () => {
    expect(htmlOf('top harness is **Claude Code** today')).toContain(
      '<strong>Claude Code</strong>',
    )
  })

  it('renders *italic*, `code`, and ~~struck~~', () => {
    const html = htmlOf('*fast* `run()` ~~old~~')
    expect(html).toContain('<em>fast</em>')
    expect(html).toContain('<code>run()</code>')
    expect(html).toContain('<del>old</del>')
  })

  it('leaves unmatched markers as literal text', () => {
    expect(htmlOf('5*3 = 15 and **open')).toContain('5*3 = 15 and **open')
  })

  it('renders multi-word italics but not space-edged asterisks', () => {
    expect(htmlOf('*really quite fast*')).toContain('<em>really quite fast</em>')
    expect(htmlOf('a * b and c * d')).toContain('a * b and c * d')
  })

  it('leaves citation markers untouched for AnswerProse', () => {
    expect(htmlOf('raised $50M [1] and more [2]')).toContain('[1]')
  })

  it('handles empty input', () => {
    expect(htmlOf('')).toBe('')
  })
})
