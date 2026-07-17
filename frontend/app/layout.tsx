import type { Metadata } from "next"
import { IBM_Plex_Mono, Manrope } from "next/font/google"
import "./globals.css"

const manrope = Manrope({ subsets: ["latin"], variable: "--font-display" })
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: "400", variable: "--font-mono" })

export const metadata: Metadata = { title: "RAG Multimodal", description: "Consulte documentos e arquivos multimodais com fontes verificáveis." }

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR" className={`${manrope.variable} ${mono.variable} dark`}><body>{children}</body></html>
}
