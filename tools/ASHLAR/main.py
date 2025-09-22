import subprocess, os

if __name__ == "__main__":
    # Execute ASHLAR to stitch the tiles
    result = subprocess.run([
        "ashlar",
        "--output", "/data/ashlar_output.ome.tiff",
        "/data/ashlar_input.ome.tiff",
    ], capture_output=False, check=False)

    if result.returncode != 0:
        raise RuntimeError(f"ASHLAR stitching failed with error: {result.stderr.decode('utf-8')}")
    
    # Remove the temporary tiles file
    try:
        os.remove("/data/ashlar_input.ome.tiff")
    except OSError as e:
        print(f"Warning: Could not remove temporary tiles file: {e}")