import numpy as np
import matplotlib.pyplot as plt
import cv2


def get_image_from_bounding_box(image: np.ndarray, x: int, y: int, w: int, h: int, offset: int=10) -> np.ndarray:
    """
    Get a cropped image from the bounding box.
    """
    return image[np.max([y - offset, 0]): np.min([y + h + offset, image.shape[0]]), np.max([x - offset, 0]): np.min([x + w + offset, image.shape[1]])]

def float_to_uint8(d):
    d -= np.min(d)
    return np.uint8(255 * (d / np.max(d)))

def crop_raman(path, iterations=1, offset: int=20, plot: bool=False):
    
    data = np.load(path)
    
    d = np.float32(data)
    index = np.argmax([np.std(d[:, :, i] - np.mean(d[:, :, i])) for i in range(data.shape[2])])    
    
    data_index = float_to_uint8(data[:, :, index])
    
    laplacian = cv2.Laplacian(data_index, cv2.CV_8UC1)
    
    _, thresh = cv2.threshold(laplacian, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    
    kernel = np.ones((3, 3), data.dtype)
    dilate1 = cv2.dilate(thresh, kernel, iterations=1 * iterations)
    erode = cv2.erode(dilate1, kernel, iterations=2 * iterations)
    dilate2 = cv2.dilate(erode, kernel, iterations=1 * iterations)
    
    contours, _ = cv2.findContours(dilate2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cnt = sorted(contours, key=cv2.contourArea, reverse=True)[0]
    
    x, y, w, h = cv2.boundingRect(cnt)
    
    if plot:
        
        plt.figure()
        plt.imshow(data_index, cmap='gray')
        plt.figure()
        plt.imshow(laplacian, cmap='gray')
        plt.figure()
        plt.imshow(thresh, cmap='gray')
        plt.figure()
        plt.imshow(dilate1, cmap='gray')
        plt.figure()
        plt.imshow(erode, cmap='gray')
        plt.figure()
        plt.imshow(dilate2, cmap='gray')
        
        result = get_image_from_bounding_box(data_index, x, y, w, h, offset)
    
        pic = np.ascontiguousarray(np.copy(data_index), dtype=np.uint8)
        pic = cv2.drawContours(pic, cnt, -1, (255, 0, 0), 100)
        
        pic = cv2.rectangle(pic, (x, y), (x + w, y + h), (0, 255, 0), 100)
        
        plt.figure()
        plt.imshow(pic, cmap='gray')
        plt.figure()
        plt.imshow(result, cmap='gray')
    
    np.save(path.replace("ashlar.npy", "ashlar_crop.npy"), get_image_from_bounding_box(data, x, y, w, h, offset))

if __name__ == '__main__':
    
    path = './data/31/ashlar.npy'
    crop_raman(path, iterations=5)
    
    path = './data/32/ashlar.npy'
    crop_raman(path, iterations=2)
    path = './data/41/ashlar.npy'
    crop_raman(path, iterations=2)
    path = './data/42/ashlar.npy'
    crop_raman(path, iterations=2)


