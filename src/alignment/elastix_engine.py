import numpy as np
import SimpleITK as sitk

from constants import TransformationType

class ElastixEngine:
    '''
    Handler for the alignment transformation using the elastix library.
    '''

    def __init__(self, path: str, moving_image: np.ndarray, fixed_image: np.ndarray, moving_spacing: tuple[float, float], fixed_spacing: tuple[float, float]) -> None:
        '''
        Initialize the ElastixEngine class.

        Parameters
        ----------
        path : str
            The path to the elastix executable
        moving_image : np.ndarray
            The moving image to be aligned
        fixed_image : np.ndarray
            The fixed image to align to
        moving_spacing : tuple[float, float]
            The spacing of the moving image in µm
        fixed_spacing : tuple[float, float]
            The spacing of the fixed image in µm
        '''

        if type(path) != str or type(moving_image) != np.ndarray or type(fixed_image) != np.ndarray or type(moving_spacing) != tuple or type(fixed_spacing) != tuple:
            raise TypeError("Invalid input types. Please check the input types.")
        if len(moving_spacing) != 2 or len(fixed_spacing) != 2:
            raise ValueError("Invalid spacing. They must be a tuple of two floats.")
        
        self.path = path
        self.sample_id = path.split('/')[-2]

        # Convert the input images to Elastix format
        self.moving_image: sitk.Image = sitk.GetImageFromArray(moving_image, isVector = True)
        self.fixed_image: sitk.Image = sitk.GetImageFromArray(fixed_image, isVector = True)

        # Apply the spacing
        self.moving_image.SetSpacing(moving_spacing)
        self.fixed_image.SetSpacing(fixed_spacing)

        # The origin will be computed later
        self.moving_image.SetOrigin((0, 0))
        self.fixed_image.SetOrigin((0, 0))

        # The direction follows the way matplotlib reads the images (h, w) = (y, x) with y inverted (top-down)
        self.moving_image.SetDirection((0, -1, 1, 0))
        self.fixed_image.SetDirection((0, -1, 1, 0))

    def _pixel_to_physical(self, image: sitk.Image, pixel_coords: np.ndarray) -> np.ndarray:
        '''
        Convert a set of pixel coordinates to physical coordinates based on the image's spacing, origin and direction.

        Parameters
        ----------
        image : sitk.Image
            The image to use for the conversion
        pixel_coords : np.ndarray
            The pixel coordinates to convert
            The shape of the array must be (n, 2) where n is the number of coordinates and 2 is the number of dimensions (x, y)
        
        Returns
        ----------
        np.ndarray
            The physical coordinates of the input pixel coordinates
            The shape of the array is (n, 2) where n is the number of coordinates and 2 is the number of dimensions (x, y)
        '''
        return np.array([image.TransformIndexToPhysicalPoint([int(x), int(y)]) for x, y in pixel_coords])
    
    def _prepare_transformation_settings(self, transformation_type: str) -> sitk.ParameterMap:
        '''
        Prepare Elastix transformation settings based on the selected transformation type.
        
        Parameters
        ----------
        transformation_type : str
            The type of transformation to apply.

        Returns
        ----------
        sitk.ParameterMap
            The parameter map for the selected transformation type.
        '''

        if type(transformation_type) != str:
            raise TypeError("Invalid transformation type. It must be a string.")
        
        if transformation_type not in TransformationType.list():
            raise ValueError(f"Invalid transformation type. It must be one of {TransformationType.list()}.")
        
        # Translation is a special transformation used only to make the initial guess about moving image origin
        if transformation_type != TransformationType.TRANSLATION:
            transformation: sitk.ParameterMap = sitk.GetDefaultParameterMap(transformation_type)
            transformation['DefaultPixelValue'] = ['0.0']
            transformation['ResampleInterpolator'] = ['FinalLinearInterpolator']
            transformation['Registration'] = ['MultiMetricMultiResolutionRegistration']
            transformation['AutomaticTransformInitialization'] = ['true']
            transformation['AutomaticTransformInitializationMethod'] = ['CenterOfGravityAlign']
            transformation['AutomaticScalesEstimation'] = ['true']

            if transformation_type == TransformationType.RIGID:
                transformation['Metric'] = ["CorrespondingPointsEuclideanDistanceMetric"]
                transformation['Metric0Weight'] = ['1.0']
            elif transformation_type == TransformationType.AFFINE:
                transformation['Metric'] = ["AdvancedMattesMutualInformation", "CorrespondingPointsEuclideanDistanceMetric"]
                transformation['Metric0Weight'] = ['1.0']
                transformation['Metric1Weight'] = ['1.0']
            elif transformation_type == TransformationType.BSPLINE:
                raise NotImplementedError("BSpline transformation is not implemented yet.")
        else:
            raise NotImplementedError("Translation transformation is not implemented yet.")
        
        return transformation
