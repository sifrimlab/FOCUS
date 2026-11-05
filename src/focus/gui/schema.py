"""Build the existing GUI configuration schema from shared option declarations."""

from focus.config.keys import (
    MicroscopyImageProcessingParams,
    MsiPreprocessingParams,
    RamanPreprocessingParams,
    STPreprocessingParams,
)
from focus.config.options import (
    MicroscopyImageType,
    MicroscopyZProjection,
    ModalityType,
    RegistrationType,
    AlignmentStrategy,
    SegmentationBackgroundColor,
    MsiIntensityNormalization,
    MsiSampleType,
    RamanNormalization,
    SpotResolution,
    StCapture,
    StNormalization,
    StUnit,
    AnnotationFileType,
)
from focus.config.catalog import (
    REGISTRATION_COMPATIBILITY,
    ALIGNMENT_STRATEGY_COMPATIBILITY,
    DISPLAY_NAMES,
)


def _build_schema() -> dict[str, object]:
    """Build the schema dict that drives all frontend dropdowns and forms."""
    return {
        "modality_types": ModalityType.list(),
        "registration_types": RegistrationType.list(),
        "registration_compatibility": {
            rt: (compat if compat is not None else None)
            for rt, compat in REGISTRATION_COMPATIBILITY.items()
        },
        "alignment_strategies": AlignmentStrategy.list(),
        "alignment_strategy_compatibility": {
            s: (compat if compat is not None else None)
            for s, compat in ALIGNMENT_STRATEGY_COMPATIBILITY.items()
        },
        "intensity_normalization": MsiIntensityNormalization.list(),
        "background_color": SegmentationBackgroundColor.list(),
        "processing_params": {
            ModalityType.MICROSCOPY_IMAGE: {
                MicroscopyImageProcessingParams.IMAGE_TYPE: {
                    "type": "enum",
                    "options": MicroscopyImageType.list(),
                    "default": MicroscopyImageType.AUTO,
                },
                MicroscopyImageProcessingParams.Z_PROJECTION: {
                    "type": "enum",
                    "options": MicroscopyZProjection.list(),
                    "default": MicroscopyZProjection.AUTO,
                },
                MicroscopyImageProcessingParams.CROP_TO_TISSUE: {
                    "type": "bool",
                    "default": False,
                },
                MicroscopyImageProcessingParams.REMOVE_BACKGROUND: {
                    "type": "bool",
                    "default": False,
                },
                MicroscopyImageProcessingParams.PIXEL_SIZE: {
                    "type": "float",
                    "default": None,
                    "nullable": True,
                },
                MicroscopyImageProcessingParams.FORCE_RECOMPUTING: {
                    "type": "bool",
                    "default": False,
                },
            },
            ModalityType.MSI: {
                MsiPreprocessingParams.LIPID_ANNOTATION_DB: {
                    "type": "path",
                    "default": None,
                    "nullable": True,
                },
                MsiPreprocessingParams.MASS_TOLERANCE: {"type": "float", "default": 10},
                MsiPreprocessingParams.INTENSITY_NORMALIZATION: {
                    "type": "enum",
                    "options": MsiIntensityNormalization.list(),
                    "default": MsiIntensityNormalization.NONE,
                },
                MsiPreprocessingParams.RECALIBRATION_REFERENCE: {
                    "type": "string",
                    "default": None,
                    "nullable": True,
                },
                MsiPreprocessingParams.INTERNAL_STANDARD_MZ: {
                    "type": "string",
                    "default": None,
                    "nullable": True,
                },
                MsiPreprocessingParams.SAMPLE_TYPE: {
                    "type": "enum",
                    "options": MsiSampleType.list(),
                    "default": MsiSampleType.TISSUE,
                },
                MsiPreprocessingParams.RESOLUTION: {
                    "type": "enum",
                    "options": SpotResolution.list(),
                    "default": SpotResolution.STANDARD,
                },
                MsiPreprocessingParams.FORCE_RECOMPUTING: {
                    "type": "bool",
                    "default": False,
                },
            },
            ModalityType.RAMAN: {
                RamanPreprocessingParams.DENOISE: {"type": "bool", "default": True},
                RamanPreprocessingParams.REMOVE_OFFSET: {
                    "type": "bool",
                    "default": False,
                },
                RamanPreprocessingParams.NORMALIZATION: {
                    "type": "enum",
                    "options": RamanNormalization.list(),
                    "default": RamanNormalization.NONE,
                },
                RamanPreprocessingParams.FORCE_RECOMPUTING: {
                    "type": "bool",
                    "default": False,
                },
            },
            ModalityType.ST: {
                STPreprocessingParams.CAPTURE: {
                    "type": "enum",
                    "options": StCapture.list(),
                    "default": StCapture.SEQUENCING,
                },
                STPreprocessingParams.UNIT: {
                    "type": "enum",
                    "options": StUnit.list(),
                    "default": StUnit.SPOT,
                },
                STPreprocessingParams.NORMALIZATION: {
                    "type": "enum",
                    "options": StNormalization.list(),
                    "default": StNormalization.AUTO,
                },
                STPreprocessingParams.QUALITY_CONTROL: {
                    "type": "bool",
                    "default": True,
                },
                STPreprocessingParams.REMOVE_CONTROLS_AND_EMPTY: {
                    "type": "bool",
                    "default": True,
                },
                STPreprocessingParams.REMOVE_MITOCHONDRIAL_GENES: {
                    "type": "bool",
                    "default": False,
                },
                STPreprocessingParams.FORCE_RECOMPUTING: {
                    "type": "bool",
                    "default": False,
                },
            },
        },
        "annotation_file_types": AnnotationFileType.list(),
        "display_names": DISPLAY_NAMES,
        "registration_params": {
            RegistrationType.FEATURE_EXTRACTION: {
                "force_recomputing": {"type": "bool", "default": False},
                "background_color": {
                    "type": "enum",
                    "options": SegmentationBackgroundColor.list(),
                    "default": SegmentationBackgroundColor.WHITE,
                },
            },
            RegistrationType.SPOT_INTERPOLATION: {
                "force_recomputing": {"type": "bool", "default": False},
            },
            RegistrationType.RAMAN_PIXEL_INTERPOLATION: {
                "force_recomputing": {"type": "bool", "default": False},
            },
            RegistrationType.PIXEL_COMPOSITION: {
                "force_recomputing": {"type": "bool", "default": False},
            },
            RegistrationType.NONE: {},
        },
    }
