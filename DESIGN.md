---
name: RAG Multimodal
description: Evidence-grounded multimodal retrieval in a focused dark workbench.
colors:
  background: "#080b12"
  surface: "#0e151f"
  surface-raised: "#151f2c"
  surface-strong: "#1a2635"
  surface-muted: "#111a25"
  surface-code: "#0a111a"
  border: "#94a3b826"
  border-strong: "#94a3b847"
  text-primary: "#f1f5fb"
  text-secondary: "#a8b5c7"
  text-muted: "#7d8da4"
  primary: "#6674ff"
  primary-hover: "#7c88ff"
  accent: "#35d2d0"
  accent-contrast: "#b8fff7"
  violet: "#986cff"
  success: "#6ad6a6"
  warning: "#f1c477"
  danger: "#ff8585"
  on-accent: "#061013"
  white: "#ffffff"
typography:
  display:
    fontFamily: "Manrope, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(1.65rem, 4vw, 2.45rem)"
    fontWeight: 780
    lineHeight: 1.2
    letterSpacing: "-0.045em"
  headline:
    fontFamily: "Manrope, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(1.18rem, 1.7vw, 1.5rem)"
    fontWeight: 750
    lineHeight: 1.3
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Manrope, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 750
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Manrope, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.96875rem"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "0.7rem"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "0.13em"
rounded:
  sm: "0.35rem"
  md: "0.6rem"
  lg: "0.8rem"
  xl: "1rem"
  2xl: "1.25rem"
  pill: "999px"
spacing:
  xs: "0.25rem"
  sm: "0.5rem"
  md: "0.8rem"
  lg: "1rem"
  xl: "1.5rem"
  2xl: "2rem"
  3xl: "3rem"
  content: "4.5rem"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.white}"
    rounded: "{rounded.lg}"
    padding: "0 0.9rem"
    height: "2.85rem"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.md}"
    padding: "0.4rem 0.6rem"
    height: "2.75rem"
  input-composer:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.xl}"
    padding: "0.55rem 0.6rem 0.55rem 0.8rem"
    height: "2.8rem"
  navigation-sidebar:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-primary}"
    rounded: "0"
    padding: "1.25rem 1.15rem"
  chip-status:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.pill}"
    padding: "0.38rem 0.7rem"
  card-file:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.lg}"
    padding: "0.42rem"
  card-evidence:
    backgroundColor: "{colors.surface-strong}"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.md}"
    padding: "0.8rem"
---

# Design System: RAG Multimodal

## 1. Overview

**Creative North Star: "The Evidence Console"**

RAG Multimodal is a focused dark workbench for asking questions against a personal evidence library. The left rail is the library: upload, inspect, filter, and tune retrieval. The central conversation is the working surface: readable answers, explicit source trails, and a composer that stays close to the task. The interface should feel like a dependable instrument for investigation, not a promotional AI demo.

The visual language uses deep blue-black surfaces, restrained indigo actions, cyan evidence signals, and violet modality cues. Density is practical and calm: compact metadata sits beside generous answer prose, while motion is limited to state changes and responsive feedback. It rejects decorative motion, poor navigation, missing search as the library grows, emoji icons, glassmorphism, and generic AI-tool theatrics.

**Key Characteristics:**

- Evidence-first hierarchy: sources are visible, expandable, and easy to trace.
- Dark layered surfaces with a cool, low-noise atmosphere.
- Manrope for readable product UI; IBM Plex Mono for metadata and system cues.
- Restrained 150–250ms state transitions with visible keyboard focus.

## 2. Colors

The palette is restrained and nocturnal: a blue-black foundation carries most of the screen, while indigo, cyan, and violet are reserved for actions, evidence, modality, and state.

### Primary

- **Signal Indigo** (`--primary`, `--primary-hover`): Primary submit actions, active controls, and the visual anchor for user-authored messages.

### Secondary

- **Evidence Cyan** (`--accent`, `--accent-contrast`): Focus rings, selected files, source references, online status, and small moments that confirm traceability.

### Tertiary

- **Modality Violet** (`--violet`): File-type identity and multimodal source distinction. It is a supporting signal, never a competing CTA.

### Neutral

- **Night Base** (`--background`): The full-viewport canvas and quietest layer.
- **Library Surface** (`--surface`): Sidebar and persistent shell surfaces.
- **Raised Surface** (`--surface-raised`, `--surface-strong`): Composer, controls, and focused interactive containers.
- **Muted Surface** (`--surface-muted`): File rows, chips, empty states, and secondary grouping.
- **Primary Ink** (`--text-primary`): Headings, filenames, answers, and high-priority labels.
- **Secondary Ink** (`--text-secondary`): Supporting prose and controls.
- **Muted Ink** (`--text-muted`): Timestamps, helper copy, and low-priority metadata.
- **Semantic States** (`--success`, `--warning`, `--danger`): Processing, caution, failure, and destructive actions.

### Named Rules

**The Signal Rarity Rule.** Indigo, cyan, and violet are functional signals. Never use them as a decorative wash across inactive surfaces.

## 3. Typography

**Display Font:** Manrope (with ui-sans-serif, system-ui fallbacks)
**Body Font:** Manrope (with ui-sans-serif, system-ui fallbacks)
**Label/Mono Font:** IBM Plex Mono (with ui-monospace, monospace fallbacks)

**Character:** Manrope keeps the tool familiar and legible across dense controls and long answers. IBM Plex Mono creates a precise, instrument-panel voice for kickers, counts, timestamps, and short system hints without taking over the reading experience.

### Hierarchy

- **Display** (780, `clamp(1.65rem, 4vw, 2.45rem)`, 1.2): Empty-state invitation and other rare orientation moments.
- **Headline** (750, `clamp(1.18rem, 1.7vw, 1.5rem)`, 1.3): Page and conversation titles.
- **Title** (750, `1rem`, 1.3): Brand title, filenames, and compact component headings.
- **Body** (400, `0.96875rem`, 1.6): User questions and answer prose; cap long-form reading at roughly 75ch.
- **Label** (400, `0.7rem`, 0.13em tracking, uppercase): Section kickers and compact system labels only.

### Named Rules

**The Two-Lane Type Rule.** Manrope carries the task; IBM Plex Mono marks system metadata. Do not put display or mono styling on ordinary buttons, data, or answer prose.

## 4. Elevation

The system uses tonal layering first and restrained shadows second. Background, surface, raised, strong, and muted layers should establish hierarchy before any shadow appears. The soft shadow is reserved for floating mobile navigation and focused shell framing; panel shadow is reserved for large persistent framing. File rows and evidence surfaces should remain border-and-tone based.

### Shadow Vocabulary

- **Soft float** (`--shadow-soft`, `0 18px 48px rgba(0, 0, 0, 0.22)`): Mobile menu trigger and small floating controls.
- **Panel frame** (`--shadow-panel`, `0 24px 70px rgba(0, 0, 0, 0.2)`): Large shell framing or future floating panels, never routine cards.

### Named Rules

**The Tonal Depth Rule.** If a component still reads clearly when its shadow is removed, the elevation is doing its job. Do not pair a thin decorative border with a wide ghost-card shadow.

## 5. Components

### Buttons

- **Shape:** Compact, gently rounded controls (`0.6rem` to `0.8rem`); full pills are reserved for status chips.
- **Primary:** Signal Indigo with white icon/text, fixed `2.85rem` send control height, and compact horizontal padding.
- **Hover / Focus:** Transition color and border treatment over 180ms; lift by at most 1px for a direct action. Every button keeps the cyan `:focus-visible` outline.
- **Secondary / Ghost / Tertiary:** Transparent or low-contrast controls use muted ink and a quiet surface response; danger actions use the danger semantic color without a filled red block.

### Chips

- **Style:** Status and filter chips use a pill shape (`999px`), muted surface, mono or compact sans text, and a one-pixel neutral border.
- **State:** Selected filters use a cyan border/background tint; online, warning, and disabled states use semantic color and never full-saturation inactive fills.

### Cards / Containers

- **Corner Style:** File rows and evidence containers use restrained corners (`0.6rem` to `0.8rem`); the empty-state mark may reach `1.25rem` as a signature shape.
- **Background:** Use the surface ladder rather than white cards: muted for files and suggestions, raised/strong for focused controls and evidence detail.
- **Shadow Strategy:** Follow the Tonal Depth Rule. Routine rows and source entries use tone and border; floating controls may use Soft float.
- **Border:** One-pixel neutral borders or dashed borders for upload/empty affordances. Never use a colored side stripe as the only container treatment.
- **Internal Padding:** Compact rows use roughly `0.42rem` to `0.85rem`; conversation and composer surfaces open to `1rem` and above.

### Inputs / Fields

- **Style:** The composer is a raised, rounded container with a transparent textarea, readable `1rem` input text, and muted placeholder copy. Selects and range controls share the same surface vocabulary.
- **Focus:** `:focus-within` shifts the border to cyan and adds a restrained three-pixel cyan ring. The global keyboard focus outline remains visible on the control itself.
- **Error / Disabled:** Errors use the danger tint with readable copy; disabled controls reduce opacity and expose `cursor: not-allowed` without removing state context.

### Navigation

- **Style:** The sidebar is a persistent 21rem library rail on desktop and a fixed drawer up to 88vw on smaller screens. It contains the logo, upload affordance, file list, filters, query settings, and destructive actions.
- **States:** Selected files use a cyan-toned row; hover raises the row one tonal step; mobile opening uses a scrim, focus containment, Escape, and restoration to the menu trigger.
- **Responsive treatment:** At widths below 1024px the rail becomes a drawer; below 700px the chat header stacks and suggestion actions become one column.

### Evidence Panel

The source panel is progressive disclosure: a compact trigger and preview chips keep answers readable, while expansion reveals deduplicated source entries, page metadata, media previews, and text previews. Source identity is carried by file icon, filename, index, and cyan/violet semantic cues—not by decorative noise.

## 6. Do's and Don'ts

### Do:

- **Do** use the existing surface ladder and semantic tokens from `frontend/app/globals.css`; add a new color only when it has a named product role.
- **Do** keep icons in the Lucide SVG vocabulary, add `cursor: pointer` to clickable elements, and preserve the visible cyan keyboard focus ring.
- **Do** make state legible: hover, focus, active, disabled, loading, error, warning, success, and selected states each need a readable treatment.
- **Do** keep answer prose around 65–75ch, keep metadata compact, and make evidence available without interrupting the answer.
- **Do** respect `prefers-reduced-motion`; transitions communicate state and stay in the 150–250ms range.

### Don't:

- **Don't** ship poor navigation or a no-search information architecture once the file library outgrows a short list.
- **Don't** use emojis as icons; use the established SVG icon set.
- **Don't** omit `cursor: pointer` on clickable elements or make hover transforms shift surrounding layout.
- **Don't** use low-contrast text; body and placeholder copy must remain at least 4.5:1 against its surface.
- **Don't** use instant state changes or invisible focus states; transitions and keyboard focus are part of the component contract.
- **Don't** add decorative motion, orchestrated page-load sequences, or full-saturation accents to inactive states.
- **Don't** use gradient text, colored side-stripe borders, decorative grid backgrounds, glassmorphism by default, or repeated identical card grids.
- **Don't** use display fonts in labels, buttons, or data, reinvent standard affordances, or reach for a modal before an inline/progressive alternative.
