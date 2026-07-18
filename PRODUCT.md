# Product

## Register

product

## Platform

web

## Users

The primary user is an analyst or researcher working with their own documents. They use the product while investigating a question, moving between a personal file library, a query, and the evidence that supports the answer. The experience should serve one focused person well; it does not assume a shared team workspace or an administrative operator.

## Product Purpose

RAG Multimodal lets a person consult a multimodal library of documents and verify answers against the retrieved evidence. The core job is finding specific information without manually searching every file. Success means the person receives an objective answer with relevant text, pages, or media to verify and no speculative completion beyond the indexed material.

## Positioning

“Consulte seus arquivos multimodais e veja exatamente quais evidências sustentam cada resposta.”

## Brand Personality

The product is technical, reliable, sober, modern, and evidence-oriented. It should feel like a quiet research station: competent and precise without becoming cold, intimidating, or overloaded. The tone is direct and grounded. It communicates what was found, what supports it, and when the available context is insufficient.

The intended composition is: NotebookLM’s source-and-conversation architecture, Perplexity’s direct answers and verifiable citations, and Linear’s visual discipline, density, keyboard quality, and progressive disclosure. These are reference points for the experience, not templates to copy.

## Anti-references

The interface must not look like a heavy corporate dashboard or a generic chatbot. It must also avoid:

- A cyberpunk AI laboratory: excess neon, futuristic grids, permanent glows, circuit motifs, animated brains/robots/particles, or blue-violet gradients applied to every control.
- Excessive glassmorphism: stacked transparent, blurred, or translucent cards that make reading and contrast unpredictable.
- “Card soup”: every heading, filter, message, source, and setting placed in an independent rounded card.
- A hacker terminal, IDE, or developer tool: monospace typography everywhere, exaggerated uppercase labels, terminal green, exposed JSON/commands, vector IDs, or model parameters in the primary flow.
- A debugging console or research lab: persistent Top K, vector scores, embedding dimensions, namespaces, latency, token counts, model names, storage keys, or internal logs.
- Black-on-black dark mode: indistinguishable surfaces, weak gray text, imperceptible borders, or selection/focus states that depend on subtle color changes.
- An administrative control panel: upload, files, filters, statistics, settings, destructive actions, and technical details competing for attention at the same time.
- A marketing page inside the application: oversized slogans, hero sections, decorative empty space, large illustrations, or commercial benefits interrupting the work.
- Attention-seeking animation: moving backgrounds, continuously animated gradients, pulsing nodes, blinking avatars, effect-only typewriter answers, or simultaneous loaders.
- False precision: presenting retrieval similarity as calibrated confidence, such as “96% de confiança”.
- A social or messaging product: infinite feeds, large avatars, prominent reactions, colorful bubbles for every message, dominant timestamps, or a WhatsApp/Discord/Slack-like hierarchy.

The consolidated anti-reference is a futuristic laboratory, hacker terminal, administrative panel, translucent card collection, landing page, or messaging app. The product should read as a modern, quiet, trustworthy documentary research station where files, answers, and evidence have an unmistakable hierarchy.

## Design Principles

1. **Evidence before assertion.** The answer is valuable because the person can inspect the source behind it. Retrieval, citations, page/media context, and insufficient-context states are part of the product’s promise.
2. **Research workspace over chatbot.** Keep the file library and conversation clearly related but distinct. The conversation is the query mechanism; the answer and its evidence are the product content.
3. **Hierarchy before density.** Show files, active filters, the conversation, and the current answer first. Keep retrieval modes, source counts, and destructive actions behind progressive disclosure.
4. **Technical confidence without developer burden.** The product may be technically sophisticated under the hood, but the primary surface should speak in terms of files, questions, answers, and evidence rather than models, vectors, namespaces, or logs.
5. **Quiet, verifiable interaction.** Use direct language, stable opaque surfaces, short functional state changes, and predictable keyboard behavior so attention stays on investigation.

## Accessibility & Inclusion

Target WCAG 2.2 AA. Support complete keyboard operation, visible focus, readable contrast, zoom and reflow, screen readers, reduced motion, and states that do not rely on color alone. Focus, hover, selection, loading, error, warning, success, and disabled states must have non-color cues where needed. The responsive web layout must remain usable at narrow mobile widths and large zoom levels without hiding the active question, answer, or evidence.
