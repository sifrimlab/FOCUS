# FOCUS: Multimodal preprocessing and registration pipeline for biomedical data

This data processing pipeline has been designed to accomodate multiple projects in a versatile way
and it's focused on being modality and task agnostic.

## 1. Getting Started

The pipeline can be executed locally using this repository. In principle, no coding is required
to use this pipeline, as the parameters required to function can be defined into a configuration
file. 

### Prerequisites
1. Python 3.11
2. NVIDIA CUDA support
3. Anaconda and Miniconda to manage the enviroinment

### Installation

1. Clone the repo
```sh
git clone https://github.com/sifrimlab/FOCUS FOCUS
```

2. Move to the project repository
```sh
cd FOCUS
```

3. Create a Python enviroinment following the [RAPIDS Instructions](https://docs.rapids.ai/install/). NOTE: If you have a different CUDA version, change the command accordingly
```sh
conda create -n FOCUS -c rapidsai -c conda-forge -c nvidia  \
    rapids=25.04 python=3.12 'cuda-version>=12.0,<=12.8'
```

4. Activate the conda env and install the rest of the requirements
```sh
conda activate FOCUS
pip install --no-cache-dir -r requirements.txt
```