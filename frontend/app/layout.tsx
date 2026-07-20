import type { Metadata } from "next"

import "./globals.css"

const title = "Agente RAG Multimodal | Teste a plataforma"

const description =
  "Envie documentos, imagens, áudios e vídeos e faça perguntas em linguagem natural com respostas fundamentadas e fontes verificáveis."

export const metadata: Metadata = {
  metadataBase: new URL(
    "https://rag-multimodal-rqg7.onrender.com",
  ),

  title,
  description,

  openGraph: {
    type: "website",
    locale: "pt_BR",
    url: "/",
    siteName: "Agente RAG Multimodal",
    title,
    description,
    images: [
      {
        url: "/og-rag-multimodal.png",
        width: 1200,
        height: 630,
        alt: "Interface do Agente RAG Multimodal para consulta inteligente de documentos e mídias",
      },
    ],
  },

  twitter: {
    card: "summary_large_image",
    title,
    description,
    images: ["/og-rag-multimodal.png"],
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="pt-BR" className="dark">
      <body>{children}</body>
    </html>
  )
}