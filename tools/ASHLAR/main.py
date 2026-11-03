"""Execute the ASHLAR stitching subprocess contract in its separate environment."""

from typing import cast
import sys
import os
import re
import math

_MAX_PYRAMID_PIXELS = (
    3000 * 3000
)  # pixel cap for the smallest pyramid level (GUI rendering)


def _compute_peak_size(H: int, W: int) -> int:
    """Compute peak_size for PyramidWriter so the smallest level stays within _MAX_PYRAMID_PIXELS."""
    total = H * W
    if total <= _MAX_PYRAMID_PIXELS:
        n_levels = 1
    else:
        n_levels = math.ceil(math.log(total / _MAX_PYRAMID_PIXELS, 4)) + 1
    return cast(int, max(1, math.ceil(max(H, W) / (2 ** (n_levels - 1)))))


def _cycle_key(path: str) -> tuple[int, str]:
    """Sort key of an ``ashlar_input_cycle_<n>.ome.tiff`` file: its cycle number, then its name."""
    match = re.search(r"cycle_(\d+)", os.path.basename(path))
    return (int(match.group(1)) if match else 0, path)


def main() -> int:
    """Parse command-line arguments and execute the requested workflow."""
    if len(sys.argv) != 3:
        print(
            "Usage: python main_ashlar.py <data_directory> <align_channel>[,<align_channel>...]",
            flush=True,
        )
        return 1

    data_dir = sys.argv[1]
    align_channels = [int(channel) for channel in sys.argv[2].split(",")]

    try:
        # Cycle order (numeric, so cycle 10 follows cycle 9) matches the alignment channel order.
        input_files = sorted(
            [
                os.path.join(data_dir, f)
                for f in os.listdir(data_dir)
                if "ashlar_input" in f and f.endswith(".ome.tiff")
            ],
            key=_cycle_key,
        )
        if not input_files:
            raise FileNotFoundError(
                "No input file found with 'ashlar_input' in the name."
            )

        output_file = os.path.join(data_dir, "ashlar_output.ome.tiff")

        stitch_files(input_files, output_file, align_channels)
        remove_tiles(input_files)

    except FileNotFoundError as e:
        print(f"Error: {e}", flush=True)
        return 2

    except Exception as e:
        print(f"Unexpected error: {e}", flush=True)
        return 99

    return 0


def stitch_files(
    input_files: list[str], output_file: str, align_channels: list[int]
) -> None:
    """Align ordered OME cycles and write the original ASHLAR multiresolution mosaic.

    ``align_channels`` holds one channel per cycle, indexed within that cycle's file; a single
    value applies to every cycle.
    """
    from ashlar.scripts.ashlar import build_reader, process_axis_flip
    from ashlar import reg

    if len(align_channels) == 1:
        align_channels = align_channels * len(input_files)
    if len(align_channels) != len(input_files):
        raise ValueError(
            f"{len(align_channels)} alignment channels for {len(input_files)} cycle files"
        )

    ea_args = {
        "channel": align_channels[0],
        "verbose": True,
        "max_shift": 15,
    }
    if len(input_files) == 1:
        ea_args["do_make_thumbnail"] = False

    reader = build_reader(input_files[0])
    process_axis_flip(reader, False, False)
    edge_aligner = reg.EdgeAligner(reader, **ea_args)
    edge_aligner.run()
    mshape = edge_aligner.mosaic_shape

    mosaics = [reg.Mosaic(edge_aligner, mshape)]

    for filepath, channel in zip(input_files[1:], align_channels[1:]):
        reader = build_reader(filepath)
        process_axis_flip(reader, False, False)
        la_args = {"channel": channel, "verbose": True, "max_shift": 15}
        la = reg.LayerAligner(reader, edge_aligner, **la_args)
        la.run()
        mosaics.append(reg.Mosaic(la, mshape))

    # Disable caching before writing to save memory
    edge_aligner.reader = edge_aligner.reader.reader

    H_mosaic, W_mosaic = mshape
    peak_size = _compute_peak_size(H_mosaic, W_mosaic)

    writer = reg.PyramidWriter(mosaics, output_file, peak_size=peak_size, verbose=True)
    writer.run()


def remove_tiles(input_files: list[str]) -> None:
    """Remove source tiles after writing, retaining non-fatal per-file cleanup warnings."""
    for f in input_files:
        try:
            os.remove(f)
        except OSError as e:
            print(f"Warning: Could not remove temporary tiles file: {e}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
