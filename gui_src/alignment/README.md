# Direct Mapping GUI

A Vue 3 desktop-only web application for aligning heterogeneous modalities (Image ↔ Image, Image ↔ Spot, Spot ↔ Image, Spot ↔ Spot).

> **Vocabulary note: this app's layer names are inverted relative to the FOCUS pipeline.** This frontend's two layers are fetched from the Flask routes `/reference/payload` (the **static** background layer) and `/target/payload` (the **interactive**, user-moved layer). The FOCUS pipeline maps these the other way around: the orchestrator passes the pipeline's **reference** modality to this app's `target` (interactive) layer and the pipeline's **non-reference** modality to this app's `reference` (static) layer (see `_run_alignment` in `orchestrator.py`). So, in FOCUS pipeline terms, **the user moves the reference modality over the fixed non-reference (target) modality**, even though in this app's own code the moved layer is named `target`. The labels below are the app-internal ones.

## Features

- **Canvas-first workspace**: the canvas fills the window; controls float at its edges (see `../DESIGN.md` section 8.17).
- **Dual layers**: Reference (static, bottom) and Target (interactive, top).
- **Interactive alignment**: translate, distort (corner and edge handles), rotate, scale and flip the target layer.
- **Heterogeneous support**: Image (PNG) and Spot (JSON) data types.
- **Export logic**: computes coordinate mappings from the two layer transforms.
- **Desktop only**: a notice replaces the workspace below 720 px.
- **Shared design system**: tokens, styles, icons and primitives come from `@focus/ui` (`../shared`), the same as the main GUI.

## Layout

| Area | Component | Contents |
|---|---|---|
| Top left | `SampleBadge` | Sample ID and progress through the dataset |
| Top right | `ChromeCluster` (shared) | Project links, theme |
| Left edge | `ToolRail` | Aligner / Camera mode, flips, resets (menu) |
| Bottom left | `LayerDock`, `LayerPanel` | One collapsible panel per layer: clusters, spots shown, spot size; opacity for the target |
| Bottom center | `TransformBar` | Scale, rotation, view zoom |
| Bottom right | Confirm button | Submits the alignment |

## Project structure

- `src/api/`: API client and types.
- `src/canvas/`: the two Pixi canvases and their modules (layer fit, geometry and hit testing, commands, pointer and drag arithmetic, spot drawing, overlay).
- `src/composables/`: screen selection, layer view, cluster filter, transform controls.
- `src/screens/`: full-screen states (loading, finished, error, offline, small screen) as data.
- `src/store/`: `main.ts` (alignment state, unchanged) and `ui.ts` (panel state, never sent).
- `src/styles/`: style entry and the HUD grid.
- `src/utils/`: matrix math and export logic.
- `src/workspace/`: the workspace view and its floating controls (`hud/`).
- `tests/`: golden equivalence tests (see below).

## Setup

The GUIs are npm workspaces rooted at `gui_src/`. Install once from there:

```bash
cd gui_src
npm install
```

Then, from `gui_src/`:

```bash
npm run dev -w alignment     # development server
npm test -w alignment        # golden tests
npm run build -w alignment   # design lint, type check, build into src/focus/GUI/alignment/
```

## Tests

`tests/` replays scripted alignment sessions (every modality pair, two canvas sizes, plus a three-sample run through the app shell) against a mocked Pixi and backend. After each step it records the transforms as Float32 bytes, the store writes and the confirmed payloads. The UI is driven through `tests/harness/workspaceDriver.ts`, which uses accessible names.

| Spec | Golden files | Meaning |
|---|---|---|
| `invariant.golden.spec.ts` | `__golden__/invariant/` | Recorded from the GUI before the alignment fixes below, covering every operation they did not touch. Must never change. |
| `alignment.golden.spec.ts`, `app.golden.spec.ts` | `__golden__/*.jsonl` | The full sessions, re-recorded once after those fixes. |
| `fixes.spec.ts` | none | Behavior of the fixes. |

The alignment fixes: each sample starts at its fit (centered on the reference) and is drawn without waiting for an interaction; Reset transform returns to that fit; a window resize moves both layers together, so an alignment in progress holds; the rotate drag maps the pivot like the rest of the canvas when the view is panned or zoomed; and the SPOT→IMAGE export keeps a spot whose id is 0.

Never update golden files with `-u` unless a numeric change is intended, and then only the affected spec.

## Usage

1. The app polls `/status` until a sample is available, then loads metadata and payloads.
2. In Aligner mode, align the target (top layer) to the reference (bottom layer):
   - drag inside the frame to translate; drag a corner or edge handle to distort;
   - drag just outside a corner to rotate; scroll to scale about the cursor.
3. In Camera mode, drag to pan and scroll to zoom the view.
4. Fine-tune scale, rotation and zoom in the transform bar; flips and resets are on the tool rail.
5. Click **Confirm alignment** to submit the mapping.

## API mocking

To test without a backend, mock the API in `src/api/client.ts` or run a simple mock server that serves the built files and the `/status`, `/{reference,target}/metadata`, `/{reference,target}/payload` and `/confirm` routes. The golden tests use an in-memory mock (`tests/harness/apiMock.ts`).
