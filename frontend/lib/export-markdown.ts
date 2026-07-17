import type { Message } from "@/types"

export function exportMarkdown(question: Message, answer: Message): void {
  const sources = (answer.sources ?? []).map((source, index) => `${index + 1}. ${source.file_name}${source.page_number ? ` — página ${source.page_number}` : ""}`).join("\n")
  const markdown = `# Pergunta\n\n${question.content}\n\n# Resposta\n\n${answer.content}\n\n# Fontes\n\n${sources || "Nenhuma fonte."}\n`
  const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }))
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = "resposta-rag.md"
  anchor.click()
  URL.revokeObjectURL(url)
}
