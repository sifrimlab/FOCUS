import numpy as np
import matplotlib.pyplot as plt
import matplotlib.widgets as mpwidgets


def plot_opacity_slider(overlay: np.ndarray, base: np.ndarray):
    OPACITY = 0.5

    if overlay.shape != base.shape:
        padded = np.zeros_like(base)
        # Overlay in top-left corner (adjust if needed)
        h, w = overlay.shape
        padded[:h, :w] = overlay
        overlay = padded


    # PLOT
    fig, (ax0, ax1) = plt.subplots(2, 1, gridspec_kw={'height_ratios': [5, 1]})
    ax0.imshow(base, cmap="gray")
    img1 = ax0.imshow(overlay, cmap="gray", alpha=OPACITY, origin='lower')


    def update(value): 
        img1.set_alpha(value)    
        fig.canvas.draw_idle()

    slider0 = mpwidgets.Slider(ax=ax1, label='opacity', valmin=0, valmax=1, valinit=OPACITY)
    slider0.on_changed(update)

    plt.show()

if __name__ == '__main__':
    path = 'C:/Users/tvanheme/Desktop/PhD/Code/RAMALDI/data/registration'
    x = np.load(f'{path}/fixed_image.npy')
    y = np.load(f'{path}/moved_image.npy')
    
    mapping = np.load('./data/00033464/tissue_mapping.npy')
    def make_image(mapping, data, sz):
    
        result = np.zeros(sz)
        for i, (j, k) in enumerate(mapping):
            result[j, k] = data[i]
            
        return result
    mask = make_image(mapping, np.ones((mapping.shape[0],)), (800,800))
    
    import tifffile
    he = tifffile.imread('./data/00033464/h&e/00033464.tif')
    crop_dict = {'00033464': [376, 2332, 1572, 3454], 'ito': [892, 2316, 1340, 2834]} 
    crop = crop_dict['00033464']
    he = he[crop[0]: crop[1], crop[2]: crop[3]]
    from skimage.transform import resize
    he = np.float32(resize(he, (800, 800, he.shape[2])))
    he = np.flip(np.swapaxes(he, 0, 1), axis=0)
    x = he
    y = mask
    plot_opacity_slider(x, y)

