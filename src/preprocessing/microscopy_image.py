import copy, os, tifffile, cv2
import numpy as np
import matplotlib.pyplot as plt
import skimage.io as skio
import skimage.exposure
import constants as constants


def get_image_from_bounding_box(image: np.ndarray, x: int, y: int, w: int, h: int, offset: int = 10) -> np.ndarray:
    """
    Get a cropped image from the bounding box.
    """
    return image[np.max([y - offset, 0]): np.min([y + h + offset, image.shape[0]]), np.max([x - offset, 0]): np.min([x + w + offset, image.shape[1]]), :]

def enhance_contrast(channel: np.ndarray, saturated_pixels: float = 0.35) -> np.ndarray:
    '''
    Enhance the contrast of a single channel image by stretching the histogram.
    Add a small amount of saturated pixels to improve the contrast.

    Parameters
    ----------
    channel : np.ndarray[np.uint8]
        The channel to enhance.
    saturated_pixels : float
        The amount of saturated pixels to add. Default is 0.35%.
    '''

    # Convert to float32
    channel = channel.astype(np.float32)

    mask = channel > 0
    result = np.zeros_like(channel, dtype=np.float32)

    if np.any(mask):
        # Compute the pixels to saturate
        p_low, p_high = np.percentile(channel[mask], (saturated_pixels, 100 - saturated_pixels))

        # Stretch the histogram
        rescaled_channel = np.clip(channel[mask], p_low, p_high)

        result[mask] = (rescaled_channel - p_low) / (p_high - p_low)

    return result

def gamma_correction(channel: np.ndarray, gamma: float = 0.45) -> np.ndarray:
    '''
    Apply gamma correction to a single channel image.

    Parameters
    ----------
    image : np.ndarray[np.uint8]
        The image to correct.
    gamma : float
        The gamma value to use. Default is 0.45.
    '''

    channel = channel.astype(np.float32)
    channel = np.power(channel, gamma)
    return channel

def read_tiff_file(file: str) -> tuple[np.ndarray, float, float]:
    '''
    Read a tiff/tif file and return the image with color channel always in the last dimension
    (swap channels if needed) and the physical pixel coverage in µm.
    If no metadata is found to determine the physical pixel coverage, it will return 1.0µm for both dimensions.
    The image is converted to float32 and normalized to 0-1.

    Parameters
    ----------
    file : str
        The path to the tiff/tif file.
    
    Returns
    ----------
    image : np.ndarray
        The image with color channel always in the last dimension.
    physical_size_x : float
        The physical pixel coverage in µm.
    physical_size_y : float
        The physical pixel coverage in µm.
    '''

    # Check if the file exists and is a tiff/tif file
    if not os.path.isfile(file) or not (file.endswith(".tiff") or file.endswith(".tif")):
        raise ValueError(f"The file {file} does not exist or is not a tiff/tif file.")

    image, physical_size_x, physical_size_y = None, None, None

    # Read the tiff/tif file
    with tifffile.TiffFile(file) as f:
        # Get the image data
        image = f.asarray()

        # Determine the channel index by looking for the smallest dimension and place it last
        # Skip if it's a grayscale image
        if len(image.shape) > 2:
            channel_index = np.argmin(image.shape)
            if channel_index == 0:
                image = image.transpose(1, 2, 0)
            elif channel_index == 1:
                image = image.transpose(0, 2, 1)
        
        # Get the physical pixel coverage
        physical_size_x, physical_size_y = None, None
        try:
            metadata = f.shaped_metadata[0]
            physical_size_x = metadata['PhysicalSizeX']
            physical_size_y = metadata['PhysicalSizeY']
        except:
            pass

        if physical_size_x is None or physical_size_y is None:
            try:
                physical_size_x = str(f.pages[0].tags['XResolution'].value[0] / f.pages[0].tags['XResolution'].value[1] ** 2)
                physical_size_y = str(f.pages[0].tags['YResolution'].value[0] / f.pages[0].tags['YResolution'].value[1] ** 2)

                # Convert to um
                physical_size_x = float(physical_size_x) * 1e6
                physical_size_y = float(physical_size_y) * 1e6
            except:
                physical_size_x = 1.0
                physical_size_y = 1.0

    # Convert the image to float32
    image = image.astype(np.float32)

    # Normalize the image to 0-1
    image = image / np.max(image)

    return image, physical_size_x, physical_size_y

def preprocess_microscopy_image(path: str, crop: bool, filter_strength: str, smoothing: bool, color_enhancement: bool, debug_mode: bool = False) -> tuple[float, float]:
    '''
    Preprocess a microscopy image by applying cropping, filtering, smoothing and color enhancement.

    Parameters
    ----------
    path : str
        The path to the directory where the source data are stored.
    crop : bool
        Whether to crop the image or not.
    filter_strength : str
        The strength of the filter to apply. Can be 'soft', 'medium' or 'strong'.
    smoothing : bool
        Whether to apply smoothing or not.
    color_enhancement : bool
        Whether to apply color enhancement or not.
    debug_mode : bool
        Whether to enable debug mode or not. Default is False.
        If True, the function will plot each step of the preprocessing.

    Returns
    ----------
    tuple[float, float]
        A tuple containing the physical pixel coverage in µm for the x and y dimensions.
    '''

    # Check the input parameters
    if type(path) != str or type(crop) != bool or type(filter_strength) != str or type(smoothing) != bool or type(color_enhancement) != bool:
        raise TypeError("Invalid input parameters. Please check the types.")
    if filter_strength not in constants.ImagingFilterStrength.list():
        raise ValueError(f"Invalid filter strength: {filter_strength}. Please choose from {constants.ImagingFilterStrength.list()}.")
    
    # Check if the path exists
    if not os.path.exists(path):
        raise ValueError(f"The path {path} does not exist.")
    
    # Check if the path is a directory
    if not os.path.isdir(path):
        raise ValueError(f"The path {path} is not a directory.")
    
    # Get a list of files in the directory
    files = os.listdir(path)
    if len(files) == 0:
        raise ValueError(f"The directory {path} is empty.")
    
    # Get the first .tiff or .tif file in the directory
    file = None
    for f in files:
        if f.endswith(".tiff") or f.endswith(".tif"):
            file = os.path.join(path, f)
            break
    
    if file is None:
        raise ValueError(f"No .tiff or .tif file found in the directory {path}.")
    
    # Read the tiff/tif file
    image, physical_size_x, physical_size_y = read_tiff_file(file)
    sample_id = path.split('/')[-2]

    # Check if the image has more than 3 color channels
    if len(image.shape) > 3:
        raise ValueError(f"The image {file} has more than 3 color channels. Please check the file. Microscopy images can be at most 3 channels.")

    channels: list[np.ndarray] = []
    thresholds: list[np.ndarray] = []
    plots: list[tuple[np.ndarray, str]] = []
    processed_image: np.ndarray = np.zeros((image.shape[0], image.shape[1], 3), dtype = np.float32)

    # Unpack the channels, if the shape is 2D it's a grayscale
    if len(image.shape) == 2:
        channels.append(image)
    else:
        for channel_id in range(image.shape[2]):
            channel = image[:, :, channel_id]
            channels.append(channel)

    # Process each channel
    for channel_id in range(len(channels)):

        # Improve the color of the image by applying a gamma correction and contrast enhancement
        if color_enhancement == True:
            channels[channel_id] = gamma_correction(channels[channel_id], gamma=0.7)
            channels[channel_id] = enhance_contrast(channels[channel_id], saturated_pixels=0.35)

        # Save the output of the color enhancement
        processed_image[:, :, channel_id] = channels[channel_id]

        # Apply Gaussian blur
        if smoothing == True:
            channels[channel_id] = cv2.GaussianBlur(channels[channel_id], (9, 9), 5)

        # Compute an adaptive threshold for cropping
        if crop == True:
            # Rescale the intensity to 0-255 and convert to uint8
            standard = skimage.exposure.rescale_intensity(channels[channel_id], out_range=(0, 255)).astype(np.uint8)

            thresh = cv2.adaptiveThreshold(standard, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 5)
            thresholds.append(thresh)

    # DEBUG: Plot the enahanced channels
    if debug_mode == True:
        plots.append((copy.deepcopy(processed_image), 'Enhanced Image'))

    # Proceed with cropping if requested
    if crop == True:

        # Merge all the thresholds into a single mask
        threshold = np.zeros_like(thresholds[0])
        for i in range(len(thresholds)):
            threshold = np.maximum(threshold, thresholds[i])
        plots.append((copy.deepcopy(threshold), 'Merged Threshold'))

        # Define a kernel for morphological operations
        if filter_strength == constants.ImagingFilterStrength.SOFT:
            kernel = np.ones((5, 5), threshold.dtype)
            iterations = 1
            final_smoothing = False
            smoothing_filter = None
        elif filter_strength == constants.ImagingFilterStrength.MEDIUM:
            kernel = np.ones((5, 5), threshold.dtype)
            iterations = 2
            final_smoothing = True
            smoothing_filter = (15, 15)
        elif filter_strength == constants.ImagingFilterStrength.AGGRESSIVE:
            kernel = np.ones((7, 7), threshold.dtype)
            iterations = 3
            final_smoothing = True
            smoothing_filter = (21, 21)

        # Perform morphological operations to clean up the mask
        for i in range(iterations):
            threshold = cv2.erode(threshold, kernel, iterations = 1)
            threshold = cv2.dilate(threshold, kernel, iterations = 2)
            threshold = cv2.erode(threshold, kernel, iterations = 1)
            if debug_mode == True:
                plots.append((copy.deepcopy(threshold), f'Morphological Erosion/Dilation/Erosion {i + 1}'))

        # Apply Gaussian blur to the mask
        if final_smoothing == True:
            threshold = cv2.GaussianBlur(threshold, smoothing_filter, int(smoothing_filter[0] / 5))
            if debug_mode == True:
                plots.append((copy.deepcopy(threshold), 'Final Gaussian Blurring'))

        # Find the contours of the mask
        contours, _ = cv2.findContours(threshold, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        largest_contour = sorted(contours, key = cv2.contourArea, reverse = True)[0]
        if debug_mode == True:
            pic = np.ascontiguousarray(np.copy(processed_image), dtype=processed_image.dtype)
            pic = cv2.drawContours(pic, largest_contour, -1, (1.0, 0.0, 0.0), 100)
            pic = cv2.rectangle(pic, (x, y), (x + w, y + h), (0.0, 1.0, 0.0), 100)
            plots.append((copy.deepcopy(pic), 'Computed bounding box'))
        
        x, y, w, h = cv2.boundingRect(largest_contour)

        # Crop the final image
        processed_image = get_image_from_bounding_box(processed_image, x, y, w, h)
        if debug_mode == True:
            plots.append((copy.deepcopy(processed_image), 'Cropped Image'))


    # Create the output folder
    output_folder = os.path.join(path, 'processed')
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    
    # Save the processed image
    path_to_file = os.path.join(output_folder, f'{sample_id}_processed.tiff')
    skio.imsave(path_to_file, processed_image, metadata = {'PhysicalSizeX': physical_size_x, 'PhysicalSizeY': physical_size_y})

    if debug_mode == True:
        # Plot the intermediate steps
        for img, title in plots:
            plt.figure()
            plt.imshow(img, cmap='gray')
            plt.title(title)
            plt.axis('off')

    return physical_size_x, physical_size_y
    
'''
def read_he(path: str, iterations: int=1, offset: int=50, plot: bool=False):
    
    for file in os.listdir(path):
        if (file.endswith(".czi") or file.endswith(".tiff") or file.endswith(".tif")) and 'crop' not in file:
            
            path_to_file = os.path.join(path, file)
            
            if path_to_file.endswith(".czi"):
                with czifile.CziFile(path_to_file) as f:
                    
                    a = f.asarray()
                    a = a.reshape(a.shape[2:])
                    a = np.flip(np.swapaxes(a, axis1=0, axis2=1), axis=0)
                    
                    metadata = f.metadata()

                    # Parse the XML
                    root = ET.fromstring(metadata)

                    for child in root.iter('Distance'):
                        if 'Id' in child.keys():
                            if child.attrib['Id'] == 'X':
                                for val in child.iter('Value'):
                                    physical_size_x = val.text
                            if child.attrib['Id'] == 'Y':
                                for val in child.iter('Value'):
                                    physical_size_y = val.text

            else:
                with tifffile.TiffFile(path_to_file) as tif:
                    a = tif.asarray()
                    channel_index = 2       # Placeholder
                    try:
                        metadata = tif.shaped_metadata[0]
                        physical_size_x = metadata['PhysicalSizeX']
                        physical_size_y = metadata['PhysicalSizeY']
                    except:
                        #physical_size_x = str(tif.pages[0].tags['XResolution'].value[0] / tif.pages[0].tags['XResolution'].value[1] ** 2)
                        #physical_size_y = str(tif.pages[0].tags['YResolution'].value[0] / tif.pages[0].tags['YResolution'].value[1] ** 2)
                        physical_size_x = 1e-6  # Placeholder: Pixel size is 1 um
                        physical_size_y = 1e-6
                        #a = np.swapaxes(a, axis1=0, axis2=1)
                
            #_, thresh = cv2.threshold(a[:, :, 0], 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)

            # Define a sequence of snapshots for plotting purposes
            plots = []

            # Define the channels
            channels: list[np.ndarray] = []
            thresholds: list[np.ndarray] = []
            
            # Unpack the channels, if the shape is 2D it's a grayscale
            if len(a.shape) == 2:
                channel = a.astype(np.float32)
                channels.append(channel)
            else:
                # Get the channel dimension
                number_of_channels = a.shape[channel_index]

                for channel_id in range(number_of_channels):
                    if channel_index == 0:
                        channel = a[channel_id, :, :].astype(np.float32)
                    elif channel_index == 1:
                        channel = a[:, channel_id, :].astype(np.float32)
                    elif channel_index == 2:
                        channel = a[:, :, channel_id].astype(np.float32)

                    channels.append(channel)

            # Process each channel
            for channel_id in range(len(channels)):

                # Normalize them between 0 and 1
                channels[channel_id] = channels[channel_id] / np.max(channels[channel_id])
                #plots.append((copy.deepcopy(channels[channel_id]), f'Channel {channel_id}'))

                # Improve the contrast of each channel and gamma correct
                channels[channel_id] = gamma_correction(channels[channel_id], gamma=0.7)
                channels[channel_id] = enhance_contrast(channels[channel_id], saturated_pixels=0.35)

                # Rescale the intensity to 0-255
                channels[channel_id] = skimage.exposure.rescale_intensity(channels[channel_id], out_range=(0, 255)).astype(np.uint8)

                # Apply Gaussian blur
                channels[channel_id] = cv2.GaussianBlur(channels[channel_id], (9, 9), 5)
                #plots.append((copy.deepcopy(channels[channel_id]), f'Blurred Channel {channel_id}'))

                # Compute an adaptive threshold
                thresh = cv2.adaptiveThreshold(channels[channel_id], 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 5)
                #plots.append((copy.deepcopy(thresh), f'Threshold Channel {channel_id}'))
                thresholds.append(thresh)

            # Define an RGB image, use empty channels if the image is less than 3 channels
            rgb_image = np.zeros((channels[0].shape[0], channels[0].shape[1], 3), dtype=np.uint8)
            for channel_id in range(len(channels)):
                if channel_id < 3:
                    rgb_image[:, :, channel_id] = channels[channel_id]
                else:
                    break
            plots.append((copy.deepcopy(rgb_image), 'RGB Image'))

            # Merge the thresholds into a single mask
            threshold = np.zeros_like(thresholds[0])
            for i in range(len(thresholds)):
                threshold = np.maximum(threshold, thresholds[i])
            plots.append((copy.deepcopy(threshold), 'Merged Threshold'))

            # Define a kernel for morphological operations
            kernel = np.ones((5, 5), a.dtype)

            # Perform morphological operations to clean up the mask
            morph = cv2.erode(threshold, kernel, iterations = iterations)
            plots.append((copy.deepcopy(morph), 'Morphological Erosion 1'))

            morph = cv2.dilate(morph, kernel, iterations = 2 * iterations)
            plots.append((copy.deepcopy(morph), 'Morphological Dilation 1'))

            morph = cv2.erode(morph, kernel, iterations=iterations)
            plots.append((copy.deepcopy(morph), 'Morphological Erosion 2'))

            morph = cv2.GaussianBlur(morph, (21, 21), 5)
            plots.append((copy.deepcopy(morph), 'Morphological Blurring (21x21)'))
            
            contours, _ = cv2.findContours(morph, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
            largest_contour = sorted(contours, key = cv2.contourArea, reverse = True)[0]
            
            x, y, w, h = cv2.boundingRect(largest_contour)
            
            sample = path_to_file.split('/')[-3]
            s = path_to_file.split('/')
            s[-1] = f'{sample}_crop.tiff'
            path_to_file = '/'.join(s)
            
            result = get_image_from_bounding_box(rgb_image, x, y, w, h, offset)
            skio.imsave(path_to_file, result, metadata = {'PhysicalSizeX': physical_size_x, 'PhysicalSizeY': physical_size_y})

            if plot == True:
                # Plot the intermediate steps
                for img, title in plots:
                    plt.figure()
                    plt.imshow(img, cmap='gray')
                    plt.title(title)
                    plt.axis('off')

                # Plot the original image with the bounding box
                pic = np.ascontiguousarray(np.copy(rgb_image), dtype=np.uint8)
                pic = cv2.drawContours(pic, largest_contour, -1, (255, 0, 0), 100)
                pic = cv2.rectangle(pic, (x, y), (x + w, y + h), (0, 255, 0), 100)
                plt.figure()
                plt.imshow(pic)
                plt.title('Computed bounding box')
                plt.axis('off')

                # Plot the cropped image
                plt.figure()
                plt.imshow(result)
                plt.title('Cropped image')
                plt.axis('off')
            
            return 1e6 * float(physical_size_x), 1e6 * float(physical_size_y)
                
def split_he(path_to_czi: str):
    if path_to_czi.endswith(".czi"):
        with czifile.CziFile(path_to_czi) as f:
            
            a = f.asarray()
            for i in range(a.shape[0]):
                image = a[i].reshape(a.shape[2:])
                image = np.flip(np.swapaxes(image, axis1=0, axis2=1), axis=0)
                
                _, res = cv2.threshold(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
                contours, _ = cv2.findContours(res, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
                cnt = sorted(contours, key=cv2.contourArea, reverse=True)[0]
                
                x, y, w, h = cv2.boundingRect(cnt)
                
                plt.figure()
                plt.imshow(image[y: y + h, x: x + w, :])
                
                metadata = f.metadata()

                # Parse the XML
                root = ET.fromstring(metadata)

                for child in root.iter('Distance'):
                    if 'Id' in child.keys():
                        if child.attrib['Id'] == 'X':
                            for val in child.iter('Value'):
                                physical_size_x = val.text
                        if child.attrib['Id'] == 'Y':
                            for val in child.iter('Value'):
                                physical_size_y = val.text
                
                skio.imsave(path_to_czi.replace('.czi', f'-{i + 1}.tiff'), image[y: y + h, x: x + w, :], metadata={'PhysicalSizeX': physical_size_x, 'PhysicalSizeY': physical_size_y})
'''

