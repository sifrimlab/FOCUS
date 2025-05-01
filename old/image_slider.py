import matplotlib.pyplot as plt
from matplotlib.widgets import Slider
import numpy as np
import os

         
if __name__ == '__main__':
                
    path = f'{os.getcwd()}/data/00071300/raman/'
    file = '/ashlar.npy'
            
    # This creates a plot of an image with a slider to go through the channels of the image
    init = 0
    data = np.load(f'{path}/{file}')
            
    fig1, ax1 = plt.subplots(1, 1)
                
    img = data[:, :, init]
    ax1.imshow(img, cmap='gray')
    ax1.set_title(init)
    axmz = fig1.add_axes([0.25, 0.1, 0.65, 0.03])
    # Make a horizontal slider to control the m/z values.
    mz_slider = Slider(
        ax=axmz,
        label='m/z values [Da]',
        valmin=0,
        valmax=data.shape[2] - 1,
        valinit=init,
    )
    
    def update(val):
        old_x_lim = ax1.get_xlim()
        old_y_lim = ax1.get_ylim()
        ax1.cla()
        ax1.imshow((data[:, :, int(mz_slider.val)] - data[:, :, int(mz_slider.val)].min()) / (data[:, :, int(mz_slider.val)].max() - data[:, :, int(mz_slider.val)].min()), cmap='gray')
        # ax1.imshow(data[:, int(mz_slider.val)])
        ax1.set_xlim(old_x_lim)
        ax1.set_ylim(old_y_lim)
        ax1.set_title(int(mz_slider.val))
        fig1.canvas.draw_idle()
    
    fig1.subplots_adjust(bottom=0.25)
    mz_slider.on_changed(update)
    
    mz_slider.reset()
    
    plt.figure()
    from sklearn.decomposition import PCA
    pca = PCA(n_components=3)
    sz = data.shape[: 2]
    d = pca.fit_transform(data.reshape(np.prod(sz), data.shape[2]))
    d = (d - np.min(d)) / (np.max(d) - np.min(d))
    plt.imshow(d.reshape(*sz, 3))
    
    plt.show()
    
        