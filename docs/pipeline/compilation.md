# MuData Compilation Stage

## Overview

The compilation stage is the final pipeline step that builds a single MuData object from the outputs of preprocessing, alignment, and registration.

This step **only runs if both of the following conditions are met**:

- The reference modality is spot‑based (`msi` or `st`).
- At least one non‑reference (target) modality has a `registration_type` other than `none` (i.e. registration runs for that modality).

If these conditions are not met, the pipeline skips compilation and the final outputs remain the per‑modality merged files produced by earlier stages (e.g. `merged/alignment/`, `merged/registration/`, `merged/annotation/`).

## What compilation does

When compilation runs, it combines two inputs for each target modality:

- The **reference modality** preprocessed output (its own `obsm['spatial']` and feature matrix).
- Each **target modality** registered output (its `obsm['spatial']`, already expressed in the reference coordinate frame, and the registered feature matrix).

It then merges them into one MuData object with the following structure:

- `mod["<modality>"]`: one `AnnData` per modality containing its feature matrix, `var`, and modality‑specific metadata.
- `obs`: global observation metadata that applies to all modalities (e.g. `sample_id`).
- `obsm`: spatial coordinates expressed in the reference modality coordinate system (`obsm['spatial']`).
- `uns`: global processing metadata.

No new spatial alignment or feature computation is performed; compilation only assembles the existing registered data into a single MuData file.

## Output

If compilation runs, the primary output is:

```
<dataset_path>/merged/multimodal_dataset.h5mu
```

If compilation is skipped, no `.h5mu` file is produced.

## Configuration (minimal)

Compilation itself has no dedicated configuration keys. Whether it runs is determined solely by:

- `reference_modality` being a spot‑based modality.
- At least one target modality having a `registration_type` that is not `"none"`.

## Notes

- All modalities included in the MuData share the same number of observations (spots) and the same coordinate system (the reference’s).
- The MuData file can be loaded directly with `mudata.read_h5mu(...)` and used with scanpy, squidpy, or other MuData‑compatible tools.
