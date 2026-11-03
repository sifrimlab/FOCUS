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
    SPOT_AGGREGATION: Final = "spot_aggregation"
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
    """Supported msi sample type values in declaration order."""

    TISSUE: Final = "tissue"
    MICROGRID: Final = "microgrid"


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
    """Spot modality resolution class; selects the clustering preset (CLUSTERING_PRESETS)."""

    STANDARD: Final = "standard"  # multicellular spots: Visium, MALDI-MSI
    SUB_CELLULAR: Final = "sub_cellular"  # weak sub-cellular spots: Visium HD, Xenium-derived, high-res MSI


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
    """Supported msi intensity normalization values in declaration order."""

    TIC: Final = "tic"
    LOG: Final = "log"
    CLR: Final = "clr"
    TIC_MEAN_SCALED: Final = "tic_mean_scaled"
    MEDIAN: Final = "median"
    INTERNAL_STANDARD: Final = "internal_standard"
    NONE: Final = "none"


class DecompositionMethod(_AbstractEnum):
    """Supported decomposition method values in declaration order."""

    PCA: Final = "pca"
    NMF: Final = "nmf"
