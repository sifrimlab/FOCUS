import subprocess, os

if __name__ == "__main__":

    # Get a list of the input files in the /data/ directory
    input_files = [f for f in os.listdir("/data/") if "ashlar_input" in f and f.endswith(".ome.tiff")]
    if not input_files:
        raise FileNotFoundError("No input file found with 'ashlar_input' in the name.")
    
    # Convert the relative path to absolute paths
    input_files = [os.path.join("/data/", f) for f in input_files]

    # Execute ASHLAR to stitch the tiles
    result = subprocess.run([
        "ashlar",
        "--align-channel", "16",
        "--output", "/data/ashlar_output.ome.tiff",
        *input_files
    ], capture_output=False, check=False)

    if result.returncode != 0:
        raise RuntimeError(f"ASHLAR stitching failed with error: {result.stderr.decode('utf-8')}")
    
    # Remove the temporary tiles file
    try:
        for f in input_files:
            os.remove(f)
    except OSError as e:
        print(f"Warning: Could not remove temporary tiles file: {e}")