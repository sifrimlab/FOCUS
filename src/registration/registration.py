from abc import ABC
from typing import List, final
from fnmatch import fnmatch
import numpy as np
import SimpleITK as sitk
import tkinter as tk
import os
import matplotlib.pyplot as plt
    
from .registration_fiducial_tool import RegistrationFiducialTool


def change_path_initial_transform(path):
    for file in list(filter(lambda x: x.endswith(".1.txt"), os.listdir(path))):
        with open(f"{path}/{file}", "r") as f:
            lines = f.readlines()
            new_lines = []
            for line in lines:
                if line.startswith("(InitialTransformParametersFileName"):
                    new_lines.append(f"(InitialTransformParametersFileName \"{path}/{file.replace('.1.txt', '.0.txt')}\")\n")
                else:
                    new_lines.append(line)
        with open(f"{path}/{file}", "w") as f:
            f.writelines(new_lines)

class Registration(ABC):
    
    def __init__(self, image1: np.ndarray, image2: np.ndarray, spacing1: tuple=(1, 1), spacing2: tuple=(1, 1), path: str=None) -> None:
        
        super().__init__()

        # Ensure that the images are UINT8
        if image1.dtype != np.uint8:
            image1 = (image1 - np.min(image1)) / (np.max(image1) - np.min(image1)) * 255
            image1 = image1.astype(np.uint8)

        if image2.dtype != np.uint8:
            image2 = (image2 - np.min(image2)) / (np.max(image2) - np.min(image2)) * 255
            image2 = image2.astype(np.uint8)

        # Rescale the image2 to the same size as image1

        
        self.path = path if path is not None else os.getcwd()
        if False:
        #if os.path.exists(self.path) and os.path.isdir(self.path) and os.path.exists(f'{self.path}/moving_image.nii') and os.path.exists(f'{self.path}/fixed_image.nii'):
            self._fixed_image = sitk.ReadImage(f'{self.path}/fixed_image.nii')
            self._moving_image = sitk.ReadImage(f'{self.path}/moving_image.nii')
            self.spacing1 = self._fixed_image.GetSpacing()
            self.spacing2 = self._moving_image.GetSpacing()
        else:
            self.image1 = image1
            self.image2 = image2
            
            self._fixed_image = sitk.GetImageFromArray(self.image1, isVector = True)
            self._moving_image = sitk.GetImageFromArray(self.image2, isVector = True)
            
            self._fixed_image.SetSpacing(spacing = spacing1)
            self._moving_image.SetSpacing(spacing = spacing2)
            self._fixed_image.SetOrigin(origin = (0, 0))
            self._moving_image.SetOrigin(origin = (0, 0))
            self._fixed_image.SetDirection(direction = [0, 1, 1, 0])
            self._moving_image.SetDirection(direction = [0, 1, 1, 0])
            self.spacing1 = self._fixed_image.GetSpacing()
            self.spacing2 = self._moving_image.GetSpacing()

            os.makedirs(f'{self.path}', exist_ok=True)
            sitk.WriteImage(self._fixed_image, f'{self.path}/fixed_image.nii')
            sitk.WriteImage(self._moving_image, f'{self.path}/moving_image.nii')
            print("Manual input required: Choose some landmarks.")
            
        self.transform_parameter_map = None
        self._error_measure = 'AdvancedMeanSquares'

    def pixel_to_physical(self, image: sitk.Image, pixel_coords: np.ndarray) -> np.ndarray:
        return np.array([image.TransformIndexToPhysicalPoint([int(y), int(x)]) for x, y in pixel_coords])

    def create_fixed_mask(self, moving_img: sitk.Image, fixed_img: sitk.Image) -> sitk.Image:
        # 1) Create a “ones” mask in moving‐image space
        moving_mask = sitk.Image(moving_img.GetSize(), sitk.sitkUInt8)
        moving_mask.SetOrigin(moving_img.GetOrigin())
        moving_mask.SetSpacing(moving_img.GetSpacing())
        moving_mask.SetDirection(moving_img.GetDirection())
        moving_mask = moving_mask + 1  # fill with value 1

        # 2) Resample that mask into the fixed‐image grid
        resampler = sitk.ResampleImageFilter()
        resampler.SetReferenceImage(fixed_img)
        resampler.SetTransform(sitk.Transform(2, sitk.sitkIdentity))
        resampler.SetInterpolator(sitk.sitkNearestNeighbor)
        resampler.SetDefaultPixelValue(0)               # outside → 0
        resampler.SetOutputPixelType(sitk.sitkUInt8)
        fixed_overlap_mask = resampler.Execute(moving_mask)

        fixed_overlap_mask.SetOrigin(moving_img.GetOrigin())
        fixed_overlap_mask.SetSpacing(moving_img.GetSpacing())
        fixed_overlap_mask.SetDirection(moving_img.GetDirection())
        
        return fixed_overlap_mask

    
    @final
    def set_error_measure(self, s: str):
        """ Set error measure of the registration procedure. Default: 'AdvancedMeanSquares'

        Args:
            s (str): Options are: 'AdvancedMeanSquares', 'AdvancedMattesMutualInformation'
        """
        error_measures = set(['AdvancedMeanSquares', 'AdvancedMattesMutualInformation'])
        if s not in error_measures:
            raise Exception("Set a valid error measure! Options are: 'AdvancedMeanSquares', 'AdvancedMattesMutualInformation'")
        self._error_measure = s
            
    @final
    def get_fixed_image(self):
        return sitk.GetArrayFromImage(self._fixed_image)
    
    @final
    def get_moving_image(self):
        return sitk.GetArrayFromImage(self._moving_image)
    
    @final
    def get_sitk_fixed_image(self):
        return self._fixed_image
    
    @final
    def get_sitk_moving_image(self):
        return self._moving_image
    
    @final
    def get_points_callback(self, points: List[List[int]]):
        self.fixed_points = np.zeros((len(points[0]), 2), dtype=np.float32)
        self.moving_points = np.zeros((len(points[0]), 2), dtype=np.float32)

        for index in range(len(points[0])):
            self.fixed_points[index, 0] = points[0][index][1]
            self.fixed_points[index, 1] = points[0][index][0]
            self.moving_points[index, 0] = points[1][index][1]
            self.moving_points[index, 1] = points[1][index][0]


        
    @final
    def compute_transformation(self, only_affine: bool=False, only_fiducials: bool=False) -> np.ndarray:
        self.fixed_points = []
        
        files = list(filter(lambda x: fnmatch(x, "TransformParameters.*.txt"), os.listdir(f'{self.path}')))
        
        '''
        if os.path.exists(f'{self.path}') and len(files) > 0:
            
            if len(files) > 1 and only_affine:
                for file in files:
                    os.remove(f'{self.path}/{file}')
                os.rmdir(f'{self.path}')
            elif len(files) == 1 and not only_affine:
                for file in files:
                    os.remove(f'{self.path}/{file}')
                os.rmdir(f'{self.path}')
            else:
                change_path_initial_transform(self.path)
                self.transform_parameter_map = tuple([sitk.ReadParameterFile(f'{self.path}/{file}') for file in files])
                return sitk.GetArrayFromImage(self.apply_transformation_sitk(self._moving_image))
        '''

        root = tk.Tk()
        app = RegistrationFiducialTool(self.get_fixed_image(), self.get_moving_image(), self.get_points_callback, master=root, size_markers=30)
        app.mainloop()
        
        if len(self.fixed_points) > 0:

            #self.fixed_points = self.pixel_to_physical(self._fixed_image, self.fixed_points)
            #self.moving_points = self.pixel_to_physical(self._moving_image, self.moving_points)

            self.fixed_points_file = f'{self.path}/fixed_points.pts'
            self.moving_points_file = f'{self.path}/moving_points.pts'
            self._create_pts_file(self.fixed_points_file, self.fixed_points)
            self._create_pts_file(self.moving_points_file, self.moving_points)
        
        return self._compute_transformation(only_affine, only_fiducials)
    
    @final
    def apply_transformation(self, image: np.ndarray) -> np.ndarray:
        if self.transform_parameter_map is None:
            raise Exception('You first have to compute a transform before you can apply it!')
        
        image = sitk.GetImageFromArray(image)
        image.SetSpacing(self.spacing2)
        
        transform = sitk.TransformixImageFilter()
        transform.LogToConsoleOff()
        transform.SetTransformParameterMap(self.transform_parameter_map)
        transform.SetMovingImage(image)
        transform.Execute()
        
        # plot_opacity_slider(self.get_fixed_image(), sitk.GetArrayFromImage(transform.GetResultImage()))
        return sitk.GetArrayFromImage(transform.GetResultImage())
    
    @final
    def apply_transformation_sitk(self, image: sitk.Image) -> np.ndarray:
        if self.transform_parameter_map is None:
            raise Exception('You first have to compute a transform before you can apply it!')
        
        transform = sitk.TransformixImageFilter()
        transform.LogToConsoleOff()
        transform.SetTransformParameterMap(self.transform_parameter_map)
        transform.SetMovingImage(image)
        transform.Execute()
        
        
        return transform.GetResultImage()
    
    @final
    def _compute_transformation(self, only_affine: bool, only_fiducials: bool) -> np.ndarray:
        
        elastixImageFilter = sitk.ElastixImageFilter()
        elastixImageFilter.LogToConsoleOff()
        elastixImageFilter.LogToFileOff()

        # Convert the images to Grayscale
        if self._fixed_image.GetNumberOfComponentsPerPixel() > 1:
            self._fixed_image = sitk.VectorIndexSelectionCast(self._fixed_image, 0)
        if self._moving_image.GetNumberOfComponentsPerPixel() > 1:
            self._moving_image = sitk.VectorIndexSelectionCast(self._moving_image, 0)

        elastixImageFilter.SetFixedImage(self._fixed_image)
        #elastixImageFilter.SetMovingImage(self._moving_image)

        physical_moving_points = self.pixel_to_physical(self._moving_image, self.moving_points)
        physical_fixed_points = self.pixel_to_physical(self._fixed_image, self.fixed_points)

        # For each couple of fixed and moving points, calculate the difference in physical space
        offset = np.zeros_like(self.fixed_points)
        offset[:, 0] = physical_moving_points[:, 0] - physical_fixed_points[:, 0]
        offset[:, 1] = physical_moving_points[:, 1] - physical_fixed_points[:, 1]
        offset = np.mean(offset, axis=0)

        # Get the absolute value of the offset, rounded to integer
        offset = np.round(np.abs(offset)).astype(int)

        # Apply the offset to moving image as Origin
        self._moving_image.SetOrigin(tuple(offset.astype(float)))

        print(f"Offset: {offset}")  
        physical_moving_points = self.pixel_to_physical(self._moving_image, self.moving_points)
        physical_fixed_points = self.pixel_to_physical(self._fixed_image, self.fixed_points)
        self.fixed_points_file = f'{self.path}/fixed_points.pts'
        self.moving_points_file = f'{self.path}/moving_points.pts'
        self._create_pts_file(self.fixed_points_file, physical_fixed_points)
        self._create_pts_file(self.moving_points_file, physical_moving_points)

        image_dim = self._fixed_image.GetDimension()

        resampler = sitk.ResampleImageFilter()
        resampler.SetReferenceImage(self._fixed_image)
        resampler.SetTransform(sitk.Transform(2, sitk.sitkIdentity))
        resampler.SetInterpolator(sitk.sitkNearestNeighbor)
        resampler.SetDefaultPixelValue(0)               # outside → 0
        resampler.SetOutputPixelType(sitk.sitkUInt8)
        test = resampler.Execute(self._moving_image)

        #plot_opacity_slider(sitk.GetArrayFromImage(test), self.get_fixed_image())

        test.SetOrigin(self._fixed_image.GetOrigin())
        test.SetSpacing(self._fixed_image.GetSpacing())
        test.SetDirection(self._fixed_image.GetDirection())
        elastixImageFilter.SetMovingImage(test)
        
        parameterMapVector = sitk.VectorOfParameterMap()
        affine: sitk.ParameterMap = sitk.GetDefaultParameterMap("rigid", image_dim, 1)
        affine['DefaultPixelValue'] = ['0.0']
        affine['ResampleInterpolator'] = ['FinalLinearInterpolator']
        affine['Registration'] = ['MultiMetricMultiResolutionRegistration']
        affine['AutomaticTransformInitialization'] = ['true']
        affine['AutomaticTransformInitializationMethod'] = ['CenterOfGravityAlign']
        affine['AutomaticScalesEstimation'] = ['true']

        if not only_affine:
            bspline: sitk.ParameterMap = sitk.GetDefaultParameterMap("bspline", 4, np.float64(np.min([int(self._fixed_image.GetSize()[0] / 16), int(self._fixed_image.GetSize()[0] / 16)])))
            bspline['DefaultPixelValue'] = ['0']
            bspline['ResampleInterpolator'] = ['FinalLinearInterpolator']
            bspline['Registration'] = ['MultiMetricMultiResolutionRegistration']

        if len(self.fixed_points) > 0:      
            elastixImageFilter.SetFixedPointSetFileName(self.fixed_points_file)
            elastixImageFilter.SetMovingPointSetFileName(self.moving_points_file)

            affine['Metric'] = [self._error_measure, 'CorrespondingPointsEuclideanDistanceMetric']
            affine['Metric0Weight'] = ['0.0']
            if not only_affine:
                affine['Metric0Weight'] = ['1.0']
                          
            affine['Metric1Weight'] = ['1.0']
            if not only_affine:
                bspline['Metric'] = [self._error_measure, 'TransformBendingEnergyPenalty', 'CorrespondingPointsEuclideanDistanceMetric']
                bspline['Metric0Weight'] = ['1.0']
                bspline['Metric1Weight'] = ['1.0']
                bspline['Metric2Weight'] = ['0.0']
        
        else:
            affine['Metric'] = [self._error_measure]
            affine['Metric0Weight'] = ['1.0']
            if not only_affine:
                bspline['Metric'] = [self._error_measure, 'TransformBendingEnergyPenalty']
                bspline['Metric0Weight'] = ['1.0']
                bspline['Metric1Weight'] = ['1.0']
        
        if only_fiducials:
            affine['Metric0Weight'] = ['0.0']
            affine['Metric1Weight'] = ['1.0']
            
            if not only_affine:
                bspline['Metric'] = [self._error_measure, 'TransformBendingEnergyPenalty', 'CorrespondingPointsEuclideanDistanceMetric']
                bspline['Metric0Weight'] = ['1.0']
                bspline['Metric1Weight'] = ['1.0']
                bspline['Metric2Weight'] = ['1.0']

        parameterMapVector.append(affine)
        if not only_affine:
            parameterMapVector.append(bspline)
        elastixImageFilter.SetParameterMap(parameterMapVector)
        
        os.makedirs(f'{self.path}/', exist_ok=True)
        elastixImageFilter.SetOutputDirectory(f'{self.path}/')
        elastixImageFilter.LogToConsoleOn()
        elastixImageFilter.LogToFileOn()
        #elastixImageFilter.SetFixedMask(self.create_fixed_mask(self._fixed_image, self._fixed_image))
        #elastixImageFilter.SetMovingMask(self.create_fixed_mask(self._moving_image, self._fixed_image))

        try:
            elastixImageFilter.Execute()
        except Exception as e:
            print(f"Error during registration: {e}")
            raise e
        
        # Grab the first (and only) transform
        tx_map = elastixImageFilter.GetTransformParameterMap()[0]

        # These are the six parameters of a 2D affine:
        #   [ a, b, c, d, tx, ty ]
        # where the 2×2 matrix [a b; c d] handles rotation/scale, 
        # and (tx, ty) is the translation in physical units.
        params = list(map(float, tx_map["TransformParameters"]))
        print("Affine parameters:", params)

        # Extract translation and convert to pixels:
        #spacing = self._fixed_image.GetSpacing()
        #tx_mm, ty_mm = params[4], params[5]
        #tx_px = tx_mm / spacing[0]
        #ty_px = ty_mm / spacing[1]
        #print(f"Translation = ({tx_mm:.2f} mm, {ty_mm:.2f} mm) ≃ ({tx_px:.1f}, {ty_px:.1f}) pixels")

        self.transform_parameter_map = elastixImageFilter.GetTransformParameterMap()
        
        result = sitk.GetArrayFromImage(elastixImageFilter.GetResultImage())

        
        if len(self.fixed_points) > 0:
            os.remove(self.fixed_points_file)
            os.remove(self.moving_points_file)
        
       
        
        return result
    
    @final
    def compute_deformation_field(self) -> np.ndarray:
        if self.transform_parameter_map is None:
            raise Exception('You first have to compute a transform before you can get the deformation field!')
        
        transform = sitk.TransformixImageFilter()
        transform.LogToConsoleOff()
        transform.SetTransformParameterMap(self.transform_parameter_map)
        transform.SetMovingImage(self._moving_image)
        transform.ComputeDeformationFieldOn()
        transform.Execute()
        
        return transform.GetDeformationField()
        
    @final
    def _create_pts_file(self, filename, points):
        text = f'index\n{len(points)}\n'
        
        for (y, x) in points:
            text += f'{y} {x}\n'
        
        with open(filename, "w") as file:
            file.write(text)
    

    def physical_bounds(self, image):
        origin = np.array(image.GetOrigin())
        spacing = np.array(image.GetSpacing())
        size = np.array(image.GetSize())
        direction = np.array(image.GetDirection()).reshape((len(spacing), -1))
        extent = origin + direction @ (spacing * size)
        return origin, extent
    
class AffineRegistrationWithoutFiducials(ABC):
    
    def __init__(self, image1: np.ndarray, image2: np.ndarray, plot: bool=False) -> None:
        super().__init__()
        
        self.image1 = image1
        self.image2 = image2
        
        self._fixed_image, self._moving_image = self._preprocess_images()
        self.transform_parameter_map = None
        self._error_measure = 'AdvancedMeanSquares'
        self.plot = plot
        
    def _preprocess_images(self) -> tuple[sitk.Image]:
        fixed_image = sitk.GetImageFromArray(self.image1)
        moving_image = sitk.GetImageFromArray(self.image2)
        
        return fixed_image, moving_image
            
    @final
    def get_fixed_image(self):
        return sitk.GetArrayFromImage(self._fixed_image)
    
    @final
    def get_moving_image(self):
        return sitk.GetArrayFromImage(self._moving_image)
    
    @final
    def set_fixed_image(self):
        return sitk.GetImageFromArray(self._fixed_image)
    
    @final
    def set_moving_image(self):
        return sitk.GetImageFromArray(self._moving_image)
    
    @final
    def compute_transformation(self) -> np.ndarray:
        
        elastixImageFilter = sitk.ElastixImageFilter()
        elastixImageFilter.LogToConsoleOff()
        elastixImageFilter.SetFixedImage(self._fixed_image)
        elastixImageFilter.SetMovingImage(self._moving_image)
        
        affine: sitk.ParameterMap = sitk.GetDefaultParameterMap("translation")
        affine['DefaultPixelValue'] = ['0.0']
        affine['ResampleInterpolator'] = ['FinalLinearInterpolator']
        affine['Registration'] = ['MultiMetricMultiResolutionRegistration']
        affine['Metric'] = [self._error_measure]
        affine['Metric0Weight'] = ['1.0']

        elastixImageFilter.SetParameterMap(affine)

        elastixImageFilter.Execute()
        
        self.transform = sitk.TransformixImageFilter()
        self.transform_parameter_map = elastixImageFilter.GetTransformParameterMap()
        
        result = sitk.GetArrayFromImage(elastixImageFilter.GetResultImage())
        
        
        return result
    
   
if __name__ == "__main__":
        
    import os
    
    sample = "00103993-1"
    path = f"./data/registration"
    
    reg = Registration(None, None, None, None, f"{path}/{sample}/maldi")
    reg.set_error_measure('AdvancedMattesMutualInformation')
    reg.compute_transformation(only_affine=True, only_fiducials=False)

    reg = Registration(None, None, None, None, f"{path}/{sample}/raman")
    reg.set_error_measure('AdvancedMattesMutualInformation')
    reg.compute_transformation(only_affine=False, only_fiducials=False)
    
