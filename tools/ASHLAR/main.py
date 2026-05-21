import sys
import os
import math

_MAX_PYRAMID_PIXELS = 3000 * 3000  # pixel cap for the smallest pyramid level (GUI rendering)


def _compute_peak_size(H: int, W: int) -> int:
    """Compute peak_size for PyramidWriter so the smallest level stays within _MAX_PYRAMID_PIXELS."""
    total = H * W
    if total <= _MAX_PYRAMID_PIXELS:
        n_levels = 1
    else:
        n_levels = math.ceil(math.log(total / _MAX_PYRAMID_PIXELS, 4)) + 1
    return max(1, math.ceil(max(H, W) / (2 ** (n_levels - 1))))


def main():
    if len(sys.argv) != 3:
        print("Usage: python main_ashlar.py <data_directory> <align_channel>", flush=True)
        return 1

    data_dir = sys.argv[1]
    align_channel = int(sys.argv[2])

    try:
        input_files = sorted([
            os.path.join(data_dir, f)
            for f in os.listdir(data_dir)
            if "ashlar_input" in f and f.endswith(".ome.tiff")
        ])
        if not input_files:
            raise FileNotFoundError("No input file found with 'ashlar_input' in the name.")

        output_file = os.path.join(data_dir, "ashlar_output.ome.tiff")

        from ashlar.scripts.ashlar import build_reader, process_axis_flip
        from ashlar import reg

        ea_args = {
            'channel': align_channel,
            'verbose': True,
            'max_shift': 15,
        }
        if len(input_files) == 1:
            ea_args['do_make_thumbnail'] = False

        reader = build_reader(input_files[0])
        process_axis_flip(reader, False, False)
        edge_aligner = reg.EdgeAligner(reader, **ea_args)
        edge_aligner.run()
        mshape = edge_aligner.mosaic_shape

        mosaics = [reg.Mosaic(edge_aligner, mshape)]

        la_args = {'channel': align_channel, 'verbose': True, 'max_shift': 15}
        for filepath in input_files[1:]:
            reader = build_reader(filepath)
            process_axis_flip(reader, False, False)
            la = reg.LayerAligner(reader, edge_aligner, **la_args)
            la.run()
            mosaics.append(reg.Mosaic(la, mshape))

        # Disable caching before writing to save memory
        edge_aligner.reader = edge_aligner.reader.reader

        H_mosaic, W_mosaic = mshape
        peak_size = _compute_peak_size(H_mosaic, W_mosaic)

        writer = reg.PyramidWriter(mosaics, output_file, peak_size=peak_size, verbose=True)
        writer.run()

        for f in input_files:
            try:
                os.remove(f)
            except OSError as e:
                print(f"Warning: Could not remove temporary tiles file: {e}", flush=True)

    except FileNotFoundError as e:
        print(f"Error: {e}", flush=True)
        return 2

    except Exception as e:
        print(f"Unexpected error: {e}", flush=True)
        return 99

    return 0


if __name__ == "__main__":
    sys.exit(main())
