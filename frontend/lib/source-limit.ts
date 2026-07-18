export function sourceLimitLabel(topK: number): string {
  return topK === 1 ? "Até 1 fonte" : `Até ${topK} fontes`
}
