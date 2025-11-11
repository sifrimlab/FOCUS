import sys
import subprocess
import os
import shlex

def main():
    if len(sys.argv) != 3:
        print("Usage: python main_ashlar.py <data_directory> <align_channel>", flush=True)
        return 1  # Exit code 1: Argument error

    data_dir = sys.argv[1]
    align_channel = sys.argv[2]

    try:
        # Get a list of input files in the specified data directory
        input_files = [f for f in os.listdir(data_dir) if "ashlar_input" in f and f.endswith(".ome.tiff")]
        if not input_files:
            raise FileNotFoundError("No input file found with 'ashlar_input' in the name.")

        # Convert relative to absolute paths
        input_files = [os.path.join(data_dir, f) for f in input_files]

        output_file = os.path.join(data_dir, "ashlar_output.ome.tiff")

        # Execute ASHLAR to stitch the tiles with the given alignment channel
        cmd_parts = [
            "ashlar",
            "--align-channel", align_channel,
            "--output", output_file,
        ] + input_files
        cmd = " ".join(shlex.quote(part) for part in cmd_parts)

        result = subprocess.run(
            cmd,
            check=True,
            shell=True,
            executable="/bin/bash"
        )

        # Remove the temporary tiles files
        for f in input_files:
            try:
                os.remove(f)
            except OSError as e:
                print(f"Warning: Could not remove temporary tiles file: {e}", flush=True)

    except FileNotFoundError as e:
        print(f"Error: {e}", flush=True)
        return 2  # Exit code 2: File not found

    except subprocess.CalledProcessError as e:
        print(f"ASHLAR stitching failed with error: {e}", flush=True)
        return 3  # Exit code 3: Subprocess error

    except Exception as e:
        print(f"Unexpected error: {e}", flush=True)
        return 99  # Exit code 99: Unknown error

    return 0  # Exit code 0: Success

if __name__ == "__main__":
    sys.exit(main())
