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


class SpotResolution(_AbstractEnum):
    """Spot modality resolution class; selects the clustering preset (CLUSTERING_PRESETS)."""

    STANDARD: Final = "standard"  # multicellular spots: Visium, MALDI-MSI
    SUB_CELLULAR: Final = "sub_cellular"  # weak sub-cellular spots: Visium HD, Xenium-derived, high-res MSI


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
