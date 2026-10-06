# FOCUS GUI design language

Version 1. Scope: the main orchestration GUI (`gui_src/main`). The alignment GUI (`gui_src/alignment`) adopts the same language in a later pass.

This document is the single source of truth for the look and feel of the FOCUS GUIs. Every visual change under `gui_src/` follows it. Token values live in [`design-tokens.css`](design-tokens.css); this document explains how to use them. If the two ever disagree, `design-tokens.css` holds the value and this document holds the rule, and both are updated in the same change.

Status: implemented in the main GUI (see [Code organization](#12-code-organization)). The alignment GUI still runs on its previous styling. See [Adoption roadmap](#10-adoption-roadmap).

---

## Contents

1. [Principles](#1-principles)
2. [Typography](#2-typography)
3. [Color](#3-color)
4. [Materials (Liquid Glass)](#4-materials-liquid-glass)
5. [Ambient backdrop](#5-ambient-backdrop)
6. [Geometry and spacing](#6-geometry-and-spacing)
7. [Motion](#7-motion)
8. [Components](#8-components)
9. [Theming mechanics](#9-theming-mechanics)
10. [Adoption roadmap](#10-adoption-roadmap)
11. [Review checklist](#11-review-checklist)
12. [Code organization](#12-code-organization)

---

## 1. Principles

The visual reference is current macOS and iOS: Liquid Glass materials, SF-style typographic hierarchy, spring motion. FOCUS is a scientific tool used for long sessions on dense forms, so the reference is applied with restraint.

1. **Content is solid, chrome is glass.** Forms, lists and results sit on near-opaque material and stay readable. Translucency is reserved for elements that float above content: the top-right control cluster, the sticky action bar, dialogs, popovers and dropdowns.
2. **One accent.** Blue (`--primary`) marks the primary action, selection, focus and progress. All other color carries state.
3. **State is never color alone.** Every state uses color plus at least one of: an icon, a text label, or a shape change. This covers color-blind users and grayscale screenshots in papers.
4. **Motion explains change.** Motion shows where something came from, what changed, or that the system is alive. No decorative loops apart from the ambient backdrop and progress indicators.
5. **Tokens only.** Components reference semantic tokens (`--fg2`, `--mat-content-fill`, `--radius-md`). Raw hex values and raw Tailwind palette classes (`text-gray-500`, `bg-blue-600`) are not used in components.
6. **Branding is fixed.** The logo SVGs, the splash animation and the FOCUS wordmark keep their current geometry, typeface and colors. They are outside this system.

---

## 2. Typography

### 2.1 Typefaces

| Role | Family | Source | Reason |
|---|---|---|---|
| Sans (UI) | **Inter** variable, with the `opsz` axis | `@fontsource-variable/inter` (opsz + wght) | Neutral grotesk close to SF Pro in proportions and metrics, so the macOS feel carries to Linux and Windows. The variable font gives exact weights and automatic optical sizing. Rendering is the same on every OS. |
| Mono | **JetBrains Mono** variable | `@fontsource-variable/jetbrains-mono` (wght) | Tall x-height that matches Inter, a clear slashed zero, and distinct `l I 1 0 O` shapes. Paths and sample IDs stay unambiguous. |

The fonts are self-hosted because the GUI often runs on offline HPC nodes and containers, where a Google Fonts `@import` fails and the UI falls back silently to the system font. The Fontsource packages are imported in `src/main.ts`; Vite copies their woff2 files into the build, so no font is fetched at runtime. Both fonts are SIL OFL.

OpenType features:

| Token | Features | Applied to |
|---|---|---|
| `--font-feature-sans` | `kern liga calt cv05` | All sans text. `cv05` gives lowercase `l` a tail, so `l` and `I` differ in modality and sample names. |
| `--font-feature-num` | `kern cv05 tnum` | Any number that updates or aligns in a column: progress counts, percentages, step numbers, file sizes, sample counts. Tabular figures stop text jittering during the 1.5 s status polling. |
| `--font-feature-mono` | `kern zero` | All mono text. |

`font-optical-sizing: auto` stays on. Inter's `opsz` axis then tightens spacing and contrast at title sizes and opens it up at caption sizes, as SF Pro Display and SF Pro Text do.

### 2.2 Type scale

The scale follows Apple's text-style naming, sized for a 14 px desktop body. Each role is defined as a `font` shorthand token plus a tracking token. Set them together:

```css
font: var(--type-headline);
letter-spacing: var(--track-headline);
```

| Role | Weight | Size / line | Tracking | Use |
|---|---|---|---|---|
| Large title | 700 | 30 / 36 | -0.022em | Reserved for single-message full-screen states (e.g. a future fatal error screen). Not used in v1 views. |
| Title 1 | 600 | 24 / 30 | -0.019em | Page title, one per view: "Configuration builder", "Pipeline completed". |
| Title 2 | 600 | 20 / 26 | -0.015em | Dialog title. Prompt card titles on Setup ("Samples found"). |
| Title 3 | 600 | 17 / 22 | -0.011em | Card title: "Pipeline settings", "Modalities", "Samples", the running status card title. |
| Headline | 600 | 15 / 20 | -0.006em | Sub-card title: `<details>` section summaries, output section names, row titles inside a card. |
| Body | 400 | 14 / 20 | -0.003em | Default text, form values, list rows, dialog message. |
| Body strong | 500 | 14 / 20 | -0.003em | Form labels, emphasized inline text, selected list row. |
| Callout | 400 | 13 / 18 | 0 | Helper text under a control, banner body text, status line text. |
| Footnote | 400 | 12 / 16 | +0.003em | Metadata: counts, timestamps, "3 of 12", inline validation messages. |
| Caption | 500 | 11 / 14 | +0.006em | Badges, status pills, stepper labels under circles. |
| Eyebrow | 600 | 11 / 14 | +0.06em, UPPERCASE | Rare category marker above a title (e.g. "STEP 2 OF 4"). At most one per card. Never used as a section title. |
| Button | 500 | 14 / 20 | -0.003em | Regular buttons. |
| Button large | 600 | 15 / 20 | -0.006em | Large and hero CTAs (Continue, Start processing, Open alignment). |
| Mono | 400 | 13 / 18 | 0 | Paths in inputs, folder browser rows, status line. |
| Mono small | 400 | 12 / 16 | 0 | Inline IDs, file names in output lists, sample chips. |

Rules:

- **Sentence case** for every title, label, button and menu item: "Start processing", not "Start Processing" or "START PROCESSING". Proper nouns and acronyms keep their case (FOCUS, MSI, ST, H&E, OME-TIFF).
- **Hierarchy comes from size and weight, then color.** Titles use `--fg1`. Labels use `--fg2`. Hints and metadata use `--fg3`. `--fg4` is only for placeholders and disabled text, and never carries information.
- **Two weights per view region** where possible: 600 for titles, 400/500 for everything else. 700 appears only in Large title. The branded wordmark is outside the scale.
- **Tracking:** negative at display sizes, near zero at body sizes, slightly positive at 12 px and below. This follows Apple's SF tracking table, and Inter's metrics need the same treatment.
- **Line length:** body paragraphs (banner text, dialog message) are capped at about 70 characters (`max-width: 60ch` at 14 px).
- **Truncation:** paths and names truncate with an ellipsis in the middle when the end carries meaning (file paths), and at the end otherwise. The full value is always available in a `title` attribute.
- **Numbers** that update or align use `--font-feature-num`. Mono already has fixed-width figures.

### 2.3 Mapping from current usage

| Current classes | Where | New role |
|---|---|---|
| `text-5xl font-bold` (FOCUS wordmark) | App splash, SetupView | Unchanged (branding) |
| `text-2xl font-semibold` | ConfigView title, CompleteView title | Title 1 |
| `text-base font-semibold` | SetupView prompt titles | Title 2 |
| `text-xs font-semibold uppercase tracking-widest text-gray-400` (card header label) | ConfigView cards, RunningView header | Title 3, sentence case, `--fg1` |
| Same pattern on ModalityCard name and `<details>` summaries | ModalityCard | Headline, sentence case (modality name keeps the user's casing) |
| `text-xs … uppercase tracking-wide` (inline labels) | RunningView, OutputSummary "Main output" | Footnote `--fg3`, sentence case |
| `text-lg font-bold` (big button) | RunningView manual alignment CTA | Button large |
| `text-sm` | Body text everywhere | Body or Callout, chosen by role |
| `text-xs text-gray-500` | Hints | Footnote `--fg3` |
| `font-mono text-xs` | Paths, counts | Mono small (paths) or Footnote with `--font-feature-num` (counts) |
| inline `font-feature-settings: 'zero'` | about 6 places | Removed. Comes from `--font-feature-mono` / `--font-feature-num`. |

---

## 3. Color

### 3.1 Palette strategy

- Accent: the existing FOCUS blue (Tailwind blue-600, `#2563eb`). It matches the blue dot in the logo.
- Semantics: consolidated to four roles plus one data color. All come from the Tailwind families, so hue and chroma sit in the same system as the accent.
- Neutrals: slightly cool grays, tuned for contrast on translucent materials rather than on flat white.

Former roles and where they went:

| Former | New |
|---|---|
| primary blue | `primary` |
| success green, add emerald | `success` for completion and "on" states; adding uses `primary` (the add button is a regular action) |
| sample indigo | `data-2` (secondary data series: sample progress bar) |
| warning yellow, force orange | `warning` (orange). Yellow fails contrast as text on light glass. |
| danger red, fatal red | `danger` |

### 3.2 Labels and separators

| Token | Light | Dark | Use |
|---|---|---|---|
| `--fg1` | `#0f1115` | `#f4f5f7` | Titles, primary text, values |
| `--fg2` | `#3f4451` | `#c6cad2` | Form labels, secondary text |
| `--fg3` | `#5c6270` | `#9ba1ad` | Hints, metadata, inactive icons |
| `--fg4` | `#8e94a0` | `#6c7280` | Placeholder, disabled. Never information. |
| `--fg-inverse` | `#ffffff` | `#ffffff` | Text on filled buttons |
| `--separator` | `rgb(15 17 21 / .08)` | `rgb(255 255 255 / .08)` | Dividers inside cards, list rows |
| `--separator-strong` | `rgb(15 17 21 / .16)` | `rgb(255 255 255 / .16)` | Input outlines, dashed empty state |

Separators are translucent, so they pick up the material beneath them.

### 3.3 Accent and semantic roles

Each role has five tokens:

- `base`: dots, icons, progress fills, toggle "on" track
- `fill`: filled button background with white text
- `hover`: hover state of `fill`
- `soft`: tinted background for banners, selected rows, chips
- `fg`: text in that color on any material, including on `soft`

| Role | | Light | Dark |
|---|---|---|---|
| primary | base / fill / hover | `#2563eb` / `#2563eb` / `#1d4ed8` | `#3b82f6` / `#2563eb` / `#3b82f6` |
| | soft / fg | `rgb(37 99 235 / .12)` / `#1d4ed8` | `rgb(59 130 246 / .20)` / `#60a5fa` |
| success | base / fill / hover | `#16a34a` / `#15803d` / `#166534` | `#22c55e` / `#15803d` / `#16a34a` |
| | soft / fg | `rgb(22 163 74 / .12)` / `#166534` | `rgb(34 197 94 / .18)` / `#4ade80` |
| warning | base / fill / hover | `#f97316` / `#c2410c` / `#9a3412` | `#fb923c` / `#c2410c` / `#ea580c` |
| | soft / fg | `rgb(234 88 12 / .12)` / `#9a3412` | `rgb(249 115 22 / .18)` / `#fb923c` |
| danger | base / fill / hover | `#dc2626` / `#dc2626` / `#b91c1c` | `#ef4444` / `#dc2626` / `#ef4444` |
| | soft / fg | `rgb(220 38 38 / .12)` / `#b91c1c` | `rgb(239 68 68 / .20)` / `#f87171` |
| data-2 | base / fg | `#6366f1` / `#4338ca` | `#818cf8` / `#a5b4fc` |

Filled buttons use the same `fill` in both modes. White text on a brighter dark-mode fill (for example `#3b82f6`) drops below 4.5:1.

### 3.4 Contrast

WCAG AA is the floor: 4.5:1 for text under 18 px, 3:1 for large text and for non-text UI such as focus rings, toggle tracks and input outlines.

Contrast is measured against the **worst case**: the most saturated ambient field at full opacity, composited under each material, with an inset well on top. The values below are the minimum over all ambient states and all materials (content, chrome, overlay, each with and without inset).

| Text token | Light min | Dark min |
|---|---|---|
| `--fg1` | 14.8 | 11.6 |
| `--fg2` | 7.7 | 7.7 |
| `--fg3` | 4.8 | 4.9 |
| `--primary-fg` | 5.3 | 5.0 |
| `--success-fg` | 5.6 | 7.3 |
| `--warning-fg` | 5.7 | 5.6 |
| `--danger-fg` | 5.1 | 4.6 |
| `--data-2-fg` | 6.2 | 6.4 |

| Pair | Ratio |
|---|---|
| white on `--primary-fill` `#2563eb` | 5.2 |
| white on `--success-fill` `#15803d` | 5.0 |
| white on `--warning-fill` `#c2410c` | 5.2 |
| white on `--danger-fill` `#dc2626` | 4.8 |
| role `fg` and `--fg1` on role `soft` over content (min) | 4.7 |

Any new color pair is checked the same way before it is added to `design-tokens.css`.

### 3.5 Mapping from current Tailwind classes

| Current | New token |
|---|---|
| `bg-gray-50 dark:bg-gray-900` (page) | Backdrop layer (`--backdrop-base` + ambient) |
| `bg-white dark:bg-gray-800` (card) | `--mat-content-fill` material |
| `bg-gray-50 dark:bg-gray-900` (card header strip) | Removed. Title sits in the card (see 8.4). |
| `bg-gray-100 dark:bg-gray-700`, striped rows | `--mat-inset-fill` |
| `text-gray-900/800 dark:text-gray-100` | `--fg1` |
| `text-gray-700/600 dark:text-gray-300` | `--fg2` |
| `text-gray-500/400` | `--fg3` |
| placeholder `text-gray-400` | `--fg4` |
| `border-gray-200 dark:border-gray-700` | `--separator` |
| `border-gray-300 dark:border-gray-600` | `--separator-strong` |
| `bg-blue-600 hover:bg-blue-700` | `--primary-fill` / `--primary-hover` |
| `bg-green-600`, `bg-emerald-500` (Start processing, add) | `--success-fill` for Start processing; add buttons use primary or plain style |
| `bg-indigo-500` (sample bar) | `--data-2` |
| `yellow-*`, `orange-*` | `warning` role |
| `red-*` | `danger` role |
| scrollbar `#cbd5e1` / `#4b5563` | `--separator-strong`, hover `--fg4` |

---

## 4. Materials (Liquid Glass)

### 4.1 Material levels

Materials are layered surfaces. Each one combines a translucent fill, `backdrop-filter: blur() saturate()`, a specular top highlight, a 0.5 px hairline and an elevation shadow.

| Level | Fill (light / dark) | Blur | Saturate | Elevation | Used for |
|---|---|---|---|---|---|
| backdrop | `--backdrop-base` + ambient fields | n/a | n/a | n/a | Window background, one per page |
| content | white .86 / `#1c1e24` .84 | 20 px | 160% | `--elev-1` | Cards: setup card, config cards, running card, output cards, cleanup card |
| chrome | white .62 / `#1c1e24` .62 | 24 px | 180% | `--elev-2` | Top-right control cluster, sticky bottom action bar |
| overlay | white .78 / `#202229` .80 | 40 px | 180% | `--elev-3` (dialog), `--elev-2` (popover) | ConfirmDialog panel, FilePicker dropdown, future menus and popovers |
| inset | `rgb(118 118 128 / .08)` / `/ .18` | none | none | none | Wells inside a material: inputs, folder list, status line, config file preview, segmented control track |

Reference recipe:

```css
.material-content {
  background: var(--mat-content-fill);
  -webkit-backdrop-filter: blur(var(--mat-content-blur)) saturate(var(--mat-content-sat));
          backdrop-filter: blur(var(--mat-content-blur)) saturate(var(--mat-content-sat));
  box-shadow: var(--mat-highlight), var(--mat-hairline), var(--elev-1);
  border-radius: var(--radius-xl);
}
```

The saturation boost makes the ambient hue glow through the glass instead of turning gray, which is the main visual signature of Liquid Glass. The specular highlight (a 1 px inner top line) gives the edge its physical "lens" reading. In dark mode it is much fainter, so it does not read as a border.

### 4.2 Rules

1. **At most two glass layers overlap at any point.** For example, the overlay dialog over the content card over the backdrop. A glass surface is never placed inside another glass surface.
2. **Nested containers use inset, not glass.** The folder browser inside the setup card, the config file preview inside the import card, and the status line inside the running card are inset wells. Lists inside a card (modality rows, review rows) are plain rows separated by hairlines.
3. **Chrome floats.** Chrome surfaces are detached from the window edges by at least `--space-3`, are fully rounded (`--radius-full` for pill clusters, `--radius-xl` for the action bar), and carry `--elev-2`.
4. **Dialogs dim, not blur, the page.** The scrim is `--mat-scrim` with no blur, so the dialog's own glass is the only blur. This also avoids a second full-screen blur pass.
5. **No glass on large scrolling regions.** A blur behind a long scrolling list repaints on every scroll frame. Scroll containers use content material on the parent and inset for the list.

### 4.3 Fallbacks

| Condition | Result |
|---|---|
| `prefers-reduced-transparency: reduce` | All materials opaque, blur 0, ambient fields hidden. Hierarchy is kept through elevation and hairlines. |
| `backdrop-filter` unsupported | All materials opaque, ambient fields at reduced opacity. |
| `forced-colors: active` | System colors. Materials render as `Canvas`, hairlines become 1 px `CanvasText` borders. |

The fallbacks are implemented in `design-tokens.css` by overriding tokens, so components need no special cases.

---

## 5. Ambient backdrop

Glass needs something to refract. The page background is a fixed, full-viewport layer with three large, heavily blurred color fields over `--backdrop-base`. The field hues reflect pipeline state, which gives a peripheral signal of what the system is doing.

### 5.1 States

The backdrop layer carries `data-ambient`. It is derived from existing store state, with no new backend data.

| `data-ambient` | When | Hues (tokens) | Character |
|---|---|---|---|
| `idle` | SetupView, ConfigView | `--ambient-idle-1..3`: cool blues and indigo | Calm, neutral |
| `run` | RunningView, processing | `--ambient-run-1..3`: blue, indigo, sky | Slow drift; the only state where fields move noticeably |
| `wait` | RunningView, manual alignment required | `--ambient-wait-1..3`: orange, peach, blue | Asks for attention without alarm |
| `done` | CompleteView | `--ambient-done-1..3`: green, mint, blue | One bloom on entry, then still |
| `err` | Error state in RunningView | `--ambient-err-1..3`: red, rose, indigo | Still, no drift |

### 5.2 Geometry

- Three radial fields, each 55 to 70 vw in diameter, blurred by `--ambient-blur` (120 px). They are positioned top-left, right, and bottom-center.
- Opacity `--ambient-opacity` (0.55). The light palette uses the 200/300 Tailwind steps; the dark palette uses the 800/900 steps.
- A state change cross-fades all three hues over `--dur-ambient` (1200 ms) with `--ease-in-out`.
- Drift: each field translates by up to 6 vw and scales between 0.95 and 1.05 over `--ambient-drift` (40 s), with phase offsets. Only `transform` and `opacity` animate, so the compositor handles it on the GPU. In `idle`, the drift amplitude is halved. In `done` and `err`, there is no drift.
- An optional 2 to 3 percent monochrome noise overlay prevents gradient banding on 8-bit displays.
- Reduced motion: the fields are static, and state changes cross-fade only.

---

## 6. Geometry and spacing

### 6.1 Radii

| Token | Value | Use |
|---|---|---|
| `--radius-xs` | 6 | Checkboxes, tiny badges |
| `--radius-sm` | 8 | Icon buttons, small controls, list rows inside wells |
| `--radius-md` | 10 | Inputs, selects, regular buttons |
| `--radius-lg` | 12 | Inset wells, banners inside cards |
| `--radius-xl` | 18 | Cards, sticky action bar |
| `--radius-2xl` | 22 | Dialogs, setup hero card |
| `--radius-full` | 9999 | Primary pill buttons, toggles, segmented controls, status pills, chips, chrome cluster |

**Concentric rule:** when one rounded shape sits inside another with a small, even inset, the inner radius equals the outer radius minus the inset. This keeps the curves parallel, as macOS window corners and their toolbar buttons do. Examples:

- A dialog (22) with buttons inset 12 gives a 10 radius (`--radius-md`).
- A card (18) with a well inset 6 gives 12 (`--radius-lg`).
- A well (12) with rows inset 4 gives 8 (`--radius-sm`).

When the inset is larger than the outer radius (normal card padding of 20), the inner element takes its own radius from the scale.

The previous mix of 4 px `rounded` and 8 px `rounded-lg` on equivalent elements is removed. Nothing in the UI uses a 4 px radius.

### 6.2 Control heights

| Token | Height | Use |
|---|---|---|
| `--control-sm` | 24 | Icon buttons in card headers, chips, step dots |
| `--control-md` | 30 | Default for inputs, selects, buttons, segmented controls |
| `--control-lg` | 36 | Dialog buttons, Back / Reset in the action bar |
| `--control-xl` | 44 | Hero CTAs: Setup Continue, Start processing, Open alignment, Start new project |

Hit targets are at least 24 × 24 px, and icon buttons pad their hit area to this when the glyph is smaller.

### 6.3 Spacing

A 4 pt grid (`--space-1` = 4 px, up to `--space-12` = 48 px).

| Context | Value |
|---|---|
| Card padding | `--card-pad` 20 |
| Gap between stacked cards | `--card-gap` 16 |
| Card title to first content | 12 |
| Form rows (label/control) | 12 vertical, 16 between label column and control |
| Inside a well | 12 padding, rows 4 apart |
| Icon to text in a button | 6 |
| Chrome cluster offset from the viewport edges | 16 |

Layout widths and view structure stay as they are in v1 (`max-w-lg`, `max-w-4xl`, `max-w-2xl`). Layout changes belong to a later pass.

---

## 7. Motion

### 7.1 Tokens

| Token | Value | Use |
|---|---|---|
| `--dur-instant` | 100 ms | Press feedback |
| `--dur-fast` | 160 ms | Hover, color, toggle, chip state |
| `--dur-base` | 240 ms | View transitions, popovers, list item enter/leave |
| `--dur-slow` | 380 ms | Dialogs, progress width |
| `--dur-ambient` | 1200 ms | Backdrop state cross-fade |
| `--ease-out` | `cubic-bezier(.22,1,.36,1)` | Exits, color changes, progress |
| `--ease-in-out` | `cubic-bezier(.65,0,.35,1)` | Ambient cross-fades |
| `--ease-spring` | `linear()` spring, about 2% overshoot | Dialogs, popovers, view enters |
| `--ease-spring-snappy` | `linear()` spring, about 5% overshoot | Toggle knob, press release, chips |
| `--press-scale` | 0.97 | Pressed scale for buttons and chips |

Springs are expressed with CSS `linear()` easing, which needs no JavaScript animation library. Browsers without `linear()` get a cubic-bezier with slight overshoot (see the `@supports` block in the tokens).

### 7.2 Recipes

| Interaction | Animation |
|---|---|
| Button hover | Background and shadow, `--dur-fast` `--ease-out`. No movement. |
| Button press | `scale(var(--press-scale))` over `--dur-instant`; release springs back with `--ease-spring-snappy`. |
| Toggle | The knob slides with `--ease-spring-snappy` over `--dur-fast`. The track color cross-fades. The knob stretches about 15% wider while pressed, as in iOS. |
| Segmented control | The selected thumb slides between segments with `--ease-spring` over `--dur-base`. |
| View change (Setup, Config, Running, Complete) | Out-in. The old view fades out over 120 ms. The new view fades in and rises 8 px with `--ease-spring` over `--dur-base`. Cards stagger by 30 ms, capped at 5 cards. |
| Dialog open | The scrim fades over `--dur-base`. The panel goes from `scale(.96)`, opacity 0, to 1 with `--ease-spring` over `--dur-slow`. Close: fade plus `scale(.98)` over 160 ms `--ease-out`. |
| Popover / dropdown | Scales from 0.96 at its anchor edge (`transform-origin` at the trigger) with `--ease-spring` over `--dur-base`. |
| List add/remove (modalities, samples, prompt cards) | Height plus opacity over `--dur-base`. Removed items fade and shrink. |
| Determinate progress | Width with `--ease-out` over `--dur-slow`. A soft highlight sweeps along the filled part every 2.4 s while running. |
| Indeterminate progress | A shimmer gradient travels across the track over 1.6 s, linear, infinite. Replaces `animate-pulse`. |
| Active stage circle | A "breathing" ring: `box-shadow` spread from 0 to 6 px of `--primary-soft`, 2.4 s, `--ease-in-out`, infinite. Replaces `animate-pulse`. |
| Stage completion | The circle fills with `--success`, and the check mark draws via `stroke-dashoffset` over `--dur-base`. |
| Ambient backdrop | See section 5. |
| Focus ring | Appears instantly. Focus is never animated in. |

### 7.3 Rules

- Animate only `transform`, `opacity`, `background-color`, `box-shadow` and `filter`. Never animate width/height on large surfaces. Progress bars may animate `width` or, preferably, `transform: scaleX()`.
- No animation runs longer than `--dur-slow` in the interaction path. Longer durations are only for ambient and progress loops.
- Status polling (every 1.5 s) must not restart animations. Progress values transition, and nothing re-mounts.
- `prefers-reduced-motion: reduce`: springs become `--ease-out`, scale and translate are removed, durations are shortened, the backdrop and shimmer are static, and the breathing ring becomes a static ring. Opacity fades remain.

---

## 8. Components

Visual specification for existing components. Layout, behavior and copy do not change in this pass except for sentence casing. States: rest, hover, press, focus-visible, disabled. Disabled is always opacity 0.4 with `cursor: not-allowed` and no hover change.

### 8.1 Buttons

| Variant | Rest | Hover | Press | Shape |
|---|---|---|---|---|
| Primary | `--primary-fill` bg, `--fg-inverse`, top highlight `inset 0 1px 0 rgb(255 255 255 / .18)`, `--elev-1` | `--primary-hover` | `--primary-press`, press scale | Pill (`--radius-full`) |
| Success (Start processing only) | `--success-fill` bg, white | `--success-hover` | press scale | Pill |
| Secondary | `--mat-inset-fill` bg, `--fg1` text | `--mat-inset-hover` | press scale | `--radius-md` |
| Tinted | `--primary-soft` bg, `--primary-fg` text | soft at 1.5× alpha | press scale | `--radius-md` |
| Plain | transparent, `--primary-fg` text | `--mat-inset-fill` bg | press scale | `--radius-md` |
| Destructive | `--danger-soft` bg, `--danger-fg` text. Filled `--danger-fill` only inside a confirmation dialog. | soft at 1.5× alpha | press scale | `--radius-md` |
| Icon | 24 or 30 square, transparent, `--fg3` icon | `--mat-inset-fill`, `--fg1` icon | press scale | `--radius-sm` |

Sizes follow 6.2. Text uses Button or Button large. Icons are 16 px with a 6 px gap.

Mapping:

| Current | New |
|---|---|
| Setup Continue, Start processing, Open alignment, Start new project | Hero (44, primary or success) |
| Back in the action bar | Secondary, large |
| Reset in the action bar | Destructive (tinted), large |
| Emerald add | Icon with a plus, `--primary-fg` |
| Red remove-all | Icon, `--danger-fg` |
| Remove modality, browse, menu | Icon |
| Dialog confirm | Primary, or destructive filled for dangerous actions |
| Dialog cancel | Secondary |

### 8.2 Text input and select

- Height `--control-md`, radius `--radius-md`, bg `--mat-inset-fill`, no border at rest.
- Hover: `--mat-inset-hover`.
- Focus: bg becomes the solid material color, with `box-shadow: var(--focus-ring)` and a 1 px `--primary` inner outline.
- Text in Body; paths in Mono. Placeholder `--fg4`.
- Leading icon (folder) in `--fg3`, 16 px, 10 px from the left edge.
- Select: native `<select>` with `appearance: none` and a custom chevron (Heroicons `chevron-up-down`, 16 px, `--fg3`). The native menu is kept for accessibility.
- Error: outline `--danger`, message below in Footnote `--danger-fg` with a small warning icon.
- Number inputs use `--font-feature-num`.

### 8.3 Toggle switch

- macOS-style switch, 38 × 22 track, 18 px knob, `--radius-full`.
- Off: track `--separator-strong`, knob white with `--elev-1`.
- On: track `--primary` (or `--warning` for the force-recompute toggle).
- Motion follows 7.2.
- One shared component replaces the six copy-pasted switches.
- The on/off state is also exposed via `role="switch"` and `aria-checked`. The track change gives a shape cue (knob position) in addition to color.

### 8.4 Card

- Content material, `--radius-xl`, padding `--card-pad`.
- **No gray header strip.** The card title (Title 3, `--fg1`) sits at the top of the card body. Header actions (icon buttons) align right on the same baseline row.
- An optional `--separator` hairline under the header row is allowed only when the card body is a list.
- Stacked cards are `--card-gap` apart.
- Nested containers inside a card: an inset well, `--radius-lg`, padding 16, title in Headline.
- `<details>` sections inside cards: the summary row in Headline with a rotating chevron (rotation with `--ease-spring` over `--dur-base`). The content height animates where supported (`interpolate-size: allow-keywords`), otherwise it appears instantly.

### 8.5 Banners and notes

- Inside a card: `--radius-lg`. Standalone: `--radius-xl`.
- Background: the role's `soft` token. No colored border.
- A 16 px leading icon in the role's `base` color. Title in Body strong `--fg1`; body text in Callout `--fg2`.
- Roles:
  - info: primary
  - pre-aligned note: warning
  - existing config: warning
  - corrupted config: danger
  - validation errors: danger
  - manual alignment: warning, with a hero CTA
  - success header on Complete: success
- The Complete success banner replaces the text `✓` glyph with a 40 px check-circle icon in `--success`, drawn in with the stroke animation.

### 8.6 Sample chips and status pills

- Sample chip: 24 high, `--radius-full`, Mono small.
  - On: `--primary-soft` bg, `--primary-fg` text, leading check icon.
  - Off: transparent bg, 1 px `--separator-strong` outline, `--fg3` text, struck-through label.
  - Toggling: press scale plus a `--ease-spring-snappy` color cross-fade.
  - The `border-2` emerald/red treatment is removed. On/off is carried by fill, icon and strike-through, not by red vs green.
- Status pill (running modality): chrome-style small pill, Caption text, leading 6 px dot in `--primary` with the breathing animation.

### 8.7 Progress

- Track: 6 px high, `--radius-full`, `--mat-inset-fill`.
- Fill: `--primary` (item progress), `--data-2` (sample progress) or `--success` (finished).
- Counts beside bars in Footnote with `--font-feature-num`, `--fg3`.
- Indeterminate: shimmer (7.2).
- Stage stepper:
  - circles 32 px, `--radius-full`
  - done: `--success` fill with a white check
  - active: `--primary` fill with the breathing ring
  - pending: inset fill with `--fg3` number
  - connectors are 2 px lines, `--separator-strong`, filling to `--success` as stages complete
  - stage labels in Caption below each circle
- Step dots: 8 px, the same color logic. The active dot is 10 px; the scale change uses the snappy spring.

### 8.8 Status line

- Inset well, `--radius-lg`, Mono 13/18, `--fg2`.
- A leading 6 px dot in the current state color.
- When the message changes, the new text cross-fades over `--dur-fast` (no layout shift; fixed one-line height, middle truncation).

### 8.9 Dialog

- Overlay material, `--radius-2xl`, padding 24, max width unchanged.
- Title 2, message in Body `--fg2`.
- Buttons are right-aligned: Secondary cancel, then Primary or destructive confirm, both `--control-lg`. Their 10 radius is concentric with the 22 radius of the panel at a 12 inset.
- Scrim `--mat-scrim` with no blur. Motion per 7.2.

### 8.10 Folder and file browser

- The list is an inset well with `--radius-lg`. Rows are 30 high with `--radius-sm`, inset 4 from the well edge. Text in Mono, folder icons in `--fg3`.
- Row hover: `--mat-inset-hover`. Selected: `--primary-soft` with `--primary-fg` text.
- No zebra striping. Rows are separated by spacing, not stripes.
- The "up" button is an Icon button.
- FilePicker dropdown: overlay material, `--radius-xl`, `--elev-2`, the popover motion from 7.2.

### 8.11 Chrome cluster (top-right controls)

- One chrome-material pill, 16 px from the top and right, holding the GitHub / Docs / Paper icon buttons, a 1 px × 16 px `--separator` divider, and the theme segmented control.
- Icon buttons are 30 square with `--radius-full`.
- Theme control: a three-segment control (System / Light / Dark) with icons (computer-desktop, sun, moon), each labeled with a tooltip and an accessible name. The track is inset; the thumb is white (light) or `#3a3d45` (dark) with `--elev-1`.

### 8.12 Sticky action bar (ConfigView)

- Chrome material, `--radius-xl`, floating 16 px above the viewport bottom, the same width as the content column.
- Back / Reset on the left as Secondary large; Start processing on the right as the Success hero.
- When content scrolls beneath it, the glass shows the content blurred, which is the intended Liquid Glass effect.

### 8.13 Empty states and drop zone

- Empty lists and the Setup config-import drop zone: an inset well with a 1.5 px dashed `--separator-strong` outline, `--radius-lg`, a centered 20 px icon in `--fg3`, and text in Callout `--fg3`.
- Drag-over: the outline turns `--primary`, the bg `--primary-soft`, and the well scales to 1.01 with `--ease-spring`.

### 8.14 Scrollbars

- 8 px wide with a 2 px transparent border, so the thumb is 4 px at rest and 6 px on hover.
- Thumb `--separator-strong`, hover `--fg4`. The track is transparent.
- Respects `scrollbar-color` in Firefox.

### 8.15 Focus

- Every interactive element shows `--focus-ring` (3 px accent at 45 to 50 percent alpha) on `:focus-visible`, following the element's radius.
- Mouse focus shows no ring.
- Inputs combine the ring with a 1 px inner `--primary` outline.

### 8.16 Icons

- Heroicons outline style, kept and standardized.
- Stroke 1.5 at 16 and 20 px. Use 20 px for solid variants and the mini set at 16 px when available.
- `currentColor` only. Icon color follows the text color of its context. Icons never use raw palette colors.
- Sizes: 16 inline with text and in buttons, 20 in icon buttons and banners, 40 for hero status (Complete).
- Icon paths live in one registry, `src/icons/paths.ts` (Heroicons v2 outline data plus filled brand marks), and are rendered through `<AppIcon name>`. New icons are added there, never pasted into templates.

---

## 9. Theming mechanics

- `<html data-theme="system|light|dark">` stores the user preference in `localStorage['focus-theme']`. The default is `system`.
- The resolved theme is applied as the `.dark` class on `<html>`, which is the only thing the tokens and Tailwind's `dark` variant key on. `system` follows `prefers-color-scheme` live, including OS changes while the app is open.
- The legacy key `focus-theme-override` is removed on startup. It only meant "ignore OS changes this session" and stored no theme, so every user starts on `system`.
- A theme switch cross-fades colors over `--dur-fast`. A temporary `theme-transition` class on `<html>` enables `transition: background-color, color, box-shadow` for 200 ms, then is removed, so normal interactions are unaffected.
- `color-scheme` is set per theme so native controls (select menus, scrollbars, date pickers) match.
- Fonts come from the Fontsource packages imported in `src/main.ts` (see 2.1). No network font requests.
- Implementation: `src/composables/useTheme.ts`. `initTheme()` runs before the app mounts, so the first paint already has the right theme.

---

## 10. Adoption roadmap

The order for implementation passes. Steps 1 to 4 are done for the main GUI.

1. **Foundation** (done). Bundle the fonts. Move `design-tokens.css` into the build of `gui_src/main`, replacing `colors_and_type.css`. Map tokens to Tailwind v4 utilities via `@theme` (for example `bg-material-content`, `text-fg2`, `rounded-card`) so templates stay utility-based. Add the backdrop layer and `data-ambient`.
2. **Primitives** (done). Shared components for Button, IconButton, Card, Toggle, SegmentedControl, TextField, Select, Banner, ProgressBar and InsetList. The repeated markup (6 toggles, about 8 card headers, about 15 inputs, 2 folder browsers) is replaced by these.
3. **Views** (done). Migrate App shell (chrome cluster), SetupView, ConfigView with ModalityCard and the forms, RunningView with StageProgress, CompleteView with OutputSummary, then ConfirmDialog. Each view is visually checked in light, dark, reduced transparency and reduced motion.
4. **Lint guard** (done). `npm run lint:design` (`scripts/check-design.mjs`) fails on raw palette classes, `dark:` color variants and hex colors in `.vue` files. It runs as the first step of `npm run build`.
5. **Alignment GUI.** Share the tokens (one file used by both apps), then apply the same materials to its sidebar and screens. Glass is not placed over the live Pixi canvas.

---

## 11. Review checklist

Every GUI change is checked against this list.

**Do**

- Use semantic tokens for color, radius, spacing, type and motion.
- Use content material for cards, chrome for floating bars, overlay for dialogs and popovers, inset for anything nested.
- Use the type roles from 2.2 and sentence case.
- Pair every state color with an icon, label or shape change.
- Check light, dark, `prefers-reduced-transparency` and `prefers-reduced-motion`.
- Give every interactive element a visible `:focus-visible` ring and an accessible name.
- Use tabular figures for updating numbers.
- Keep animations on transform and opacity, under `--dur-slow` in the interaction path.

**Don't**

- Use raw Tailwind palette classes or hex values in components.
- Nest glass inside glass, or put blur behind large scrolling lists.
- Use UPPERCASE section titles. Uppercase is only for the Eyebrow role.
- Use yellow for text or warnings.
- Use `animate-pulse`. Use the shimmer or breathing ring.
- Add a 4 px radius or a new radius outside the scale.
- Load fonts or assets from the network at runtime.
- Change the logo, splash or wordmark as part of a styling change.

---

## 12. Code organization

How the main GUI (`gui_src/main/src`) implements this document. The alignment GUI follows the same layout when it adopts the system.

### 12.1 Rules

- **Views compose, primitives style.** Views and domain components arrange primitives with Tailwind layout utilities (flex, grid, gap, padding, width). Color, radius, type, material and state styling live in the primitives or in the token-backed utilities. A view never restyles a primitive; it picks a variant through props.
- **One concern per file.** Each component or composable does one thing. Shared logic lives in a composable, shared formatting in `utils/`.
- **Tokens are the only source of values.** The Tailwind default palette, radii, shadows and text sizes are cleared in `styles/theme.css`. Only semantic utilities exist: `text-fg2`, `bg-primary-soft`, `rounded-card`, `type-headline`, `material-content`.
- **Guarded.** `npm run lint:design` must pass. It runs inside `npm run build`.

### 12.2 Layout

| Path | Contents |
|---|---|
| `../design-tokens.css` | All token values (shared by both GUIs) |
| `styles/index.css` | Style entry: tokens, Tailwind, partials |
| `styles/theme.css` | Token to Tailwind utility mapping |
| `styles/base.css` | Document defaults, focus, scrollbars, theme fade |
| `styles/typography.css` | `type-*`, `nums`, `ellipsis` utilities |
| `styles/materials.css` | `material-content`, `material-chrome`, `material-overlay`, `material-popover`, `material-inset`, `scrim` |
| `styles/transitions.css` | Shared Vue transitions: `view`, `fade`, `pop`, `popover`, `list`, `swap` |
| `styles/layouts.css` | Grid templates shared by several components (`modality-columns`) |
| `icons/paths.ts` | Icon registry |
| `utils/` | `format.ts`, `errors.ts`, `params.ts` |
| `composables/` | `useTheme`, `useDialog`, `useDirectoryBrowser`, `useInlineEntry`, `useAnchoredPopover`, `useAmbientState` |
| `components/ui/` | Domain-agnostic primitives (12.3) |
| `components/shell/` | Backdrop, chrome cluster, theme switcher, splash, brand |
| `components/browser/` | `DirectoryList` (presentational) and `FilePicker` |
| `components/builder/` | Guided configuration builder: frame (`StepFrame`, `BuilderHeader`, `BuilderStepper`, `BuilderNav`), `steps/`, and per-step parts (`modalities/`, `settings/`, `review/`) |
| `components/setup/`, `config/`, `running/`, `complete/` | Domain components of each view (`config/` keeps the schema-driven parameter form) |
| `store/builder.ts` | Builder navigation state (step, unlocked steps, active modality, edit mode); UI only, never saved |
| `views/` | Thin view compositions |

### 12.3 Primitive catalog

| Component | Purpose |
|---|---|
| `AppIcon` | Icon from the registry |
| `BaseButton` | Text button: `variant`, `size`, `shape`, `icon`, `block` |
| `IconButton` | Icon-only button or link with a required `label` |
| `GlassCard` | Content-material card; `hero` for the focal card |
| `InsetWell` | Nested container (inset material) |
| `CardHeader` | Title row with meta and actions slots |
| `FieldShell` | Shared field box (fill, radius, focus ring, error) |
| `TextField`, `SelectField` | Inputs built on `FieldShell` |
| `ToggleSwitch` | `role="switch"`, `tone` primary or warning |
| `SegmentedControl` | Sliding-thumb radio group |
| `FormRow` | Label and control row |
| `Banner` | Tinted message with icon, title, actions |
| `ToggleChip` | On/off chip; `size="lg"` is a grid tile (sample inclusion) |
| `RadioDot` | Single radio for picking one row (reference modality) |
| `Stepper` | Horizontal step indicator for guided flows (12.4) |
| `OverflowMenu` | Ellipsis button with a small teleported action menu |
| `StatusPill` | Chrome pill with a live dot |
| `ProgressBar` | Determinate or shimmer progress |
| `Disclosure` | Animated `<details>` section |
| `DropZone`, `EmptyState` | Dashed wells for file drop (Setup config import step) and empty lists |
| `InlineEntryForm` | Name entry row, paired with `useInlineEntry` |
| `PageHeader`, `ActionBar` | View title and floating action bar |

Before adding a new component, check this catalog. Extend a primitive with a variant when the need is visual; add a new primitive only for a new interaction pattern, and list it here.

### 12.4 Guided flows

Multi-step tasks (the configuration builder) follow one pattern:

- **Frame.** Every step uses `StepFrame`: Title 1, a one-line description, an optional toolbar aligned with the title, then the content. The column width is the same on every step, so the frame does not move between steps.
- **Progress.** A sticky `Stepper` in a chrome pill sits under the header. Completed and unlocked steps are clickable; locked steps are inert.
- **Moving.** A floating `ActionBar` holds Back on the left and the forward action on the right. When the step is incomplete, the forward button is disabled and the reason is shown next to it in Footnote. The last step's forward action is the task's primary action (Start processing).
- **Gating.** Steps unlock as the user progresses. Entering with complete data (a loaded config) unlocks every step and opens the last one.
- **Targeted edits.** Leaving the final step to change one thing enters edit mode: the forward action becomes "Done, back to review", so the user returns without walking the remaining steps.
- **Destructive side effects ask first.** A change that discards data entered in a later step (changing a modality type, changing the reference) shows a confirm dialog that names what will be reset.

