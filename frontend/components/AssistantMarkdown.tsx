import ReactMarkdown from "react-markdown"
import type { Components } from "react-markdown"
import remarkGfm from "remark-gfm"

interface AssistantMarkdownProps {
  content: string
}

const BLOCKED_PROTOCOLS = new Set(["javascript:", "data:", "vbscript:"])
const SAFE_PROTOCOLS = new Set(["http:", "https:", "mailto:", "tel:", "irc:", "ircs:", "xmpp:"])
const URL_BASE = "https://rag-multimodal.local"

function safeUrlTransform(url: string): string {
  try {
    const parsed = new URL(url, URL_BASE)
    return BLOCKED_PROTOCOLS.has(parsed.protocol) || !SAFE_PROTOCOLS.has(parsed.protocol) && parsed.origin === URL_BASE ? "" : url
  } catch {
    return ""
  }
}

function isExternalLink(href: string): boolean {
  const parsed = new URL(href, URL_BASE)
  return parsed.origin !== URL_BASE || !["http:", "https:"].includes(parsed.protocol)
}

const components: Components = {
  h1: ({ children }) => <h1 className="assistant-markdown-h1">{children}</h1>,
  h2: ({ children }) => <h2 className="assistant-markdown-h2">{children}</h2>,
  h3: ({ children }) => <h3 className="assistant-markdown-h3">{children}</h3>,
  h4: ({ children }) => <h4 className="assistant-markdown-h4">{children}</h4>,
  p: ({ children }) => <p className="assistant-markdown-p">{children}</p>,
  strong: ({ children }) => <strong className="assistant-markdown-strong">{children}</strong>,
  em: ({ children }) => <em className="assistant-markdown-em">{children}</em>,
  ul: ({ children }) => <ul className="assistant-markdown-ul">{children}</ul>,
  ol: ({ children }) => <ol className="assistant-markdown-ol">{children}</ol>,
  li: ({ children }) => <li className="assistant-markdown-li">{children}</li>,
  blockquote: ({ children }) => <blockquote className="assistant-markdown-blockquote">{children}</blockquote>,
  a: ({ href, children }) => href ? <a href={href} target={isExternalLink(href) ? "_blank" : undefined} rel={isExternalLink(href) ? "noopener noreferrer" : undefined} className="assistant-markdown-link">{children}</a> : <span>{children}</span>,
  code: ({ children, className }) => <code className={className ? "assistant-markdown-code assistant-markdown-code-block" : "assistant-markdown-code"}>{children}</code>,
  pre: ({ children }) => <pre className="assistant-markdown-pre">{children}</pre>,
  table: ({ children }) => <div className="assistant-markdown-table-wrap"><table className="assistant-markdown-table">{children}</table></div>,
  thead: ({ children }) => <thead className="assistant-markdown-thead">{children}</thead>,
  tbody: ({ children }) => <tbody className="assistant-markdown-tbody">{children}</tbody>,
  tr: ({ children }) => <tr className="assistant-markdown-tr">{children}</tr>,
  th: ({ children }) => <th className="assistant-markdown-th">{children}</th>,
  td: ({ children }) => <td className="assistant-markdown-td">{children}</td>,
  hr: () => <hr className="assistant-markdown-hr" />,
  img: () => null,
}

export function AssistantMarkdown({ content }: AssistantMarkdownProps) {
  return <div className="assistant-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={safeUrlTransform} components={components}>{content}</ReactMarkdown></div>
}
