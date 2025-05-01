import czifile, skimage, copy
import numpy as np
import matplotlib.pyplot as plt
import cv2
import skimage.exposure
import tifffile    
import os
import skimage.io as skio
import xml.etree.ElementTree as ET


def get_image_from_bounding_box(image: np.ndarray, x: int, y: int, w: int, h: int, offset: int=10) -> np.ndarray:
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

if __name__ == "__main__":
    
    # import os
    # p = "C:/Users/tvanheme/Desktop/PhD/Code/RAMALDI/data/"
    # for folder in ["00033464/", "00071845/", "ito/"]:
    #     for file in os.listdir(p + folder + "h&e/"):
    #         read_he(p + folder + "h&e/" + file, iterations=1, plot=True)
    
    # path = './data/00071300/h&e/00071300.czi'
    
    import yaml
    # with open('./config.yaml', 'r') as f:
    #     config = yaml.safe_load(f)
    
    # for path in ['./transfer_428938_files_ee05f1c2/00103993-1/h&e/', './transfer_428938_files_ee05f1c2/00103993-2/h&e/', './transfer_428938_files_ee05f1c2/00103994-1/h&e/', './transfer_428938_files_ee05f1c2/00103994-2/h&e/']:
    #     read_he(path, iterations=1, plot=True)
    # plt.show()
    
    # path = './transfer_428938_files_ee05f1c2/h&e/00103994.czi'
    # path = './transfer_428938_files_ee05f1c2/h&e/00103993.czi'
    # split_he(path)
    
    # split_he('./transfer_428938_files_ee05f1c2/2-again.czi')
    # split_he('./transfer_428938_files_ee05f1c2/h&e/00103993.czi')
    # split_he('./transfer_428938_files_ee05f1c2/h&e/00103994.czi')
    
    # read_he('./transfer_428938_files_ee05f1c2/00103993-1/h&e/', iterations=1, plot=True)
    
    read_he('./data/00071300/h&e/', iterations=1, invert=True, plot=True)
    plt.show()
    
    # read_he('./transfer_428938_files_ee05f1c2/', iterations=1, plot=True)

