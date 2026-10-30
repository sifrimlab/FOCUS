# Registration Methods

## 1. Objective

Given aligned reference coordinates in target space, registration builds a target-modality feature matrix indexed by reference observations.

For target modality \(T\), output has shape \(N_R \times D_T\), where rows correspond one-to-one with reference observations.

The input is the aligned reference AnnData written by the [alignment stage](alignment_methods.md).
It holds the reference spots twice: `obsm['spatial']`, in the reference modality's own frame, and
`obsm['{target_name}_spatial']`, the same spots expressed in the target modality's frame. This page
calls the reference spots the **anchors** and uses the term interchangeably with *reference
observations*. Each anchor has a **footprint**: the area its measurement covers, given by the
reference's `uns['spot_size']` \((s_x, s_y)\) in the units of its `obsm['spatial']`. Every
registration engine evaluates footprints with the geometry of §1.1 to §1.4.

### 1.1 Anchor frame and alignment map

`spot_size` is defined in the anchor frame, so every footprint test and distance is evaluated there.
Target elements (spots, pixels, encoder tokens) are mapped into the anchor frame with the inverse of
the alignment map, which is recovered per sample from the pairs
\(c_i = \) `obsm['spatial']`\([i]\) and \(m_i = \) `obsm['{target_name}_spatial']`\([i]\):

1. Rows with a non-finite coordinate are excluded; those anchors cannot be placed.
2. Fewer than 3 valid anchors, or valid anchors on one line, leave the map undetermined: the sample
   is skipped with a warning.
3. \(m_i = c_i\) for every row: the map is the identity (pre-aligned pairs).
4. \(m_i - c_i\) constant within float32 storage precision (\(8\,\varepsilon_{32}\max|m|\)): the map
   is a translation.
5. Otherwise a projective map (homography) \(H\) is fitted with the normalised direct linear
   transform[^hartley1997][^hartley2004] on a seeded subsample of at most 4096 anchors plus the four
   coordinate extremes, scaled so \(w = 1\) at the anchor centroid. The alignment step applies one
   3x3 matrix to every anchor and rescales each axis to full resolution, so one homography reproduces
   the stored pairs. The fit is accepted when it reproduces every valid pair within
   \(\max(10^{-3}\sqrt{s_x s_y}\,\lambda,\ 8\,\varepsilon_{32}\max|m|)\), with \(\lambda\) the linear
   scale of \(H\) at the centroid.
6. Stored pairs that no single homography reproduces get a per-anchor affine map, with a warning:
   each anchor's linear part is the least-squares fit to the offsets of its 8 nearest valid anchors,
   and its translation makes the map reproduce that anchor's own pair exactly. A neighbourhood
   without two independent directions uses the global least-squares affine. FOCUS design choice:
   a piecewise map keeps every anchor exactly at its stored position, and the per-anchor pieces
   cover footprints that extend beyond the convex hull of the anchors.

A footprint whose bounding rectangle crosses the horizon of the homography (\(w \le 0\)) cannot be
placed. Anchors that cannot be placed get an all-zero row.

### 1.2 Footprint shape

FOCUS design choice: the footprint shape follows the anchor lattice, which is inferred from the
anchor coordinates alone with the same `infer_lattice` used by
[ST preprocessing](st_methods.md#361-clustering-units). The lattice basis is reduced to its two
shortest vectors \(a, b\) with \(|a| \le |b|\) and \(|a \cdot b| \le |a|^2/2\), and
\(\cos\theta = a\cdot b / (|a||b|)\):

| Lattice | Test | Footprint |
|---|---|---|
| hexagonal | \(\bigl||\cos\theta| - 0.5\bigr| \le 0.02\) and \(|b|/|a| \le 1.05\) | ellipse inscribed in \((s_x, s_y)\), axis-aligned |
| square, rectangular | \(|\cos\theta| \le 0.02\) | rectangle \(s_x \times s_y\), \(s_x\) along the basis vector nearest the anchor x-axis |
| oblique, irregular (no lattice, or fewer than 16 anchors) | otherwise | axis-aligned rectangle \(s_x \times s_y\) |

Spots on a hexagonal lattice are discs (standard Visium); spots on a square or rectangular lattice
tile the plane (MSI raster pixels, Visium HD bins, Raman pixels), so a disc would leave gaps and a
rectangle along the lattice axes covers each spot's cell. A rectangle's rotation is wrapped to
\((-\pi/4, \pi/4]\) and set to exactly 0 below \(10^{-6}\) rad.

### 1.3 Membership

FOCUS design choice: a target element belongs to an anchor's footprint when its centre, mapped into
the anchor frame, lies inside the footprint, boundary included. For an anchor centre \(c_i\), a
point \(q\) in the anchor frame and the footprint rotation \(\phi\), let \(u = R(-\phi)(q - c_i)\):

\[
\text{rectangle: } |u_x| \le s_x/2,\ |u_y| \le s_y/2
\qquad
\text{ellipse: } \left(\frac{u_x}{s_x/2}\right)^2 + \left(\frac{u_y}{s_y/2}\right)^2 \le 1
\]

Candidates are searched in the target frame with a KD-tree (`cKDTree.query_ball_point`) around the
mapped anchor centre, with a radius that covers the mapped corners of the footprint's bounding
rectangle (the mapped rectangle is convex in front of the horizon). The test above then decides
membership. The result is a sparse \(N_R \times N_T\) operator. An anchor whose footprint holds no
target element has an all-zero row.

### 1.4 Pixel convention and Gaussian weights

Image pixel \((\text{col}, \text{row})\) covers \([\text{col}, \text{col}+1) \times [\text{row},
\text{row}+1)\) in the frame the alignment step writes; its point is its centre
\((\text{col} + 0.5, \text{row} + 0.5)\).

FOCUS design choice: the weighted engines use a Gaussian of the anchor-frame distance
\(d_{ij} = \|q_j - c_i\|\), with a bandwidth of half the geometric mean spot size:

\[
\sigma=\frac{\sqrt{s_x s_y}}{2},\qquad w_{ij}=\exp\!\left(-\frac{d_{ij}^2}{2\sigma^2}\right)
\]

---

## 2. Feature extraction (`feature_extraction`)

Applicable modality type: `microscopy_image`, restricted by the encoder's training domain to
**H&E-stained brightfield RGB sections** (see §2.0).

### 2.0 Applicability

The encoder is Prov-GigaPath, pretrained on brightfield tiles from H&E-stained whole-slide
images[^xu2024] ([model card](https://huggingface.co/prov-gigapath/prov-gigapath)). The embeddings
it produces are a representation of H&E morphology, and nothing in the FOCUS code path narrows or
adapts that domain: windows are cut, ImageNet-normalized (§2.3) and forwarded to the model
regardless of stain, imaging mode or channel semantics, and the resulting \(N_R \times 1536\) matrix
is written out with no diagnostic. Applying it to immunofluorescence, IHC with other chromogens,
other histological stains, or any non-brightfield-RGB acquisition therefore yields well-formed
embeddings of an input the model was never trained on.

Channel coercion happens before encoding (`ensure_hwc3`): a 1-channel image is replicated to RGB, a
4-channel image loses its 4th channel. A single-channel fluorescence image is consequently encoded as
a grayscale brightfield slide rather than rejected.

The intended configuration for those modalities is `registration_type: "none"`: preprocessing and
alignment still run and their outputs are kept, only the registration matrix (and hence the MuData
modality) is not produced.

### 2.1 Encoding scale

Prov-GigaPath tiles were cut from slides resized to 0.5 µm per pixel (20x)[^xu2024]. FOCUS encodes
the stored level of the [preprocessed pyramid](microscopy_methods.md#5-ome-tiff-pyramid-output)
whose pixel size is closest to 0.5 µm on a log scale. Level \(i\) has the pixel size
\(u_i = u_0\sqrt{(W_0/W_i)(H_0/H_i)}\) from the measured level sizes; ties go to the finer level.
FOCUS adaptation: the level is used as stored, without resampling, so a 40x scan (0.25 µm) is
encoded from its half-resolution level and a 20x scan from full resolution, and a source coarser
than 0.5 µm is encoded at full resolution.

The level-0 pixel size \(u_0\) (geometric mean of x and y) is read, in order, from:

1. the `PhysicalSizeX/Y` of the preprocessed OME-TIFF, recorded by
   [microscopy preprocessing](microscopy_methods.md#5-ome-tiff-pyramid-output) from the source
   file;
2. for an MSI reference, whose coordinates are always µm, the inverse of the alignment map's linear
   scale \(\sqrt{|\det J|}\) at the anchor centroid;
3. otherwise it is unknown: level 0 is encoded and a warning is logged.

The chosen level is read into memory; integer dtypes are rescaled by their dtype maximum so the
encoder receives \([0,1]\) data. Anchor coordinates and footprints map to the level through the
measured size ratio \((W_i/W_0, H_i/H_0)\).

### 2.2 Footprint-restricted CLS readout

Prov-GigaPath's tile encoder is a vision transformer[^dosovitskiy2021] pretrained with
DINOv2[^xu2024][^oquab2023]: the input window is split into square tokens of \(p\) pixels (read from
the model), a class token (CLS) is prepended, and the CLS output after the final normalisation is
the tile embedding. FOCUS design choice: each anchor gets the CLS embedding of exactly the tissue
inside its footprint, computed with attention masking inside windows of the training size, so the
tokens keep their surrounding context while the embedding reads only the footprint.

**Windows.** The training window side \(b\) is the model's input size (224 px); the context margin
is \(c = b/4 = 56\) px. For an anchor whose footprint box, clipped to the image, has extent \(e\)
(the larger side, in level pixels):

- the window needs \(n = \lceil e \rceil_p + 2c + p\), where \(\lceil\cdot\rceil_p\) rounds up to a
  multiple of \(p\) and the extra token absorbs the stride rounding below;
- window side \(= b\) when \(n \le b\), otherwise \(n\) rounded up to a multiple of \(4p\), at most
  64 tokens (1024 px for \(p = 16\)); larger windows use position embeddings interpolated by `timm`
  (`dynamic_img_size`). A footprint wider than the largest window is read through the largest window
  centred on it, with a warning;
- each window axis is at most the image size rounded down to whole tokens;
- windows sit on a lattice of stride \(s = \max(p, \lfloor \text{side} - \lceil e \rceil_p - 2c
  \rfloor_p)\): the window origin is \(s \cdot \mathrm{round}((\text{mid} - \text{side}/2)/s)\),
  clamped to the image, with mid the centre of the clipped footprint box. Every footprint therefore
  has at least \(c\) pixels of tissue around it inside its window, except where the window meets the
  image edge.

Anchors that map to the same window share one forward pass, in chunks of at most 512 readouts.
Anchors that cannot be placed (§1.1), whose footprint lies outside the image, or for which the image
is smaller than one token keep an all-zero row.

**Tokens of a footprint.** Token \((r, k)\) of a window with origin \((o_x, o_y)\) is centred at
\((o_x + (k + 0.5)p,\ o_y + (r + 0.5)p)\). A token belongs to an anchor when its centre, mapped to
level 0 and into the anchor frame, lies inside the footprint (§1.3). An anchor whose footprint
contains no token centre (a footprint smaller than one token) reads the token that contains the
anchor centre. A token is background when at least 99 % of its pixels match the configured
background color, a pixel matching iff `np.isclose(pixel, bg_color, atol=1e-3)` holds on every
channel; background tokens are removed from every footprint, and an anchor left without tokens keeps
an all-zero row.

**Readout.** For each chunk, one query per anchor is appended to the window's token sequence,
initialised as the window CLS (class token plus its position embedding). The boolean attention mask
allows:

- the CLS and the patch tokens (and any register tokens) to attend among themselves only, exactly as
  in a standard forward pass, so the readout queries never change them;
- each readout query to attend to itself, to any register tokens, and to its footprint's tokens; it
  never attends to the window CLS or to another query.

The embedding of an anchor is its query's output after the final normalisation and the model head.
A query whose footprint covers every token of the window reproduces the standard CLS embedding;
FOCUS verifies this on a random window when the model is loaded and stops with an error if the
readout differs (relative tolerance \(10^{-3}\)).

### 2.3 Encoder normalization and model

Input tensor normalization, as in the Prov-GigaPath reference inference code[^xu2024]:

\[
\hat{x}_{c} = \frac{x_c-\mu_c}{\sigma_c}
\]

with ImageNet statistics

\[
\mu=(0.485,0.456,0.406),\quad \sigma=(0.229,0.224,0.225)
\]

Model: `hf_hub:prov-gigapath/prov-gigapath` via `timm` with `dynamic_img_size=True`, run in
`torch.inference_mode()` on a CUDA GPU when one is available, otherwise on the CPU.

On GPU the batch size is chosen per window shape from the available VRAM: up to 8 copies of a real
window with its largest readout chunk are forwarded once to measure the per-window cost (halved on
an out-of-memory error), and the batch is sized to 80 % of the free memory, clamped to 1 to 512
windows, capped so the in-flight pixels stay within \(512 \times 224^2\), and rounded to a multiple
of 8 from 16 windows up. A CUDA out-of-memory error halves the batch and retries. On CPU the batch is
at most 32 windows under the same pixel cap.

### 2.4 Outputs

Per-sample AnnData:

- `.X`: embeddings, dense float32, shape `(N_ref, D)` with `D` the model width (1536)
- `.obsm['spatial']`: the anchor coordinates in level-0 image pixels
- `.obs['sample_id']`
- `.uns`: the stamps of §6, with `focus_registration_geometry` extended by `encoder`,
  `encoded_level` and, when the pixel size is known, `encoded_um_per_px`
- no `.var` metadata (`var_names` are positional strings) and no `.layers`

### 2.5 Parameters reflected in code path

- `background_color` (`white`/`black`, default `white`)
- `force_recomputing` (default `false`)

The window size is fixed by the model; a `patch_size` key in `registration_settings` is ignored with
a warning.

---

## 3. Spot interpolation (`spot_interpolation`)

Applicable modality types: `msi`, `st`.

### 3.1 Geometric setup

The target points \(t_j\) are the target modality's `obsm['spatial']`. The footprint
\(\mathcal{N}_i\) of anchor \(i\) holds the targets whose mapped centres lie inside it (§1.1 to §1.3).

### 3.2 Kernel and interpolation

Weights \(w_{ij}\) are the Gaussian of §1.4, on anchor-frame distances. Normalized weighted average:

\[
\hat{f}(r_i)=\frac{\sum_{j\in\mathcal{N}_i} w_{ij} f(t_j)}{\sum_{j\in\mathcal{N}_i} w_{ij}}
\]

If \(\mathcal{N}_i=\varnothing\), row \(i\) is left at zeros. The row-normalized weights form one
sparse operator applied to `.X` and to every layer.

### 3.3 Output semantics

Per-sample output AnnData has:

- `.X`: interpolated target features at anchor rows, dense float32
- `.obsm['spatial']`: anchor coordinates in the target frame
- target `.var` and `.var_names` propagated
- every target `.layers` entry interpolated with the same weights

### 3.4 Parameters

- `force_recomputing` (default `false`)

---

## 4. Spot aggregation (`spot_aggregation`)

Applicable modality types: `msi`, `st`.

Same footprint geometry as §3, but the targets inside the footprint are **summed with equal weight** rather than Gaussian-averaged. Applies to subcellular-resolution spot modalities (e.g. Visium HD), where one reference footprint covers many native spots.

### 4.1 Geometric setup

Identical to §3.1: \(\mathcal{N}_i\) holds the targets whose mapped centres lie inside the footprint of anchor \(i\) (§1.1 to §1.3).

### 4.2 Aggregation

Equal-weight sum over the footprint:
\[
\hat{f}(r_i)=\sum_{j\in\mathcal{N}_i} f(t_j)
\]

There is no kernel and no normalization: weights are 1, and the sum is **not** divided by \(|\mathcal{N}_i|\) (footprint occupancy). Equivalently, with the sparse \(N_R\times N_T\) membership matrix \(A\) where \(A_{ij}=1\) iff \(t_j\in\mathcal{N}_i\),
\[
\hat{F}=A\,F,
\]
evaluated as a sparse product (the target matrix \(F\) is not densified). Footprints may overlap, so one target may contribute to several reference rows. If \(\mathcal{N}_i=\varnothing\), row \(i\) is left at zeros.

`.X` and every `.layers` entry are aggregated with the same membership matrix \(A\).

### 4.3 Output semantics

Per-sample output AnnData has:

- `.X`: summed target features at anchor rows, shape \((N_R, D_T)\), kept **sparse (CSR)**, unlike
  §3, which densifies
- `.obsm['spatial']`: anchor coordinates in the target frame
- target `.var` and `.var_names` propagated
- any target `.layers` summed identically, also sparse

### 4.4 Parameters

- `force_recomputing` (default `false`)

---

## 5. Raman pixel interpolation (`raman_pixel_interpolation`)

Applicable modality type: `raman`.

[Raman preprocessing](raman_methods.md#10-outputs) produces a hyperspectral OME-TIFF (one channel per Raman shift), not a spot table. This engine applies the interpolation of §3 to pixel data.

### 5.1 Geometric setup

1. Each Raman pixel \((\text{col},\text{row})\) of the full-resolution level is a target point at its
   centre \((\text{col}+0.5,\text{row}+0.5)\) (§1.4), with a feature vector equal to its spectral
   intensities across channels.
2. The footprints are mapped into the Raman pixel frame through the alignment map (§1.1), so the
   reference's `spot_size` is honoured whatever the Raman pixel size. Only the pixels inside the
   union of the footprints' pixel bounding boxes enter the computation. Channel names are taken from
   the OME-XML metadata (fallback `Channel_N`).

### 5.2 Kernel and interpolation

Identical to §3.2: the Gaussian weights of §1.4 over the pixels whose centres lie inside each
footprint, row-normalized. Anchor rows with no pixel in the footprint are left at zeros.

If no footprint reaches the image, a warning is logged and the output is an all-zero
\((N_R, C)\) matrix.

### 5.3 Output semantics

Per-sample output AnnData has:

- `.X`: interpolated spectra, dense float32, shape \((N_R, C)\)
- `.obsm['spatial']`: anchor coordinates in the Raman pixel frame
- `.var`: indexed by spectral channel name, read from the OME-XML. ASHLAR writes no channel names,
  so in practice `Channel_0 … Channel_{C-1}`
- no `.layers`

### 5.4 Parameters

- `force_recomputing` (default `false`)

---

## 6. Merge and cache behavior

All registration engines:

- process the samples present in both the anchor and the target mapping, and log a warning and skip
  any sample whose anchor lacks `obsm['{target_name}_spatial']`
- fall back to `spot_size` \([1, 1]\) with a warning when the anchor carries no usable `spot_size`
- report the number of anchors that cannot be placed (§1.1)
- stamp each per-sample output with `uns['registration_type']`,
  `uns['focus_registration_fingerprint']` and `uns['focus_registration_geometry']` (alignment map
  kind `transform`, `lattice`, `footprint` shape and `footprint_angle`)
- write every output to a temporary file and rename it into place, so an interrupted run never
  leaves a truncated file

FOCUS design choice: outputs are reused by content fingerprint. A per-sample output is reused only
when its stored fingerprint equals the SHA-256 fingerprint of its current inputs:

- the registration method version and `registration_type`;
- digests of the anchor's `obsm['spatial']` and `obsm['{target_name}_spatial']`, and its `spot_size`;
- the identity of the target input: the level-0 pixel digest of a FOCUS OME-TIFF, the
  preprocessing fingerprint of an h5ad, otherwise the file's path, size and modification time;
- the method settings (`feature_extraction`: `background_color`, the encoder, the reference
  modality type and the image's recorded pixel size).

An output without a fingerprint, or with a different one, is recomputed. The merged file is reused
only when its fingerprint, built from the per-sample fingerprints in sample order, matches;
otherwise it is rebuilt with `anndata.concat(merge='same')`, `obs_names` rewritten as
`{sample_id}_{row_index}`, `uns['registration_type']` and the merged fingerprint re-stamped, and
`uns['focus_registration_geometry']` stored as a dict keyed by `sample_id`. `force_recomputing`
bypasses both checks. The merged file is

```text
{dataset_path}/merged/registration/{modality}_merged_processed_aligned_registered.h5ad
```

---

## 7. Compatibility matrix

| Modality type | `feature_extraction` | `spot_interpolation` | `spot_aggregation` | `raman_pixel_interpolation` | `none` |
|---|---:|---:|---:|---:|---:|
| `microscopy_image` | yes | no | no | no | yes |
| `msi` | no | yes | yes | no | yes |
| `raman` | no | no | no | yes | yes |
| `st` | no | yes | yes | no | yes |

Compatibility is enforced during config validation; an incompatible `registration_type`/modality pairing raises a `ValueError`. The reference modality itself is not registered; it defines the row index used by all registered targets.

---

## References

[^hartley1997]: Hartley RI. In defense of the eight-point algorithm. *IEEE Trans Pattern Anal Mach Intell* 19:580-593 (1997). [doi:10.1109/34.601246](https://doi.org/10.1109/34.601246). Coordinate normalisation before the linear solve.
[^hartley2004]: Hartley R, Zisserman A. *Multiple View Geometry in Computer Vision*, 2nd edn. Cambridge University Press, Cambridge (2004). [doi:10.1017/CBO9780511811685](https://doi.org/10.1017/CBO9780511811685). Direct linear transform for a 2D homography.
[^xu2024]: Xu H, Usuyama N, Bagga J, et al. A whole-slide foundation model for digital pathology from real-world data. *Nature* 630:181-188 (2024). [doi:10.1038/s41586-024-07441-w](https://doi.org/10.1038/s41586-024-07441-w). Slides resized to 0.5 µm per pixel and tiled at 256 x 256 pixels (Methods, "Preprocessing WSIs"); tile encoder pretrained with DINOv2; inference transform (224 px input, ImageNet normalization) from the reference code at [github.com/prov-gigapath/prov-gigapath](https://github.com/prov-gigapath/prov-gigapath).
[^oquab2023]: Oquab M, Darcet T, Moutakanni T, et al. DINOv2: learning robust visual features without supervision. *arXiv* 2304.07193 (2023). [doi:10.48550/arXiv.2304.07193](https://doi.org/10.48550/arXiv.2304.07193)
[^dosovitskiy2021]: Dosovitskiy A, Beyer L, Kolesnikov A, et al. An image is worth 16x16 words: transformers for image recognition at scale. *International Conference on Learning Representations* (2021). [doi:10.48550/arXiv.2010.11929](https://doi.org/10.48550/arXiv.2010.11929)
