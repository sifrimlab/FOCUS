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
anchor coordinates alone with the same `infer_lattice` used by the alignment clustering
([Alignment Clustering §2](spatial_clustering.md#2-clustering-units)). The lattice basis is reduced to its two
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

FOCUS design choice: the Raman engine (§4) uses a Gaussian of the anchor-frame distance
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
it produces are a representation of H&E morphology. `feature_extraction` applies to brightfield
images only: an image recorded as fluorescence is rejected, at configuration time when the modality
sets `image_type: "fluorescence"` and at registration time from the `focus_image_type` that
[microscopy preprocessing](microscopy_methods.md#3-image-type) records. Within brightfield, nothing
in the FOCUS code path narrows or adapts the domain: windows are cut, ImageNet-normalized (§2.3) and
forwarded to the model regardless of stain or channel semantics, and the resulting
\(N_R \times 1536\) matrix is written out with no diagnostic. Applying it to IHC with other
chromogens or other histological stains therefore yields well-formed embeddings of an input the
model was never trained on.

Channel coercion happens before encoding (`ensure_hwc3`): a 1-channel image is replicated to RGB, a
4-channel image loses its 4th channel. A single-channel brightfield image is consequently encoded as
a grayscale slide.

The intended configuration for those modalities is `registration_type: "none"`: preprocessing and
alignment still run and their outputs are kept, only the registration matrix (and hence the MuData
modality) is not produced.

### 2.1 Encoding scale

Prov-GigaPath tiles were cut from slides resized to 0.5 µm per pixel (20x)[^xu2024]. FOCUS encodes
the stored level of the [preprocessed pyramid](microscopy_methods.md#71-pyramid)
whose pixel size is closest to 0.5 µm on a log scale. Level \(i\) has the pixel size
\(u_i = u_0\sqrt{(W_0/W_i)(H_0/H_i)}\) from the measured level sizes; ties go to the finer level.
FOCUS adaptation: the level is used as stored, without resampling, so a 40x scan (0.25 µm) is
encoded from its half-resolution level and a 20x scan from full resolution, and a source coarser
than 0.5 µm is encoded at full resolution.

The level-0 pixel size \(u_0\) (geometric mean of x and y) is read, in order, from:

1. the `PhysicalSizeX/Y` of the preprocessed OME-TIFF, recorded by
   [microscopy preprocessing](microscopy_methods.md#24-pixel-size) from the source file metadata
   or, when the file has none, the `pixel_size` setting;
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
anchor centre. A token is background when at least 99 % of its pixels match the background color,
a pixel matching iff `np.isclose(pixel, bg_color, atol=1e-3)` holds on every
channel; background tokens are removed from every footprint, and an anchor left without tokens keeps
an all-zero row. The background color is the fill recorded by preprocessing
(`focus_background_fill`, [microscopy §5.3](microscopy_methods.md#53-crop-and-fill-opt-in): 0 is
black, any other value white) when the image was filled, and the configured `background_color`
otherwise.

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

- `background_color` (`white`/`black`, default `white`), used when the image records no background
  fill
- `force_recomputing` (default `false`)

The window size is fixed by the model; a `patch_size` key in `registration_settings` is ignored with
a warning.

---

## 3. Spot interpolation (`spot_interpolation`)

Applicable modality types: `msi`, `st`.

One method registers a spot-based target on the anchors for every combination of spot sizes:
anchors larger than, equal to or smaller than the target spots, small weak target spots on a large
signal-rich anchor, and the reverse. The weight of a target spot for an anchor is the area where
their footprints overlap[^goodchild1980] [^jones1999]. FOCUS adaptation: the overlap is estimated by
sampling the smaller footprint (§3.2), so ellipses, rotated lattices and projective maps need no
special case.

### 3.1 Footprints

The target points \(t_j\) are the target modality's `obsm['spatial']`, read from the sample's rows
of the merged preprocessed target file. That file carries the cross-sample feature filter, the union
feature panel and the merged normalization, so every registered sample has the same features; the
per-sample preprocessed file is read only when no merged file exists.

- The anchor footprint \(F_i\) is the footprint of §1.2 and §1.3, with area \(|F_i|\), mapped into
  the target frame. Its area there is \(A_i = |F_i|\,|\det J_i|\), with \(J_i\) the Jacobian of the
  alignment map at the anchor centre (target units per anchor unit).
- The target footprint \(G_j\) is the footprint of the target's own `spot_size` (read from
  `uns['spot_size']`, per sample in a merged file), centred on \(t_j\) in the target frame, with the
  shape rule of §1.2: an ellipse on a hexagonal target lattice, a rectangle along the lattice axes
  otherwise. All target spots of a sample share one area \(A_T\). A target without a valid
  `spot_size` uses the median spacing of its spots as a square footprint, with a warning.

Both coordinate systems are in µm, which makes \(A_i\) and \(A_T\) comparable. An alignment map whose
scale is outside 0.5 to 2 target units per anchor unit logs a warning, because it points to
coordinates in pixels.

### 3.2 Overlap

The overlap area is \(O_{ij} = |F_i \cap G_j|\), in target-frame units. It is computed pairwise:
footprints of one set may overlap each other, and each anchor takes its own overlap.

The footprint with the smaller area is sampled (the anchor when \(A_i \le A_T\) for the median
anchor, the target otherwise) on a cell-centred stratified grid of \(p \times p\) points, clipped to
the ellipse for an elliptical footprint. \(p\) is as fine as a budget of \(10^7\) sample points allows
for the number of sampled footprints, at most 32. Each point is located in the footprints of the
other set with the KD-tree search and the containment test of §1.3, whose upper footprint edges are
open so that footprints tiling the plane share no point.

- Anchors sampled: \(O_{ij} = A_i\,\gamma_{ij}\), with \(\gamma_{ij}\) the share of the points of
  anchor \(i\) that lie in \(G_j\).
- Targets sampled: \(O_{ij} = A_T\,\eta_{ij}\), with \(\eta_{ij}\) the share of the points of target
  \(j\) that lie in \(F_i\).

An anchor inside one target spot therefore takes that spot, an anchor over many target spots takes
all of them in proportion to their overlap, and a target spot split among small anchors is shared
by area. The estimate has a quantisation error of at most half a grid step per footprint edge.

### 3.3 Reduction

What the target measures decides how the overlap is used. The target is a count matrix when its
merged preprocessed file records `uns['focus_normalization']` (spatial transcriptomics) and an
intensity matrix otherwise (MSI).

| Target | Anchor feature vector | Property |
|---|---|---|
| Intensities (intensive) | \(\hat f_i = \dfrac{\sum_j O_{ij} f_j}{\sum_j O_{ij}}\) | A constant field stays constant for every combination of spot sizes; the result does not scale with the number of pixels. |
| Counts (extensive) | \(\hat f_i = \sum_j \dfrac{O_{ij}}{A_T} f_j\) | Each target spot gives the share of its counts that lies in the anchor, so counts are conserved where anchors tile the target and small weak spots add up on a large anchor without loss. |

The row-normalized overlap (intensities) or \(O/A_T\) (counts) is one sparse operator applied to
`.X` and every layer. Counts are taken from `layers['raw']`, or from `.X` when the target
normalization is `none`.

Coverage is the share of the anchor footprint that target spots cover,
\(c_i = \min\bigl(1, \sum_j O_{ij} / A_i\bigr)\). It is below 1 where target spots have gaps
between them or the anchor lies partly over the edge of the target section. An anchor that no
target spot overlaps keeps an all-zero row.

For counts, the merged registered file is normalized once with the target's method
(`uns['focus_normalization']`) after the samples are merged, and the area used by
`area_log_normalize` is the covered footprint area \(c_i\,|F_i|\) (the full area for an uncovered
anchor, whose row is zero). Fractional counts are valid input to every normalization.

### 3.4 Output semantics

Per-sample output AnnData has:

- `.X`: registered target features at anchor rows, sparse CSR float32, shape \((N_R, D_T)\)
- `.layers['raw']`: the apportioned counts, for a normalized ST target
- `.obs['coverage']`: \(c_i\)
- `.obs['focus_area']`: the covered footprint area, for an ST target
- `.obsm['spatial']`: anchor coordinates in the target frame
- target `.var` and `.var_names` propagated
- every target `.layers` entry reduced with the same operator, for an intensity target

The `.uns['focus_registration_geometry']` stamp adds `overlap_sampled` (`anchor` or `target`) and
`overlap_points_per_axis`.

### 3.5 Parameters

- `force_recomputing` (default `false`)

---

## 4. Raman pixel interpolation (`raman_pixel_interpolation`)

Applicable modality type: `raman`.

[Raman preprocessing](raman_methods.md#6-output) produces a hyperspectral OME-TIFF with one image
per acquisition (one channel per Raman shift), not a spot table. This engine applies the Gaussian
footprint average of §4.2 to the pixels of every image.

### 4.1 Geometric setup

1. Each pixel \((\text{col},\text{row})\) of an image's full-resolution level is a target point at
   its centre \((\text{col}+0.5,\text{row}+0.5)\) (§1.4), with a feature vector equal to its
   spectral intensities. Pixels with a non-finite value (not measured by the acquisition) are not
   target points.
2. The alignment maps the reference spots into image-0 pixels. Image \(k\), stored with placement
   \((x_0, y_0, s_x, s_y)\) (its pixel \((c, r)\) lies at image-0 pixel
   \((x_0 + s_x c, y_0 + s_y r)\)), receives the spots at \(((x - x_0)/s_x, (y - y_0)/s_y)\), and the
   footprint map (§1.1) is fitted again on these coordinates, so the reference's `spot_size` is
   honoured whatever the Raman pixel size. Only the pixels inside the union of the footprints' pixel
   bounding boxes are read. Files written before `raman-2` hold one image at the identity
   placement.

### 4.2 Kernel and interpolation

The Gaussian weights \(w_{ij}\) of §1.4 over the measured pixels whose centres lie inside each
footprint \(\mathcal{N}_i\), row-normalized, per image:

\[
\hat{f}(r_i)=\frac{\sum_{j\in\mathcal{N}_i} w_{ij} f(t_j)}{\sum_{j\in\mathcal{N}_i} w_{ij}}
\]

An anchor row with no measured pixel of an image in its
footprint is NaN for that image's channels; if no footprint reaches an image, a warning is logged.
The images' features are concatenated.

### 4.3 Output semantics

Per-sample output AnnData has:

- `.X`: interpolated spectra, dense float32, shape \((N_R, C)\) with \(C\) the channels of all images
- `.obsm['spatial']`: anchor coordinates in the Raman image-0 pixel frame
- `.var`: indexed by channel name, read from the OME-XML (`<wavenumber> cm-1`; the acquisition name
  is appended when a wavenumber is measured by several acquisitions; `Channel_N` when the file has
  no names), with columns `wavenumber` and `acquisition`
- no `.layers`

### 4.4 Parameters

- `force_recomputing` (default `false`)

---

## 5. Pixel composition (`pixel_composition`)

Applicable modality types: `raman`, and `microscopy_image` recorded as fluorescence
(`focus_image_type`). Brightfield images are rejected at config validation and at registration, with
`feature_extraction` named as the alternative.

A hyperspectral or multiplexed image holds many pixels per anchor: a 55 µm Visium spot covers about
7,500 pixels of a 0.56 µm Raman image. Each pixel measures a small volume that is close to one
chemical or marker state, and a spot is a mixture of such states. The mean spectrum of a spot (§4)
does not separate the proportion of a state from its local intensity. This engine describes every
measured pixel by a composition vector that sums to 1, from a dataset-level codebook of pixel
phenotypes, and gives each anchor the mean vector over the pixels in its footprint: the fraction of
the footprint occupied by each phenotype.

### 5.1 Channel groups and pixel sampling

1. The images of the target files (§4.1: one image per Raman acquisition with its placement, one
   image at the identity placement for microscopy) are grouped by channel signature: the channel
   names and, for Raman, the wavenumber axis. Images of the same group share one codebook; images
   with different channels (a CH window and a fingerprint window) never do.
2. A group with fewer than 3 channels has no composition and only contributes channel means
   (§5.4).
3. FOCUS design choice: the codebook is fitted once per dataset, on pixels of every sample, so the
   composition columns mean the same phenotype in every sample. Every sample draws at random up to
   `200000 / n_samples` pixels per image among the measured pixels whose centres lie inside its
   anchor footprints (§1.3), and the group keeps at most 200,000. The drawn pixels live in memory
   only.

### 5.2 Hyperspectral images: linear spectral unmixing

Raman images (any file not recorded as fluorescence) follow the linear mixing model: a pixel
spectrum \(x\) is a non-negative combination of the spectra of \(p\) pure components (endmembers)
\(E\), with abundances \(a\) that sum to 1,

\[
x = E^\top a + \varepsilon, \qquad a \ge 0, \quad \mathbf{1}^\top a = 1 .
\]

Multivariate curve resolution of hyperspectral SRS images uses this model to map the components
of cells and tissue[^zhang2013], and unsupervised endmember extraction (VCA, N-FINDR) recovers
cholesterol, lipids, proteins and nucleic acids from tissue Raman images[^bergner2012].

1. **Number of endmembers.** Spectra averaged over 4.5 µm bins (the cellular-scale bins of Raman
   denoising, [Raman methods](raman_methods.md)), mean removed, have the Gavish-Donoho rank \(r\)
   (singular values above \(\omega(\beta)\) times their median)[^gavish2014]. \(p = r + 1\)
   endmembers, at most the number of channels: \(p\) endmembers span a \((p-1)\)-dimensional affine
   subspace[^nascimento2005]. Pixels are too noisy for the rank; the bins of every sample's tiles are
   drawn at random, at most 300,000.
2. **Endmembers.** Vertex Component Analysis on the drawn pixels[^nascimento2005]: projection on
   the first \(p\) right singular vectors (projective) when the estimated signal-to-noise ratio
   exceeds \(15 + 10 \log_{10} p\) dB, else on the first \(p - 1\) principal components with a
   constant coordinate; the endmembers are the pixels at the extremes of \(p\) successive random
   directions orthogonal to the endmembers already found, taken from the projected data in the
   original units. VCA assumes that each endmember occurs as a nearly pure pixel, which holds at
   the pixel scale and not at the spot scale.
3. **Abundances.** Fully constrained least squares, the least-squares abundances on the probability
   simplex[^heinz2001]. FOCUS solves this problem exactly with a primal active-set method that
   carries the sum-to-one constraint as an equality on the passive set (the Lawson-Hanson active
   set[^lawson1974] adapted to the simplex), instead of the augmented-matrix approximation of the
   original method.

Under the linear model, unmixing commutes with averaging up to the constraints: the mean abundances
over a footprint are close to the abundances of its mean spectrum. The pixels contribute the
endmembers, which cannot be identified from spot means.

### 5.3 Multiplexed fluorescence images: pixel clustering

Fluorescence images follow Pixie[^liu2023]:

1. every channel is smoothed with a Gaussian of \(\sigma = 2\) pixels;
2. every pixel is divided by its total over the channels; pixels with zero total over the channels
   are excluded;
3. every channel is divided by its 99.9th percentile over the drawn pixels;
4. a 10 x 10 self-organizing map (learning rate 0.05 to 0.01, random initialisation, Euclidean
   distance, 10 passes; trained on a random subset of the pixels) clusters the pixels into 100
   nodes. FOCUS trains it as the FlowSOM reference code does[^vangassen2015]: codes initialised on
   random pixels, bubble neighbourhood on the Chebyshev grid distance, learning
   rate from 0.05 to 0.01 and radius from the 0.67 quantile of the grid distances to 0 over 10
   passes (radius 0.5 once below 1), training stopped after a pass that moves the codes by less
   than 1 in total;
5. the mean of the drawn pixels of every SOM node (nodes without pixels drop out), z-scored per
   channel and capped at 3, is grouped by consensus hierarchical clustering[^monti2003]. Pixie
   does not state the consensus settings; FOCUS uses the ConsensusClusterPlus settings of FlowSOM's
   metaclustering: 100 resamples of 90 % of the nodes, average linkage,
   Euclidean distance, consensus classes from average linkage on \(1 - M\) of the consensus matrix
   \(M\).

In Pixie the user sets the number of metaclusters and adjusts them by inspection. FOCUS
adaptation: the number of phenotypes \(k \le 20\) is set by the automatic criterion of FlowSOM
(`MetaClustering`, default maximum 20): the elbow of
the within-cluster sum of squares of the consensus classes for \(k = 1, \dots, 20\), after one pass of
3-point smoothing (weight 0.2), at the split that minimises the absolute residuals of two
least-squares lines[^vangassen2015]. The sum of squares weights every node by its pixels (FOCUS
adaptation): the phenotypes describe pixels, and the sparse nodes that the map places between
phenotypes would otherwise each weigh as much as a phenotype. A pixel takes the metacluster of its
nearest node; its composition vector is one-hot. FOCUS adaptation: pixels with zero total, excluded
from the clustering, are counted in a last `no signal` entry, so the composition of a footprint
sums to 1 over all its measured pixels.

### 5.4 Readout

The union of the footprints' pixel bounding boxes (§4.1) is read in tiles of 1024 x 1024 pixels
(with an 8-pixel margin for the Gaussian of §5.3). For every group, with \(\mathcal{N}_i\) the
measured pixels whose centres lie inside footprint \(i\) (§1.3, uniform weights) and \(v_j\) the
composition vector of pixel \(j\):

\[
c_i = \frac{1}{|\mathcal{N}_i|}\sum_{j\in\mathcal{N}_i} v_j, \qquad
m_i = \frac{1}{|\mathcal{N}_i|}\sum_{j\in\mathcal{N}_i} x_j .
\]

Images of the same group add their pixels to the same sums. A footprint without a measured pixel
of a group is NaN for that group. The coverage of a footprint is its measured pixel count over its
area in image pixels (the anchor area times the determinant of the alignment Jacobian), the largest
over the images, clipped to \([0, 1]\).

### 5.5 Output semantics

Per-sample output AnnData has:

- `.X`: the composition \(c_i\), dense float32, one block per group, each block summing to 1
  (Raman: `endmember 1..p`; fluorescence: `phenotype 1..k` and `no signal`); the names carry the
  group in parentheses when the dataset has several groups. A dataset whose groups all have fewer
  than 3 channels stores the channel means in `.X`.
- `.var`: columns `group` and `phenotype`
- `.obsm['channel_mean']`: DataFrame of \(m_i\), one column per channel of every group
- `.obsm['spatial']`: anchor coordinates in the image-0 pixel frame
- `.obs['coverage']`: measured fraction of the footprint (§5.4)
- `{modality}_{sample}_pixel_phenotypes.ome.tiff` in the sample's registration folder: one uint8
  image per source image, with its placement, holding the dominant entry of every pixel read
  (`1..K`, `0` where nothing was measured or read); pyramid levels take every other pixel

The merged file adds `uns['pixel_phenotypes']`, per group: `model` (`unmixing`, `pixel_clusters`
or `mean`), `channels`, `phenotypes`, `prototypes` (endmember spectra, or the mean blurred
intensities of each phenotype, in the original units), `pixel_share` (mean composition of the
drawn pixels) and `criterion` (spectral rank and singular values, or the within-cluster sums of
squares and \(k\)).

The codebooks are stored in
`{dataset_path}/merged/registration/{modality}_merged_processed_aligned_registered_pixel_phenotypes.npz`
with the fingerprint of the per-sample inputs (§6). A changed sample input refits the codebooks,
and the codebook fingerprint enters every per-sample fingerprint, so every sample is recomputed.

### 5.6 Parameters

- `force_recomputing` (default `false`): refits the codebooks and recomputes every sample

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
- the algorithm marker of the spot method (`overlap-1`), so a change of that method rebuilds only the spot registrations;
- for `pixel_composition`, its algorithm marker (`pixel-phenotypes-1`) and the codebook fingerprint (§5.5);
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

| Modality type | `feature_extraction` | `spot_interpolation` | `raman_pixel_interpolation` | `pixel_composition` | `none` |
|---|---:|---:|---:|---:|---:|
| `microscopy_image` | brightfield | no | no | fluorescence | yes |
| `msi` | no | yes | no | no | yes |
| `raman` | no | no | yes | yes | yes |
| `st` | no | yes | no | no | yes |

Compatibility is enforced during config validation; an incompatible `registration_type`/modality pairing raises a `ValueError`. The reference modality itself is not registered; it defines the row index used by all registered targets.

---

## References

[^goodchild1980]: Goodchild MF, Lam NS-N. Areal interpolation: a variant of the traditional spatial problem. *Geo-Processing* 1:297-312 (1980). No DOI. Source-zone values reassigned to target zones by the area of their intersection.
[^jones1999]: Jones PW. First- and second-order conservative remapping schemes for grids in spherical coordinates. *Mon Weather Rev* 127:2204-2210 (1999). [doi:10.1175/1520-0493(1999)127<2204:FASOCR>2.0.CO;2](https://doi.org/10.1175/1520-0493(1999)127%3C2204:FASOCR%3E2.0.CO;2). First-order conservative remapping: weights are overlap areas, so the integral of a field is conserved.
[^hartley1997]: Hartley RI. In defense of the eight-point algorithm. *IEEE Trans Pattern Anal Mach Intell* 19:580-593 (1997). [doi:10.1109/34.601246](https://doi.org/10.1109/34.601246). Coordinate normalisation before the linear solve.
[^hartley2004]: Hartley R, Zisserman A. *Multiple View Geometry in Computer Vision*, 2nd edn. Cambridge University Press, Cambridge (2004). [doi:10.1017/CBO9780511811685](https://doi.org/10.1017/CBO9780511811685). Direct linear transform for a 2D homography.
[^xu2024]: Xu H, Usuyama N, Bagga J, et al. A whole-slide foundation model for digital pathology from real-world data. *Nature* 630:181-188 (2024). [doi:10.1038/s41586-024-07441-w](https://doi.org/10.1038/s41586-024-07441-w). Slides resized to 0.5 µm per pixel and tiled at 256 x 256 pixels (Methods, "Preprocessing WSIs"); tile encoder pretrained with DINOv2; inference transform (224 px input, ImageNet normalization) from the reference code at [github.com/prov-gigapath/prov-gigapath](https://github.com/prov-gigapath/prov-gigapath).
[^oquab2023]: Oquab M, Darcet T, Moutakanni T, et al. DINOv2: learning robust visual features without supervision. *arXiv* 2304.07193 (2023). [doi:10.48550/arXiv.2304.07193](https://doi.org/10.48550/arXiv.2304.07193)
[^dosovitskiy2021]: Dosovitskiy A, Beyer L, Kolesnikov A, et al. An image is worth 16x16 words: transformers for image recognition at scale. *International Conference on Learning Representations* (2021). [doi:10.48550/arXiv.2010.11929](https://doi.org/10.48550/arXiv.2010.11929)

[^gavish2014]: Gavish M, Donoho DL. The optimal hard threshold for singular values is 4/√3. *IEEE Trans Inf Theory* 60:5040-5053 (2014). [doi:10.1109/TIT.2014.2323359](https://doi.org/10.1109/TIT.2014.2323359). Hard threshold \(\omega(\beta)\) times the median singular value for an unknown noise level.
[^zhang2013]: Zhang D, Wang P, Slipchenko MN, Ben-Amotz D, Weiner AM, Cheng JX. Quantitative vibrational imaging by hyperspectral stimulated Raman scattering microscopy and multivariate curve resolution analysis. *Anal Chem* 85:98-106 (2013). [doi:10.1021/ac3019119](https://doi.org/10.1021/ac3019119). Linear mixing model of hyperspectral SRS images: concentration maps and spectra of the chemical components.
[^bergner2012]: Bergner N, Krafft C, Geiger KD, Kirsch M, Schackert G, Popp J. Unsupervised unmixing of Raman microspectroscopic images for morphochemical analysis of non-dried brain tumor specimens. *Anal Bioanal Chem* 403:719-725 (2012). [doi:10.1007/s00216-012-5858-1](https://doi.org/10.1007/s00216-012-5858-1). VCA and N-FINDR endmembers of tissue Raman images.
[^nascimento2005]: Nascimento JMP, Bioucas-Dias JM. Vertex component analysis: a fast algorithm to unmix hyperspectral data. *IEEE Trans Geosci Remote Sens* 43:898-910 (2005). [doi:10.1109/TGRS.2005.844293](https://doi.org/10.1109/TGRS.2005.844293). Signal subspace projection, SNR switch and endmembers as extremes of orthogonal projections.
[^heinz2001]: Heinz DC, Chang C-I. Fully constrained least squares linear spectral mixture analysis method for material quantification in hyperspectral imagery. *IEEE Trans Geosci Remote Sens* 39:529-545 (2001). [doi:10.1109/36.911111](https://doi.org/10.1109/36.911111). Abundances constrained to be non-negative and to sum to one.
[^lawson1974]: Lawson CL, Hanson RJ. *Solving Least Squares Problems*. SIAM, Philadelphia (1995; first published 1974). [doi:10.1137/1.9781611971217](https://doi.org/10.1137/1.9781611971217). Active-set method for non-negative least squares.
[^liu2023]: Liu CC, Greenwald NF, Kong A, et al. Robust phenotyping of highly multiplexed tissue imaging data using pixel-level clustering. *Nat Commun* 14:4618 (2023). [doi:10.1038/s41467-023-40068-5](https://doi.org/10.1038/s41467-023-40068-5). Methods, "Pixel clustering methodology": Gaussian blur (standard deviation 2), exclusion of pixels with zero expression, pixel normalization, 99.9th percentile marker normalization, 10 x 10 SOM, z-scored node means capped at 3, consensus hierarchical metaclustering; Results: SOM trained on a random subset of the pixels.
[^vangassen2015]: Van Gassen S, Callebaut B, Van Helden MJ, et al. FlowSOM: using self-organizing maps for visualization and interpretation of cytometry data. *Cytometry A* 87:636-645 (2015). [doi:10.1002/cyto.a.22625](https://doi.org/10.1002/cyto.a.22625). Self-organizing map and consensus metaclustering; SOM training and the elbow criterion as implemented in the reference code ([github.com/SofieVG/FlowSOM](https://github.com/SofieVG/FlowSOM), `R/2_buildSOM.R`, `src/som.c`, `R/4_metaClustering.R`).
[^monti2003]: Monti S, Tamayo P, Mesirov J, Golub T. Consensus clustering: a resampling-based method for class discovery and visualization of gene expression microarray data. *Mach Learn* 52:91-118 (2003). [doi:10.1023/A:1023949509487](https://doi.org/10.1023/A:1023949509487). Consensus matrix over resampled clusterings.
