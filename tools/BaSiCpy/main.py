import sys
import os
import numpy as np

# Redirect stdout and stderr to null to suppress all printed output
sys.stdout = open(os.devnull, 'w')
sys.stderr = open(os.devnull, 'w')

os.environ["JAX_PLATFORM_NAME"] = "cpu"

from basicpy.basicpy import BaSiC

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(1)

    data_dir = sys.argv[1]
    channel_idx = sys.argv[2]

    input_path = os.path.join(data_dir, f"basic_input_{channel_idx}.npy")
    output_path = os.path.join(data_dir, f"basic_output_{channel_idx}.npy")

    input_data = np.load(input_path)

    basic = BaSiC()
    basic.fit(input_data)
    output_data = basic.transform(input_data)

    np.save(output_path, output_data)
