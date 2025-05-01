import numpy as np
import tifffile
import matplotlib.pyplot as plt

def get_maldi_image(maldi, row2grid):
    xmin = np.min(row2grid[:, 0])
    xmax = np.max(row2grid[:, 0])
    ymin = np.min(row2grid[:, 1])
    ymax = np.max(row2grid[:, 1])
    
    result = np.zeros([xmax - xmin + 1, ymax - ymin + 1], dtype=maldi.dtype)
    for i in range(row2grid.shape[0]):
        result[row2grid[i, 0] - xmin, row2grid[i, 1] - ymin] = maldi[i]

    return result

def main(path, sample):
    
    he = tifffile.imread(f'{path}/{sample}/h&e/{sample}_crop.tiff')
    
    maldi = np.load(f'{path}/{sample}/maldi/{sample}_noTIC_matrix.npy')
    row2grid = np.load(f'{path}/{sample}/maldi/{sample}_row2grid.npy')
    maldi_coordinates = np.load(f'{path}/{sample}/maldi/maldi_coordinates.npy')
    
    raman = np.load(f'{path}/{sample}/raman/smooth_0.npy')
    raman_coordinates = np.load(f'{path}/{sample}/raman/raman_coordinates.npy')
    
    fig, axs = plt.subplots(1, 2)
    axs[0].imshow(he)
    axs[1].imshow(he)
    
    lines = []
    
    k = 2
    
    for i in range(maldi_coordinates.shape[0]):
        if i % k == 0:
            lines.append((maldi_coordinates[i, :, 0][maldi_coordinates[i, :, 0] != -1], maldi_coordinates[i, :, 1][maldi_coordinates[i, :, 0] != -1]))
    for j in range(maldi_coordinates.shape[1]):
        if j % k == 0:
            lines.append((maldi_coordinates[:, j, 0][maldi_coordinates[:, j, 0] != -1], maldi_coordinates[:, j, 1][maldi_coordinates[:, j, 0] != -1]))
                
    for line in lines:
        if len(line[0]) > 0:
            axs[0].plot(line[0], line[1], color='red', linewidth=1)
    
    lines = []
    
    k = 20
    
    for i in range(raman_coordinates.shape[0]):
        if i % k == 0:
            lines.append((raman_coordinates[i, :, 0][raman_coordinates[i, :, 0] != -1], raman_coordinates[i, :, 1][raman_coordinates[i, :, 0] != -1]))
    for j in range(raman_coordinates.shape[1]):
        if j % k == 0:
            lines.append((raman_coordinates[:, j, 0][raman_coordinates[:, j, 0] != -1], raman_coordinates[:, j, 1][raman_coordinates[:, j, 0] != -1]))
                
    for line in lines:
        if len(line[0]) > 0:
            axs[1].plot(line[0], line[1], color='blue', linewidth=1)
    plt.show()


if __name__ == '__main__':
    
    path = './results'
    sample = '00103993-1'
    
    main(path, sample)

