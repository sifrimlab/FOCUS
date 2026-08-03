# Direct Mapping GUI

A Vue 3 desktop-only web application for aligning heterogeneous modalities (Image ↔ Image, Image ↔ Spot, Spot ↔ Image, Spot ↔ Spot).

> **Vocabulary note: this app's layer names are inverted relative to the FOCUS pipeline.** This frontend's two layers are fetched from the Flask routes `/reference/payload` (the **static** background layer) and `/target/payload` (the **interactive**, user-moved layer). The FOCUS pipeline maps these the other way around: the orchestrator passes the pipeline's **reference** modality to this app's `target` (interactive) layer and the pipeline's **non-reference** modality to this app's `reference` (static) layer (see `_run_alignment` in `orchestrator.py`). So, in FOCUS pipeline terms, **the user moves the reference modality over the fixed non-reference (target) modality**, even though in this app's own code the moved layer is named `target`. The labels below are the app-internal ones.

## Features

- **Dual Canvas Visualization**: Reference (static) and Target (interactive) layers.
- **Interactive Alignment**: Translate, Scale, Rotate, and Flip the target modality.
- **Heterogeneous Support**: Handles both Image (PNG) and Spot (JSON) data types.
- **Export Logic**: Computes coordinate mappings based on alignment.
- **Responsive Design**: Optimized for desktop, with a warning screen for small viewports (< 720px).
- **Dark/Light Mode**: System-aware theming.

## Tech Stack

- **Vue 3**: Composition API, Script Setup.
- **Vite**: Build tool.
- **TypeScript**: Type safety.
- **Pinia**: State management.
- **Tailwind CSS**: Utility-first styling (v4).
- **gl-matrix**: Matrix math for transformations.
- **Axios**: API client.

## Project Structure

- `src/api/`: API client and types.
- `src/components/`: Reusable UI components (Canvas, Controls, Screens).
- `src/layouts/`: Layout components.
- `src/store/`: Pinia store for state management.
- `src/utils/`: Utility functions (Matrix math, Export logic).

## Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. Run development server:
   ```bash
   npm run dev
   ```

3. Build for production:
   ```bash
   npm run build
   ```

## Usage

1. The app polls `/status` to check for available samples.
2. Once a sample is ready, it loads metadata and payloads.
3. Use the Right Panel to align the Target (top layer) to the Reference (bottom layer).
   - **Drag** on canvas to translate.
   - **Scroll** on canvas to zoom (scale).
   - Use **Control Panel** for rotation, flip, and fine-tuning.
4. Click **Confirm Alignment** to submit the mapping.

## API Mocking

To test without a backend, you can mock the API in `src/api/client.ts` or run a simple mock server.
