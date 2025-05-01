import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
import os
import cv2


def make_image(mapping, data, sz):
    
    result = np.zeros((*sz, data.shape[1]))
    for i, (j, k) in enumerate(mapping):
        result[j, k] = data[i]
        
    return result


def make_image_cluster(mapping, data, sz, colors):
    
    result = np.zeros((*sz, 3))
    for i, (j, k) in enumerate(mapping):
        result[j, k] = colors[int(data[i])]
        
    return result


def plot(path, mapping, sz):
    sample = path.split('/')[-3]
    title = sample + ' ' + path.split('/')[-1][:-4]
    if '.npy' not in path:
        return
    if 'PCA' in path or 'UMAP' in path:
        image = np.load(path)
        if len(image.shape) == 2:
            for i in range(3):
                image[:, i] = (image[:, i] - np.min(image[:, i])) / (np.max(image[:, i]) - np.min(image[:, i]))
            
            plt.figure()
            plt.imshow(make_image(mapping, image, sz))
        else:
            for i in range(3):
                image[:, :, i] = (image[:, :, i] - np.min(image[:, :, i])) / (np.max(image[:, :, i]) - np.min(image[:, :, i]))
            
            plt.figure()
            plt.imshow(image) 
        
        plt.title(title)
                
    elif 'KMeans' in path:
        colormap = mpl.colormaps['Accent']
        accent_colors = colormap.colors
        colors = [(0, 0, 0)]
        [colors.append(np.array(c)) for c in accent_colors]
        colormap.colors = np.array(colors)
        image = np.load(path)
        plt.figure()
        plt.imshow(make_image_cluster(mapping, image, sz, colors))
        plt.title(title)
        
    elif 'baseline_corrected' in path:
        image = np.load(path)[:, :, 0]        
        plt.figure()
        plt.imshow(image)
        
    elif 'h&e' in path:
        image = np.load(path)
        plt.figure()
        plt.imshow(make_image(mapping, image, sz), cmap='gray')
        plt.title(title)
    elif 'mapping' not in path:
        image = np.load(path)
        image = (image - np.min(image)) / (np.max(image) - np.min(image))
        plt.figure()
        plt.imshow(make_image(mapping, image, sz), cmap='gray')
        plt.title(title)
    plt.xticks([], [])
    plt.yticks([], [])
    plt.savefig(path.replace('.npy', '.png'), bbox_inches='tight', dpi=500)
        

path = os.getcwd() + f'/data/00071300/'
sz = (800, 800)
mapping = np.load(path + 'tissue_mapping.npy')
for file in os.listdir(path + 'figures/'):
    plot(path + 'figures/' + file, mapping, sz)

# path = os.getcwd() + f'/data/ito/'
# sz = (800, 800)
# mapping = np.load(path + 'tissue_mapping.npy')
# for file in os.listdir(path + 'figures/'):
#     plot(path + 'figures/' + file, mapping, sz)

# plot(os.getcwd() + f'/data/00033464/raman/stitched_and_registered.npy')

# image = cv2.imread('./data/00033464/raman/TileScan 4_Merged.png')
# plt.figure()
# plt.imshow(np.swapaxes(image, 0, 1), cmap='gray')
# cv2.imwrite('./data/00033464/raman/TileScan 4_Merged.png', np.swapaxes(image, 0, 1))
# image = cv2.imread('./data/00033464/raman/stitched_and_registered.png')
# plt.figure()
# plt.imshow(image, cmap='gray')
# cv2.imwrite('./data/00033464/raman/stitched_and_registered.png', image)

# plot(os.getcwd() + f'/data/ito/raman/baseline_corrected_0.npy', None, None)

plt.show()

