from basicpy.basicpy import BaSiC
import numpy as np
import os

if __name__ == "__main__":
    # Load the input channel data
    input_data = np.load('/data/basic_input.npy')

    # Apply BaSiC correction
    basic = BaSiC()
    basic.fit(input_data)
    output_data = basic.transform(input_data)

    # Save the output data
    np.save('/data/basic_output.npy', output_data)

    # Remove the input data file
    os.remove('/data/basic_input.npy')