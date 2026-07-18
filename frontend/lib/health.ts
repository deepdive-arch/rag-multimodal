import type { HealthStatus } from "@/types"

export interface HealthView {
  label: string
  tone: "success" | "warning" | "danger" | "pending"
}

export function deriveHealthView(healthStatus: HealthStatus | null, isRefreshing: boolean): HealthView {
  if (isRefreshing) return { label: "Verificando…", tone: "pending" }
  if (healthStatus?.status === "ok") return { label: "Online", tone: "success" }
  if (healthStatus?.status === "degraded") return { label: "Configuração incompleta", tone: "warning" }
  return { label: "Indisponível", tone: "danger" }
}
