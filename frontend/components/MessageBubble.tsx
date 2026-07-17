import { Bot, User } from "lucide-react"
import { ResponseActions } from "@/components/ResponseActions"
import { SourcesPanel } from "@/components/SourcesPanel"
import type { Message } from "@/types"

interface MessageBubbleProps { message: Message; question?: Message; onFeedback: (useful: boolean) => Promise<void> }

export function MessageBubble({ message, question, onFeedback }: MessageBubbleProps) {
  const isUser = message.role === "user"
  return <article className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}><div className={`flex max-w-[min(760px,92%)] gap-3 ${isUser ? "flex-row-reverse" : ""}`}><div className={`flex size-8 shrink-0 items-center justify-center rounded-xl ${isUser ? "bg-[#2e6d77] text-[#d9f3ec]" : "bg-[#2a332f] text-[#e8b16b]"}`}>{isUser ? <User size={15} /> : <Bot size={15} />}</div><div className={`${isUser ? "rounded-2xl rounded-tr-sm bg-[#255b67] text-[#eef8f4]" : "rounded-2xl rounded-tl-sm border border-[#2b3637] bg-[#171d1e] text-[#dfe7e1]"} px-4 py-3`}><p className="whitespace-pre-wrap text-sm leading-6">{message.content}</p><p className={`mt-2 font-mono text-[9px] ${isUser ? "text-[#b4e0db]" : "text-[#778581]"}`}>{new Date(message.created_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}</p>{!isUser && message.sources && <SourcesPanel sources={message.sources} />}{!isUser && question && <ResponseActions question={question} answer={message} onFeedback={onFeedback} />}</div></div></article>
}
