import matplotlib.pyplot as plt
from matplotlib.widgets import Slider
import numpy as np
import colorcet as cc
import os
from sklearn.decomposition import PCA

import sys
module_path = os.path.abspath(os.getcwd())
if module_path not in sys.path:
    sys.path.append(module_path)
    

def normalize(d):
    avg = np.average(d, axis=0)
    avg -= np.min(avg)
    avg /= np.max(avg)
    for i in range(d.shape[0]):
        d[i, :] -= np.min(d[i, :])
        if not np.isclose(np.max(d[i, :]), 0, atol=1e-5):
            d[i, :] /= np.max(d[i, :])
        else:
            d[i, :] = avg
    return d

def kmeans_cluster_means(labels, data, suffix, xaxis):
    k = np.unique(labels).shape[0]
    if k // 2 == 1:
        fig, axsk = plt.subplots(int(k / (k // 2)), 1, sharex=True, sharey=True)
    elif int(k / (k // 2)) == 1:
        fig, axsk = plt.subplots(k // 2, 1, sharex=True, sharey=True)
    else:
        fig, axsk = plt.subplots(k // 2, int(k / (k // 2)), sharex=True, sharey=True)
    fig.suptitle(f'KMeans k={k} cluster means {suffix}')
    for i in range(k):
        a = np.average(data[labels == i], axis=0)
        if int(k // 2) == 1 or int(k / (k // 2)) == 1:
            axsk[i].plot(xaxis, a)
            axsk[i].set_title(f'Cluster {i + 1}')
        else:
            axsk[i // 2, i % 2].plot(xaxis, a)
            axsk[i // 2, i % 2].set_title(f'Cluster {i + 1}')
    
    plt.tight_layout()
    
def plot_pcs(reducer, suffix, xaxis):
    fig, axsk = plt.subplots(reducer.components_.shape[0], 1, sharex=True, sharey=True)
    fig.suptitle(f'Principal components {suffix}')
    for i in range(reducer.components_.shape[0]):
        axsk[i].plot(xaxis, reducer.components_[i, :] / np.linalg.norm(reducer.components_[i, :]))
        axsk[i].set_title(f'PC {i + 1}')
    
    plt.tight_layout()
    
def perform_pca_and_scale(d, sz):
    
    reducer = PCA(n_components=3)
    d = reducer.fit_transform(d)
    d = np.float32(d)
    
    scaled = np.zeros_like(d)
    for i in range(3):
        scaled[:, i] = 255 * (d[:, i] - np.min(d[:, i])) / (np.max(d[:, i]) - np.min(d[:, i]))
    scaled = np.uint8(scaled.reshape((*sz[: 2], 3)))
    
    return reducer, scaled, d

def score_plot_3d(d, suffix):
    
    c = np.ndarray(d.shape)
    
    for i in range(3):
        c[:, i] = (d[:, i] - np.min(d[:, i])) / (np.max(d[:, i]) - np.min(d[:, i]))
    
    fig = plt.figure()
    ax = fig.add_subplot(projection='3d')
    ax.scatter(d[:, 0], d[:, 1], d[:, 2], c=c)
    ax.set_xlabel('PC 1')
    ax.set_ylabel('PC 2')
    ax.set_zlabel('PC 3')
    ax.set_title(f'3D PCA scores plot {suffix}')
    
         
if __name__ == '__main__':
    init = 0
    sample = 'baseline_corrected_0.npy'
    path = os.getcwd() + "/data/00071300/raman"
    
    original = np.float32(np.load(f"{path}/ashlar_registered.npy"))
    data = np.float32(np.load(f"{path}/{sample}"))
    baseline_corrected = np.float32(np.load(f"{path}/{sample}"))
    smooth_data = np.float32(np.load(f'{path}/smooth_0.npy'))
    
    xaxis = np.load(f'{path}/wavelength_vector.npy')
    from get_wavelength import get_wavenumber
    xaxis = np.array([get_wavenumber(x) for x in xaxis])
        
    xaxis = np.flip(xaxis)
    data = np.flip(data, axis=2)
    original = np.flip(original, axis=2)
    baseline_corrected = np.flip(baseline_corrected, axis=2)
    smooth_data = np.flip(smooth_data, axis=2)
        
    fig1, ax1 = plt.subplots(1, 1)
    fig2, (ax2, ax3) = plt.subplots(1, 2)
    
    def mouse_click(event):
        x, y = event.xdata, event.ydata
        if x and y:
            index = (int(np.round(y)), int(np.round(x)))
            if 0 <= index[0] < data.shape[0] and 0 <= index[1] < data.shape[1]:
                print(index)
                ax3.cla()
                ax3.plot(xaxis, original[index[0], index[1], :], label='original', c='k')
                ax3.plot(xaxis, baseline_corrected[index[0], index[1], :], label='baseline_corrected', c='r')
                ax3.plot(xaxis, smooth_data[index[0], index[1], :], label='smooth', c='b')
                ax3.set_xlabel('wavenumber')
                ax3.set_ylabel('intensity')
                ax3.set_title('Raman Spectrum')
                ax3.legend()
                ax3.set_xlim([xaxis[0], xaxis[-1]])
                plt.pause(0.005)
                
    if not os.path.exists(f'{path}/figures/'):
        os.makedirs(f'{path}/figures/')
    
    d = original
    d = np.copy(d).reshape((np.prod(d.shape[: 2]), d.shape[2]))
    # d = normalize(d)
    for i in range(d.shape[0]):
        if not np.isclose(np.sum(d[i, :]), 0, atol=1e-5):
            d[i, :] /= np.sum(d[i, :])
    
    reducer, scaled, scores = perform_pca_and_scale(d, data.shape)
    ax2.imshow(scaled)
    ax2.set_title('PCA')
    plt.connect('button_press_event', mouse_click)
    
    ax1.imshow(data[:, :, 0])
    ax1.set_title(xaxis[0])
    axmz = fig1.add_axes([0.25, 0.1, 0.65, 0.03])
    # Make a horizontal slider to control the m/z values.
    mz_slider = Slider(
        ax=axmz,
        label='index',
        valmin=0,
        valmax=xaxis.shape[0] - 1,
        valinit=0,
        valstep=1,
    )
    
    def update(val):
        ax1.cla()
        img = data[:, :, val]
        ax1.imshow(img)
        ax1.set_title(f'{np.round(xaxis[val], decimals=2)} cm^-1')
        fig1.canvas.draw_idle()
    
    fig1.subplots_adjust(bottom=0.25)
    mz_slider.on_changed(update)
    
    mz_slider.reset()    
    
    plt.show()
    
    

