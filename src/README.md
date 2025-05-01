
Nina sends me files:

    - raman: .lif
    - H&E: .tif
    - maldi: .imzML and .ibd

Preprocessing pipeline:

    - raman: 
        - src/preprocessing_ashlar/read_images_lif.py               To read tiles from .lif and save them separately to .tiff files with relevant metadata after applying the BaSiC shading correction.
        - src/preprocessing_ashlar/run_ashlar_after_basic.py        Perform ASHLAR on the .tiff files, i.e., stitch the tiles and register them. Save result to stitched_and_registered.npy.
        - src/preprocessing/baseline_correction.py                  Perform baseline correction.
        - src/preprocessing/smoothing.py                            Perform signal smoothing using the Savitzky-Golay filter.
        
    - maldi:
        - src/imzmltopy.py                                          Convert .imzML and .ibd to .npy files containing the intensities, the m/z values and the pixel locations.
        
    - H&E:
        - No preprocessing necessary.
        
    - Data fusion:
        In some of the following scripts a tool will show up allowing you to aid the registration by adding fiducials/landmarks to the image. Additionally, best results will be obtained if the data is cropped such that the sample barely fits on the image. This needs to be performed manually (for now), you create a plot of the sample and infer the correct crop. The raman and H&E staining images should be cropped. The maldi files are cropped by design. IMPORTANT: It is essential to have a maldi/raman image that shares a lot of similarities with the H&E staining to obtain an accurate registration. The registration class in the following scripts default to the score of the first principal component of the maldi/raman data and takes the average of the 3 H&E channels. You can provide an alternative by setting the maldi/raman_image in the constructor of the registration classes.
        - src/register_maldi_to_he.py                               Resize the maldi and H&E staining to the given size and perform an affine transformation followed by a non-rigid B-spline registration procedure.
        - src/register_raman_to_he.py                               Resize the raman and H&E staining to the given size and perform an affine transformation followed by a non-rigid B-spline registration procedure.

Data analysis:

    - TODO

