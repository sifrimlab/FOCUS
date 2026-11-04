"""Supported modality, alignment, registration and preprocessing option values."""

from typing import Final

from focus.config.choices import _AbstractEnum


class SegmentationBackgroundColor(_AbstractEnum):
    """Supported segmentation background color values in declaration order."""

    WHITE: Final = "white"
    BLACK: Final = "black"


class MsiIonMode(_AbstractEnum):
    """Supported msi ion mode values in declaration order."""

    POSITIVE: Final = "pos"
    NEGATIVE: Final = "neg"


class AnnotationFileType(_AbstractEnum):
    """Supported annotation file type values in declaration order."""

    GEOJSON: Final = "geojson"


class RegistrationType(_AbstractEnum):
    """Supported registration type values in declaration order."""

    NONE: Final = "none"
    FEATURE_EXTRACTION: Final = "feature_extraction"
    SPOT_INTERPOLATION: Final = "spot_interpolation"
    RAMAN_PIXEL_INTERPOLATION: Final = "raman_pixel_interpolation"


class AlignmentStrategy(_AbstractEnum):
    """Supported alignment strategy values in declaration order."""

    MANUAL: Final = "manual"
    PRE_ALIGNED: Final = "pre_aligned"


class ModalityType(_AbstractEnum):
    """Supported modality type values in declaration order."""

    MICROSCOPY_IMAGE: Final = "microscopy_image"
    MSI: Final = "msi"
    RAMAN: Final = "raman"
    ST: Final = "st"


class TransformationType(_AbstractEnum):
    """Supported transformation type values in declaration order."""

    TRANSLATION: Final = "translation"
    RIGID: Final = "rigid"
    AFFINE: Final = "affine"
    BSPLINE: Final = "bspline"


class MsiSampleType(_AbstractEnum):
    """MSI experiment type; calibrates foreground detection, its spatial cleanup and the clustering mode."""

    TISSUE: Final = "tissue"  # tissue section on a clean or empty background
    TISSUE_NOISY: Final = (
        "tissue_noisy"  # tissue section on a background rich in noise centroids
    )
    CELL_CULTURE: Final = (
        "cell_culture"  # uniform culture, domains that change along gradients
    )
    MICROGRID: Final = (
        "microgrid"  # isolated cells of about one pixel in a background matrix
    )


class MicroscopyImageType(_AbstractEnum):
    """Microscopy acquisition class; calibrates tissue detection, Z reduction, fill and storage."""

    AUTO: Final = "auto"  # read from the acquisition metadata
    BRIGHTFIELD: Final = "brightfield"  # transmitted light: H&E, IHC, special stains
    FLUORESCENCE: Final = "fluorescence"  # IF, confocal, multiplexed imaging


class MicroscopyZProjection(_AbstractEnum):
    """Reduction of a Z-stack to one plane."""

    AUTO: Final = "auto"  # max for fluorescence, best_focus for brightfield
    MAX: Final = "max"  # maximum-intensity projection
    BEST_FOCUS: Final = "best_focus"  # plane with the highest variance of the Laplacian
    MIDDLE: Final = "middle"  # middle plane


class SpotResolution(_AbstractEnum):
    """Spot modality resolution class; sets the unit aggregation of the alignment clustering."""

    STANDARD: Final = (
        "standard"  # multicellular spots, clustered natively: Visium, MALDI-MSI
    )
    SUB_CELLULAR: Final = "sub_cellular"  # weak spots, summed into blocks: Visium HD, Xenium-derived, high-res MSI


class StCapture(_AbstractEnum):
    """How an ST technology reads transcripts; calibrates the gene filter and normalization."""

    SEQUENCING: Final = "sequencing"  # Visium, Visium HD, Stereo-seq, Slide-seq
    IMAGING: Final = "imaging"  # Xenium, MERFISH, CosMx, seqFISH


class StUnit(_AbstractEnum):
    """Observation unit of an ST sample; calibrates quality control and clustering."""

    SPOT: Final = "spot"  # multicellular capture spots (Visium)
    BIN: Final = (
        "bin"  # sub-cellular square bins or beads (Visium HD, Stereo-seq, Slide-seq)
    )
    CELL: Final = "cell"  # segmented cells (Xenium, MERFISH, CosMx, Visium HD cells)


class StNormalization(_AbstractEnum):
    """Supported ST normalizations, applied once to the merged dataset."""

    AUTO: Final = "auto"
    NONE: Final = "none"
    LOG_NORMALIZE: Final = "log_normalize"
    CP10K_LOG: Final = "cp10k_log"
    SIZE_FACTORS: Final = "size_factors"
    AREA_LOG_NORMALIZE: Final = "area_log_normalize"


class MsiIntensityNormalization(_AbstractEnum):
    """MSI row normalizations; each divides a spectrum by one divisor at a fixed scale."""

    NONE: Final = "none"
    TIC: Final = "tic"  # total ion current, rows sum to the number of features
    MEDIAN: Final = "median"  # median of the nonzero features, set to 1
    RMS: Final = "rms"  # root mean square over the features, set to 1
    INTERNAL_STANDARD: Final = (
        "internal_standard"  # sprayed standard intensity, set to 1
    )


class RamanNormalization(_AbstractEnum):
    """Raman per-pixel normalizations, applied within one acquisition."""

    NONE: Final = "none"
    AREA: Final = "area"  # spectrum divided by its mean over the acquisition's channels


class DecompositionMethod(_AbstractEnum):
    """Supported decomposition method values in declaration order."""

    PCA: Final = "pca"
    NMF: Final = "nmf"
