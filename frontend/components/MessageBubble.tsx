import { Bot, User } from "lucide-react"
import { AssistantMarkdown } from "@/components/AssistantMarkdown"
import { ResponseActions } from "@/components/ResponseActions"
import { SourcesPanel } from "@/components/SourcesPanel"
import type { Message } from "@/types"

interface MessageBubbleProps { message: Message; question?: Message; onFeedback: (useful: boolean) => Promise<void> }

const timeFormatter = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" })

export function MessageBubble({ message, question, onFeedback }: MessageBubbleProps) {
  const isUser = message.role === "user"
  return (
    <article className={`message-row ${isUser ? "user" : "assistant"}`} aria-label={isUser ? "Pergunta do usuário" : "Resposta do assistente"}>
      <div className="message-inner">
        <div className="message-avatar" aria-hidden="true">{isUser ? <User size={16} /> : <Bot size={16} />}</div>
        <div className="message-body">
          {!isUser && <div className="assistant-identity"><span className="assistant-label">Assistente</span><span>· resposta fundamentada</span></div>}
          <div className={`message-content ${isUser ? "user" : "assistant"}`}>
            {isUser ? <p className="m-0 whitespace-pre-wrap">{message.content}</p> : <AssistantMarkdown content={message.content} />}
          </div>
          <p className="message-timestamp">{timeFormatter.format(new Date(message.created_at))}</p>
          {!isUser && message.sources && message.sources.length > 0 && <SourcesPanel sources={message.sources} />}
          {!isUser && question && <ResponseActions question={question} answer={message} onFeedback={onFeedback} />}
        </div>
      </div>
    </article>
  )
}
