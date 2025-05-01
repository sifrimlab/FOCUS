import tifffile
import os
import ome_types
import pathlib
import copy
import uuid
import numpy as np

from .run_ahslar_parallel import process_single


def make_ome_pixel(img_path: str, sample_ome: ome_types.OME, metadata: dict):
    
    img_path: pathlib.Path = pathlib.Path(img_path)
    sample_ome = copy.deepcopy(sample_ome)
    pixel = sample_ome.images[0].pixels
    
    pixel.physical_size_x = metadata['PhysicalSizeX']
    pixel.physical_size_y = metadata['PhysicalSizeY']

    UUID = ome_types.model.TiffData.UUID(
        file_name=str(img_path.name),
        value=uuid.uuid4().urn
    )

    tiff_block = pixel.tiff_data_blocks[0]

    num_planes = tiff_block.plane_count
    tiff_block.uuid = UUID

    for i in range(num_planes):
        plane = ome_types.model.Plane(
            the_c=0, the_z=0, the_t=i,
            position_x=float(metadata['PositionX']), position_x_unit=metadata['PositionXUnit'], 
            position_y=float(metadata['PositionY']), position_y_unit=metadata['PositionYUnit']
        )
        pixel.planes.append(plane)
    
    return pixel


def run_ashlar(path_to_file: str, quiet: bool=False, flip_x: bool=False, flip_y: bool=False):

    files = list(filter(lambda x: os.path.isdir(f'{path_to_file}/{x}') and x.startswith('tilescan_'), os.listdir(path_to_file)))
    nb_tilescans = len(files)
    nb_tiles = len(list(filter(lambda x: os.path.isfile(f'{path_to_file}/{files[0]}/{x}') and x.startswith('series_'), os.listdir(f'{path_to_file}/{files[0]}/'))))
    
    filenames = []
    for series_id in range(nb_tiles):
        sid = ''.join([str(0) for _ in range(len(str(nb_tiles)) - len(str(series_id)))]) + str(series_id)
        filenames.append(f"series_{sid}.tiff")
    
    for i in range(nb_tilescans):
        
        paths = [path_to_file + f"/tilescan_{i}/{filename}" for filename in filenames]

        # generate a ome-xml template
        tifffile.imwrite(path_to_file + f'/tilescan_{i}/sample.ome.tif', tifffile.imread(paths[0]))
        sample_ome = ome_types.from_tiff(path_to_file + f'/tilescan_{i}/sample.ome.tif')
        
        pixels = []
        for j in range(nb_tiles):
            with tifffile.TiffFile(paths[j]) as tif:
                metadata = tif.shaped_metadata[0]
                pixels.append(make_ome_pixel(paths[j], sample_ome, metadata))

        omexml = ome_types.model.OME()
        omexml.images = [ome_types.model.Image(pixels=p) for p in pixels]

        out_path = path_to_file + f'/tilescan_{i}/test.companion.ome'
        print(f"Writing to {out_path}\n")
        with open(out_path, 'w') as f:
            f.write(omexml.to_xml())
    
    stack_paths = [f'{path_to_file}/tilescan_{i}/test.companion.ome' for i in range(nb_tilescans)]
    aligner_args = dict(filter_sigma=0.0, max_shift=50, channel=0, verbose=True)
    mosaic_args = dict(verbose=True)
    dst = f'{path_to_file}/ashlar.npy'

    process_single(
        output_path_format=str(dst),
        filepaths=[str(p) for p in stack_paths],
        aligner_args=aligner_args,
        mosaic_args=mosaic_args,
        quiet=quiet,
        flip_x=flip_x,
        flip_y=flip_y,
    )
    
    return float(pixels[0].physical_size_x), float(pixels[0].physical_size_y)


if __name__ == '__main__':
    
    quiet = False
    flip_x = False
    flip_y = False
    
    # path_to_file = os.getcwd() + '/data/pancreatic_cancer/raman'      
    
    # path_to_file = os.getcwd() + '/data/00071845/raman'
    
    # path_to_file = os.getcwd() + '/data/ito/raman'
        
    # path_to_file = os.getcwd() + '/data/00033464/raman'
    
    path_to_file = os.getcwd() + '/data/00071300/raman'
    
    run_ashlar(path_to_file, quiet=quiet, flip_x=flip_x, flip_y=flip_y)

