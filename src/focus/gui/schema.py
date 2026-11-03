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
                MsiPreprocessingParams.FREQUENCY_THRESHOLD: {
                    "type": "float",
                    "default": 0.01,
                },
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
                MsiPreprocessingParams.DETECT_BACKGROUND: {
                    "type": "bool",
                    "default": False,
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
                RamanPreprocessingParams.FORCE_RECOMPUTING: {
                    "type": "bool",
                    "default": False,
                },
                RamanPreprocessingParams.MAX_WORKERS: {"type": "int", "default": 8},
                RamanPreprocessingParams.SAVGOL_WINDOW: {"type": "int", "default": 7},
                RamanPreprocessingParams.SAVGOL_POLYORDER: {
                    "type": "int",
                    "default": 3,
                },
                RamanPreprocessingParams.BG_MIN_AREA_FRACTION: {
                    "type": "float",
                    "default": 0.05,
                },
                RamanPreprocessingParams.OTSU_THRESHOLD_FACTOR: {
                    "type": "float",
                    "default": 0.7,
                },
                RamanPreprocessingParams.MIN_OBJECT_SIZE: {
                    "type": "int",
                    "default": 500,
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
            RegistrationType.SPOT_AGGREGATION: {
                "force_recomputing": {"type": "bool", "default": False},
            },
            RegistrationType.RAMAN_PIXEL_INTERPOLATION: {
                "force_recomputing": {"type": "bool", "default": False},
            },
            RegistrationType.NONE: {},
        },
    }
