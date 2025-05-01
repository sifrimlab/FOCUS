import copy
import sys
import numpy as np
import numpy.typing as npt
import multiprocessing as mp
import os

module_path = os.path.abspath(os.getcwd())
if module_path not in sys.path:
    sys.path.append(module_path)

from ...utils import execute_indexed_parallel, execute_parallel
from ...registration.registration import AffineRegistrationWithoutFiducials
from threading import Lock
from typing import List
from ashlar import _version, utils
from ashlar.reg import (
    EdgeAligner,
    Mosaic,
    LayerAligner,
    warn_data,
    TiffListWriter,
    Reader,
    BioformatsReader,
)
from ashlar.scripts.ashlar import process_axis_flip
from skimage.transform import rescale
from tifffile import tifffile
from tqdm import tqdm


class ThreadSafeBioformatsReader(BioformatsReader):
    lock = Lock()

    def read(self, *args, **kwargs):
        with self.lock:
            return super().read(*args, **kwargs)


class ParallelMosaic(Mosaic):
    def assemble_channel_parallel(
        self,
        channel: int,
        positions: list[npt.NDArray],
        reader: Reader,
        out: npt.NDArray = None,
        tqdm_args: dict = None,
    ):
        if tqdm_args is None:
            tqdm_args = {}

        if out is None:
            out = np.zeros(self.shape, self.dtype)
        else:
            if out.shape != self.shape:
                raise ValueError(
                    f"out array shape {out.shape} does not match Mosaic"
                    f" shape {self.shape}"
                )
        for si, position in tqdm(enumerate(positions), **tqdm_args):
            img = reader.read(c=channel, series=si)
            img = self.correct_illumination(img, channel)
            utils.paste(out, img, position, func=utils.pastefunc_blend)
        # Memory-conserving axis flips.
        if self.flip_mosaic_x:
            for i in range(len(out)):
                out[i] = out[i, ::-1]
        if self.flip_mosaic_y:
            for i in range(len(out) // 2):
                out[[i, -i - 1]] = out[[-i - 1, i]]
        return out


def make_thumbnail(reader, channel=0, scale=0.05, verbose=False):
    metadata = reader.metadata
    positions = metadata.positions - metadata.origin
    coordinate_max = (positions + metadata.size).max(axis=0)
    mshape = ((coordinate_max + 1) * scale).astype(int)
    mosaic = np.zeros(mshape, dtype=np.uint16)
    total = reader.metadata.num_images
    for i in tqdm(
        range(total),
        desc="           assembling thumbnail",
        total=total,
        file=sys.stdout,
        disable=not verbose,
    ):
        img = reader.read(c=channel, series=i)
        # We don't need anti-aliasing as long as the coarse features in the
        # images are bigger than the scale factor. This speeds up the rescaling
        # dramatically.
        img_s = rescale(img, scale, anti_aliasing=False)
        utils.paste(mosaic, img_s, positions[i] * scale, np.maximum)
    return mosaic


class ParallelEdgeAligner(EdgeAligner):
    def make_thumbnail(self):
        if not self.do_make_thumbnail:
            return
        self.reader.thumbnail = make_thumbnail(
            self.reader, channel=self.channel, verbose=self.verbose
        )

    def compute_threshold(self):
        # Compute error threshold for rejecting aligments. We generate a
        # distribution of error scores for many known non-overlapping image
        # regions and take a certain percentile as the maximum allowable error.
        # The percentile becomes our accepted false-positive ratio.
        false_positive_ratio = 0.05
        edges = self.neighbors_graph.edges
        num_tiles = self.metadata.num_images
        # If not enough tiles overlap to matter, skip this whole thing.
        if len(edges) <= 1:
            self.errors_negative_sampled = np.empty(0)
            self.max_error = np.inf
            return
        widths = np.array([self.intersection(t1, t2).shape.min() for t1, t2 in edges])
        w = widths.max()
        max_offset = self.metadata.size[0] - w
        # Number of possible pairs minus number of actual neighbor pairs.
        num_distant_pairs = num_tiles * (num_tiles - 1) // 2 - len(edges)
        # Reduce permutation count for small datasets -- there are fewer
        # possible truly distinct strips with fewer tiles. The calculation here
        # is just a heuristic, not rigorously derived.
        n = 1000 if num_distant_pairs > 8 else (num_distant_pairs + 1) * 10
        pairs = np.empty((n, 2), dtype=int)
        offsets = np.empty((n, 2), dtype=int)
        # Generate n random non-overlapping image strips. Strips are always
        # horizontal, across the entire image width.
        max_tries = 100
        if self.randomize is False:
            random_state = np.random.RandomState(0)
        else:
            random_state = np.random.RandomState()
        for i in range(n):
            # Limit tries to avoid infinite loop in pathological cases.
            for current_try in range(max_tries):
                t1, t2 = random_state.randint(self.metadata.num_images, size=2)
                o1, o2 = random_state.randint(max_offset, size=2)
                # Check for non-overlapping strips and abort the retry loop.
                if t1 != t2 and (t1, t2) not in edges:
                    # Different, non-neighboring tiles -- always OK.
                    break
                elif t1 == t2 and abs(o1 - o2) > w:
                    # Same tile OK if strips don't overlap within the image.
                    break
                elif (t1, t2) in edges:
                    # Neighbors OK if either strip is entirely outside the
                    # expected overlap region (based on nominal positions).
                    its = self.intersection(t1, t2, np.repeat(w, 2))
                    ioff1, ioff2 = its.offsets[:, 0]
                    if (
                        its.shape[0] > its.shape[1]
                        or o1 < ioff1 - w
                        or o1 > ioff1 + w
                        or o2 < ioff2 - w
                        or o2 > ioff2 + w
                    ):
                        break
            else:
                # Retries exhausted. This should be very rare.
                warn_data("Could not find non-overlapping strips in {max_tries} tries")
            pairs[i] = t1, t2
            offsets[i] = o1, o2

        def register(t1, t2, offset1, offset2):
            img1 = self.reader.read(t1, self.channel)[offset1 : offset1 + w, :]
            img2 = self.reader.read(t2, self.channel)[offset2 : offset2 + w, :]
            _, error = utils.register(img1, img2, self.filter_sigma, upsample=1)
            return error

        # prepare arguments for executor
        args = []
        for (t1, t2), (offset1, offset2) in zip(pairs, offsets):
            arg = (t1, t2, offset1, offset2)
            args.append(copy.deepcopy(arg))

        errors = execute_indexed_parallel(
            register,
            args=args,
            tqdm_args=dict(
                file=sys.stdout,
                disable=not self.verbose,
                desc="    quantifying alignment error",
            ),
        )

        errors = np.array(errors)
        self.errors_negative_sampled = errors
        self.max_error = np.percentile(errors, false_positive_ratio * 100)

    def register_all(self):
        args = []
        for t1, t2 in self.neighbors_graph.edges:
            arg = (t1, t2)
            args.append(copy.deepcopy(arg))

        execute_parallel(
            self.register_pair,
            args=args,
            tqdm_args=dict(
                file=sys.stdout,
                disable=not self.verbose,
                desc="                  aligning edge",
            ),
        )
        if self.verbose:
            print()

        self.all_errors = np.array([x[1] for x in self._cache.values()])
        # Set error values above the threshold to infinity.
        for k, v in self._cache.items():
            if v[1] > self.max_error or np.any(np.abs(v[0]) > self.max_shift_pixels):
                self._cache[k] = (v[0], np.inf)


class ParallelLayerAligner(LayerAligner):
    def make_thumbnail(self):
        self.reader.thumbnail = make_thumbnail(
            self.reader, channel=self.channel, verbose=self.verbose
        )

    def register_all(self):
        n = self.metadata.num_images
        args = [copy.deepcopy((i,)) for i in range(n)]
        results = execute_indexed_parallel(
            self.register,
            args=args,
            tqdm_args=dict(
                file=sys.stdout,
                disable=not self.verbose,
                desc="                  aligning tile",
            ),
        )
        shift, error = list(zip(*results))
        self.shifts = np.array(shift)
        self.errors = np.array(error)
        assert self.shifts.shape == (n, 2)
        assert self.errors.shape == (n,)

        if self.verbose:
            print()


def get_reg(d: np.ndarray, image: np.ndarray):
    
    reg = AffineRegistrationWithoutFiducials(d, image)
    return reg.compute_transformation()


class ParallelNumpyListWriter(TiffListWriter):
    def get_result(self):           
        
        result = []
        for j, mosaic in enumerate(self.mosaics):
            data = mosaic.assemble_channel_parallel(0, mosaic.aligner.positions, mosaic.aligner.reader, tqdm_args=dict(disable=True))
            data = np.ndarray((*data.shape, len(mosaic.channels)), dtype=data.dtype)
            results = execute_indexed_parallel(
                mosaic.assemble_channel_parallel,
                args=[(channel, 
                       mosaic.aligner.positions, 
                       mosaic.aligner.reader, 
                       None, 
                       dict(disable=True)) 
                      for channel in mosaic.channels]
            )
            
            for i, d in enumerate(results):
                data[:, :, i] = d
                
            result.append(data)
                
        return result
    
    def run(self, data: List[np.ndarray]):
        
        data = np.concatenate(data, axis=2)
        
        # Registration seemed necessary to compensate for the drift during the measurement, but the similarity is less apparent after the laser correction so registration is difficult now
        
        # np.save(self.path_format.replace('registered', 'unregistered'), data)
    
        # with mp.Pool(processes=mp.cpu_count()) as pool:  
            
        #     print('Registering subsequent images. This can take several minutes.')
                
        #     parts = 4
            
        #     result = execute_indexed_parallel_mp(pool, get_reg, args=[(data[:, :, 0], data[:, :, j]) for j in range(1, int(data.shape[2] / parts))])
        #     for i, d in result:
        #         data[:, :, i + 1] = d
                    
        #     for i in range(1, parts):
        #         result = execute_indexed_parallel_mp(pool, get_reg, args=[(data[:, :, int(i * data.shape[2] / parts) - 1], data[:, :, j]) for j in range(int(i * data.shape[2] / parts), int((i + 1) * data.shape[2] / parts))])
        #         for j, d in result:
        #             data[:, :, int(i * data.shape[2] / parts) + j] = d
                    
        np.save(self.path_format, data)
        
        
class ParallelTiffListWriter(TiffListWriter):
    def run(self):
        pixel_size = self.mosaics[0].aligner.metadata.pixel_size
        software = f"Ashlar v{_version}"

        def write(
            cycle: int,
            mosaic: ParallelMosaic,
            channel: int,
            path: str,
            positions: list[npt.NDArray],
            reader: Reader,
            tqdm_ind: int,
        ):
            tqdm_args = dict(
                desc=f"    cycle {cycle}, channel {channel}",
                total=len(positions),
                disable=not self.verbose,
                file=sys.stdout,
                position=tqdm_ind,
            )

            with tifffile.TiffWriter(path, bigtiff=True) as tiff:
                tiff.write(
                    data=mosaic.assemble_channel_parallel(
                        channel, positions, reader, tqdm_args=tqdm_args
                    ),
                    software=software.encode("utf-8"),
                    photometric="minisblack",
                )

        tqdm_ind = 0

        args = []
        for cycle, mosaic in enumerate(self.mosaics):
            for channel in mosaic.channels:
                path = self.path_format.format(cycle=cycle, channel=channel)
                positions = mosaic.aligner.positions
                reader = mosaic.aligner.reader

                arg = (cycle, mosaic, channel, path, positions, reader, tqdm_ind)
                args.append(arg)
                # arg_copy = []
                # for a in arg:
                #     if isinstance(a, Mosaic):
                #         arg_copy.append(a)
                #     else:
                #         arg_copy.append(copy.deepcopy(a))
                # args.append(tuple(arg_copy))

                tqdm_ind += 1

        execute_parallel(write, args=args, tqdm_args=dict(disable=True))


def process_single(
    filepaths: list[str],
    output_path_format: str,
    flip_x: bool,
    flip_y: bool,
    aligner_args: dict | None,
    mosaic_args: dict | None,
    quiet: bool,
):
    mosaic_args = mosaic_args.copy()
    mosaics = []
    
    ea_args = aligner_args.copy()
    ea_args["do_make_thumbnail"] = False

    if not quiet:
        print("Stitching and registering input images")
        print("Reading %s" % filepaths[0])

    reader = ThreadSafeBioformatsReader(filepaths[0])
    process_axis_flip(reader, flip_x, flip_y)

    edge_aligner = ParallelEdgeAligner(reader, **ea_args)
    edge_aligner.run()

    mshape = edge_aligner.mosaic_shape
    mosaic_args_final = mosaic_args.copy()
    mosaics.append(ParallelMosaic(edge_aligner, mshape, **mosaic_args_final))

    for filepath in filepaths[1:]:
        if not quiet:
            print("Reading %s" % filepath)

        reader = ThreadSafeBioformatsReader(filepath)
        process_axis_flip(reader, flip_x, flip_y)

        edge_aligner = ParallelEdgeAligner(reader, **ea_args)
        edge_aligner.run()

        mosaic_args_final = mosaic_args.copy()
        mosaics.append(ParallelMosaic(edge_aligner, mshape, **mosaic_args_final))

    # Disable reader caching to save memory during mosaicing and writing.
    edge_aligner.reader = edge_aligner.reader.reader

    if not quiet:
        print()
        print(f"Merging tiles and writing to {output_path_format}")
    writer = ParallelNumpyListWriter(mosaics, output_path_format, verbose=not quiet)
    result = writer.get_result()
    writer.run(result)


def run():
    import os
    path = os.getcwd() + '/data/pancreatic_cancer/raman'
    stack_paths = [f'{path}/tilescan_{i}/test.companion.ome' for i in range(4)]
    aligner_args = dict(filter_sigma=0.0, max_shift=50, channel=0, verbose=True)
    mosaic_args = dict(verbose=True)
    dst = path + '/stitched_and_registered.npy'

    process_single(
        output_path_format=str(dst),
        filepaths=[str(p) for p in stack_paths],
        aligner_args=aligner_args,
        mosaic_args=mosaic_args,
        quiet=False,
        flip_x=False,
        flip_y=False,
    )


if __name__ == "__main__":
    run()