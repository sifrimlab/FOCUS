import numpy as np
import SimpleITK as sitk
import os

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
            The output path for the computed transformation
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
        
        # Check if the output path exists, if not create the directory
        if not os.path.exists(path):
            os.makedirs(path)
        if not os.path.isdir(path):
            raise ValueError(f"Invalid output path. The path {path} is not a directory.")
        if not os.access(path, os.W_OK):
            raise PermissionError(f"Invalid output path. The path {path} is not writable.")
        
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
    
    def _prepare_transformation_settings(self, transformation_type: str) -> sitk.ParameterMap | None:
        '''
        Prepare Elastix transformation settings based on the selected transformation type.
        For Translation, no Elastix transformation is needed.
        
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
            transformation = None
        
        return transformation
    
    def _compute_origin_offset(self, fixed_image: sitk.Image, moving_image: sitk.Image, fixed_points: np.ndarray, moving_points: np.ndarray) -> np.ndarray:
        '''
        Compute the physical distance (offset) between the origins of the fixed and moving images based on the provided points.
        This method is used to estimate the initial guess about the moving image origin, since it is not known in advance
        and Elastix needs at least partial overlap between the two images to compute the transformation.

        Parameters
        ----------
        fixed_image : sitk.Image
            The fixed image to align to
        moving_image : sitk.Image
            The moving image to be aligned
        fixed_points : np.ndarray
            Fiducial landmarks in the fixed image, in pixel coordinates
            The shape of the array must be (n, 2).
        moving_points : np.ndarray
            Fiducial landmarks in the moving image, in pixel coordinates
            The shape of the array must be (n, 2).
        
        Returns
        ----------
        np.ndarray
            The offset between the origins of the fixed and moving images in physical coordinates
            The shape of the array is (2,).
        '''

        if isinstance(fixed_image, sitk.Image) == False or isinstance(moving_image, sitk.Image) == False:
            raise TypeError("Invalid image type. It must be a SimpleITK Image.")
        if type(fixed_points) != np.ndarray or type(moving_points) != np.ndarray:
            raise TypeError("Invalid points type. They must be numpy arrays.")
        if fixed_points.shape != moving_points.shape:
            raise ValueError("Invalid points shape. They must have the same shape.")
        if fixed_points.shape[1] != 2 or moving_points.shape[1] != 2:
            raise ValueError("Invalid points shape. They must be of shape (n, 2).")
        
        # Convert the pixel coordinates to physical coordinates using the current image metadata
        fixed_points_physical = self._pixel_to_physical(fixed_image, fixed_points)
        moving_points_physical = self._pixel_to_physical(moving_image, moving_points)

        # For each couple of fixed and moving points, calculate the difference in physical space
        offset = np.zeros_like(self.fixed_points)
        offset[:, 0] = moving_points_physical[:, 0] - fixed_points_physical[:, 0]
        offset[:, 1] = moving_points_physical[:, 1] - fixed_points_physical[:, 1]
        offset = np.mean(offset, axis=0)

        # Get the absolute value of the offset, rounded to integer
        offset = np.round(np.abs(offset)).astype(float)

        return offset
    
    def _rescale_translate(self, fixed_image: sitk.Image, moving_image: sitk.Image, offset: np.ndarray) -> sitk.Image:
        '''
        Rescale and translate the moving image to match the fixed image, based on the computed offset.

        Parameters
        ----------
        fixed_image : sitk.Image
            The fixed image to align to
        moving_image : sitk.Image
            The moving image to be aligned
        offset : np.ndarray
            The offset between the origins of the fixed and moving images in physical coordinates
            The shape of the array is (2,).
        
        Returns
        ----------
        sitk.Image
            The rescaled and translated moving image.
        '''

        if isinstance(fixed_image, sitk.Image) == False or isinstance(moving_image, sitk.Image) == False:
            raise TypeError("Invalid image type. It must be a SimpleITK Image.")
        if type(offset) != np.ndarray:
            raise TypeError("Invalid offset type. It must be a numpy array.")
        if offset.shape != (2,):
            raise ValueError("Invalid offset shape. It must be of shape (2,).")
        
        # Apply the offset to the moving image to update its origin
        moving_image.SetOrigin(tuple(offset))

        # Define a ResampleImageFilter to rescale the moving image to match the fixed image
        resample_filter = sitk.ResampleImageFilter()
        resample_filter.SetReferenceImage(fixed_image)                          # The output must match the size of the fixed image
        resample_filter.SetTransform(sitk.Transform(2, sitk.sitkIdentity))      # Identity transform: no change in the pixel values
        resample_filter.SetInterpolator(sitk.sitkNearestNeighbor)               # Nearest neighbor interpolation: no interpolation, should never be used
        resample_filter.SetDefaultPixelValue(-1.0)
        resample_filter.SetOutputPixelType(sitk.sitkFloat32)

        # Resample the moving image to match the fixed image
        resampled_moving_image: sitk.Image = resample_filter.Execute(moving_image)

        # Update the moving image's metadata to match the fixed image, since now they are matching
        resampled_moving_image.SetSpacing(fixed_image.GetSpacing())
        resampled_moving_image.SetOrigin(fixed_image.GetOrigin())

        return resampled_moving_image
    
    def _save_landmarks_to_file(self, landmarks: np.ndarray, filename: str) -> None:
        '''
        Save the landmarks to a file in the format required by Elastix.

        Parameters
        ----------
        landmarks : np.ndarray
            The landmarks to save
            The shape of the array must be (n, 2).
        filename : str
            The name of the file to save the landmarks to
        '''

        if type(landmarks) != np.ndarray:
            raise TypeError("Invalid landmarks type. It must be a numpy array.")
        if type(filename) != str:
            raise TypeError("Invalid filename type. It must be a string.")
        if landmarks.shape[1] != 2:
            raise ValueError("Invalid landmarks shape. It must be of shape (n, 2).")
        
        text = f'index\n{landmarks.shape[0]}\n'
        
        for (y, x) in landmarks:
            text = text + f'{y} {x}\n'
        
        with open(filename, "w") as file:
            file.write(text)

    def align_images(self, transformations: list[str], fixed_points: np.ndarray, moving_points: np.ndarray) -> np.ndarray:
        '''
        Align the moving image to the fixed image, using the given list of transformations in the order they're given.
        The fixed_points and moving_points are used to compute and train the transformation kernels.

        Parameters
        ----------
        transformations : list[str]
            The list of transformations to apply in the order they should be applied
        fixed_points : np.ndarray
            Fiducial landmarks in the fixed image, in pixel coordinates
            The shape of the array must be (n, 2).
        moving_points : np.ndarray
            Fiducial landmarks in the moving image, in pixel coordinates
            The shape of the array must be (n, 2).
        
        Returns
        ----------
        np.ndarray
            The aligned moving image.
        '''

        if type(transformations) != list:
            raise TypeError("Invalid transformations type. It must be a list.")
        if type(fixed_points) != np.ndarray or type(moving_points) != np.ndarray:
            raise TypeError("Invalid points type. They must be numpy arrays.")
        if fixed_points.shape != moving_points.shape:
            raise ValueError("Invalid points shape. They must have the same shape.")
        if fixed_points.shape[1] != 2 or moving_points.shape[1] != 2:
            raise ValueError("Invalid points shape. They must be of shape (n, 2).")
        
        # Elastix requires grayscale images to compute the transformation. Use the highest contrast channel
        if self.fixed_image.GetNumberOfComponentsPerPixel() > 1:
            fixed_channel = None
            fixed_array = sitk.GetArrayFromImage(self.fixed_image)

            for i in range(fixed_array.shape[2]):
                if fixed_channel is None or np.mean(fixed_array[:, :, i]) > np.mean(fixed_array[:, :, fixed_channel]):
                    fixed_channel = i

            # Use the selected channels to compute the transformations
            fixed_image_grayscale = sitk.VectorIndexSelectionCast(self.fixed_image, fixed_channel)
            del fixed_array
        else:
            fixed_image_grayscale = self.fixed_image
            fixed_channel = None

        if self.moving_image.GetNumberOfComponentsPerPixel() == 1:
            moving_channel = None
            moving_array = sitk.GetArrayFromImage(self.moving_image)

            for i in range(moving_array.shape[2]):
                if moving_channel is None or np.mean(moving_array[:, :, i]) > np.mean(moving_array[:, :, moving_channel]):
                    moving_channel = i

            # Use the selected channels to compute the transformations
            moving_image_grayscale = sitk.VectorIndexSelectionCast(self.moving_image, moving_channel)
            del moving_array
        else:
            moving_image_grayscale = self.moving_image
            moving_channel = None

        # Compute the initial offset between the fixed and moving images
        offset = self._compute_origin_offset(fixed_image_grayscale, moving_image_grayscale, fixed_points, moving_points)

        # Rescale and translate the moving image to match the fixed image
        moving_image_grayscale = self._rescale_translate(fixed_image_grayscale, moving_image_grayscale, offset)

        # Initialize the Elastix engine
        elastixImageFilter = sitk.ElastixImageFilter()
        elastixImageFilter.LogToFileOff()
        
        # Initialize the transformations
        transformationVector = sitk.VectorOfParameterMap()
        for transformation in transformations:
            transformation_settings = self._prepare_transformation_settings(transformation)
            if transformation_settings is not None:
                transformationVector.append(transformation_settings)

        # Save the landmarks to file
        fixed_landmarks_file = os.path.join(self.path, self.sample_id, "fixed_landmarks.txt")
        moving_landmarks_file = os.path.join(self.path, self.sample_id, "moving_landmarks.txt")
        self._save_landmarks_to_file(self._pixel_to_physical(fixed_points), fixed_landmarks_file)
        self._save_landmarks_to_file(self._pixel_to_physical(moving_points), moving_landmarks_file)
        elastixImageFilter.SetFixedPointSetFileName(fixed_landmarks_file)
        elastixImageFilter.SetMovingPointSetFileName(moving_landmarks_file)

        # Set the images and the transformations
        elastixImageFilter.SetFixedImage(fixed_image_grayscale)
        elastixImageFilter.SetMovingImage(moving_image_grayscale)
        elastixImageFilter.SetParameterMap(transformationVector)
        elastixImageFilter.SetOutputDirectory(self.path)
        
        # Compute the transformation parameters
        elastixImageFilter.Execute()

        # Get the transformation parameters
        transformix_filter = sitk.TransformixImageFilter()
        computed_parameters_map = elastixImageFilter.GetTransformParameterMap()
        transformix_filter.SetTransformParameterMap(computed_parameters_map)

        # Now that the transformation is computed, apply it to each channel of the moving image
        output_image = np.zeros((self.moving_image.GetSize()[1], self.moving_image.GetSize()[0], self.moving_image.GetNumberOfComponentsPerPixel()), dtype = np.float32)

        for index in range(output_image.shape[2]):
            # Obtain the channel
            moving_image_channel = sitk.VectorIndexSelectionCast(self.moving_image, index)

            # Rescale the channel using the offset computed before
            moving_image_channel = self._rescale_translate(fixed_image_grayscale, moving_image_channel, offset)

            # Apply the transformation to the channel
            moving_image_channel = transformix_filter.Execute(moving_image_channel)
            moving_image_channel = sitk.GetArrayFromImage(moving_image_channel)

            # Store the transformed channel
            output_image[:, :, index] = moving_image_channel

        # Save the transformation parameters
        for index, param_map in enumerate(computed_parameters_map):
            sitk.WriteParameterFile(param_map, os.path.join(self.path, self.sample_id, f"TransformParameters_{index}.txt"))

        return output_image

