import matplotlib.pyplot as plt
from matplotlib.widgets import Slider
import numpy as np
import colorcet as cc
import os
import math
from sklearn.decomposition import PCA
from bisect import bisect_left, bisect_right

import sys
module_path = os.path.abspath(os.getcwd())
if module_path not in sys.path:
    sys.path.append(module_path)
            
def make_image_color(row2grid, data):
    xmax = np.max(row2grid[:, 0])
    xmin = np.min(row2grid[:, 0])
    ymax = np.max(row2grid[:, 1])
    ymin = np.min(row2grid[:, 1])
    
    image_matrix = np.zeros([xmax - xmin + 1, ymax - ymin + 1, 3])
    for i, e in enumerate(row2grid):
        image_matrix[e[0] - xmin, -(e[1] - ymin), :] = data[i, :]
    return image_matrix

def scale_to_rgb(data):
    min_val = np.min(data, axis=0)
    max_val = np.max(data, axis=0)
    
    scaled_matrix = np.zeros_like(data)
    
    for column in range(data.shape[1]):
        scaled_matrix[:, column] = (data[:, column] - min_val[column]) / (max_val[column] - min_val[column])
    return scaled_matrix
    
def find_nearest_idx(array,value):
    idx = np.searchsorted(array, value, side="left")
    if idx > 0 and (idx == len(array) or math.fabs(value - array[idx-1]) < math.fabs(value - array[idx])):
        return idx-1
    else:
        return idx
    
def make_image(row2grid, data):
    xmax = np.max(row2grid[:, 0])
    xmin = np.min(row2grid[:, 0])
    ymax = np.max(row2grid[:, 1])
    ymin = np.min(row2grid[:, 1])

    image_matrix = np.zeros([xmax - xmin + 1, ymax - ymin + 1])
    for i, e in enumerate(row2grid):
        image_matrix[e[0] - xmin, -(e[1] - ymin)] = data[i]
    return image_matrix
    
def make_ion_image(data, mz_vector, row2grid, mz_value):
    mz_vector = np.ndarray.flatten(mz_vector)
    decimals = str(mz_value)[::-1].find('.')
    if decimals==-1:
        decimals=0
    index = np.where(np.round(mz_vector, decimals)==mz_value)[0]
    if len(index) == 0:
        index = find_nearest_idx(mz_vector, mz_value)
    else:
        index = index[0]
    return make_image(row2grid, data[:, index]), mz_vector[index]

def make_ion_image2(data, mz_vector, row2grid, index):
    return make_image(row2grid, data[:, index]), mz_vector[index]

def make_image_color_clusters(row2grid, spatial_i):
    xmax = np.max(row2grid[:, 0])
    xmin = np.min(row2grid[:, 0])
    ymax = np.max(row2grid[:, 1])
    ymin = np.min(row2grid[:, 1])

    # background is white 
    image_matrix = np.zeros([xmax - xmin + 1, ymax - ymin + 1])
    k = 0
    for e in row2grid:
        # spatial_i is -1 when point is considered noise -> black
        if spatial_i[k] == -1:
            image_matrix[e[0] - xmin, -(e[1] - ymin)] = 0
        else:
            image_matrix[e[0] - xmin, -(e[1] - ymin)] = spatial_i[k] #colors[spatial_i[k] % colors.shape[0], :]
        k += 1
    return image_matrix
         
         
if __name__ == '__main__':
    mz_init = 1000
    sample = '00071300'
    path = os.getcwd() + "/data/00071300/maldi/" + sample
    
    row2grid = np.load(f"{path}_row2grid.npy")
    mz_vector = np.load(f"{path}_mz_vector.npy")
    data = np.load(f"{path}_noTIC_matrix.npy")
    data[data <= 0] = 0
    
    start = 300
    stop = 2600
    
    # Limit data to a given m/z range
    start_index = np.max([bisect_left(mz_vector, start) - 1, 0])
    stop_index = np.min([bisect_right(mz_vector, stop), mz_vector.shape[0] - 1])
    data = data[:, start_index: stop_index + 1]
    mz_vector = mz_vector[start_index: stop_index + 1]
        
    for i in range(data.shape[0]):
        data[i, :] /= np.sum(data[i, :])

    pixel_to_index_dict = dict()
    xmin = np.min(row2grid[:, 0])
    ymin = np.min(row2grid[:, 1])
    ymax = np.max(row2grid[:, 1])

    for i, e in enumerate(row2grid):
        pixel_to_index_dict[e[0] - xmin, -(e[1] - ymin) + ymax] = i    
        
    fig1, ax1 = plt.subplots(1, 1)
    fig2, (ax2, ax3) = plt.subplots(1, 2)
    
    def plot_sample(row2grid, data):
        ax2.imshow(make_image_color(row2grid, data), cmap=cc.cm.rainbow)
        ax2.set_title(f"Dimensionality reduction {sample}")
        
    avg = np.average(data, axis=0)
    def mouse_click(event):
        global prev_pixel
        x, y = event.xdata, event.ydata
        if x and y:
            index = pixel_to_index_dict.get((np.round(y), np.round(x)))
            if index:
                print(index)
                ax3.cla()
                ax3.plot(mz_vector, data[index, :], label='pixel')
                ax3.set_xlabel('m/z value')
                ax3.set_ylabel('intensity')
                ax3.set_title('Mass Spectrum')
                ax3.legend()
                plt.pause(0.005)
                
    reducer = PCA(n_components=3)

    embedding = reducer.fit_transform(data)

    scaled_embedding = scale_to_rgb(embedding)

    plt.connect('button_press_event', mouse_click)
    plot_sample(row2grid, scaled_embedding)
    img, mz = make_ion_image(data, mz_vector, row2grid, mz_init)
    ax1.imshow(img)
    ax1.set_title(mz)
    axmz = fig1.add_axes([0.25, 0.1, 0.65, 0.03])
    # Make a horizontal slider to control the m/z values.
    mz_slider = Slider(
        ax=axmz,
        label='index',
        valmin=0,
        valmax=mz_vector.shape[0] - 1,
        valinit=0,
        valstep=1,
    )
    
    def update(val):
        ax1.cla()
        # img, mz = make_ion_image(data, mz_vector, row2grid, mz_slider.val)
        img = make_image(row2grid, data[:, mz_slider.val])
        img = (img - np.min(img)) / (np.max(img) - np.min(img))
        ax1.imshow(img)
        ax1.set_title(f'{np.round(mz_vector[mz_slider.val], decimals=3)} Da')
        fig1.canvas.draw_idle()
    
    fig1.subplots_adjust(bottom=0.25)
    mz_slider.on_changed(update)
    
    mz_slider.reset()
    
    plt.show()
        