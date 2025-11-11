#!/bin/bash

set -e

# Check if conda command is available
if ! command -v conda &> /dev/null
then
    echo "conda could not be found. Please install conda and make sure it is in your PATH."
    exit 1
fi

# Function to check if conda environment exists
function conda_env_exists() {
    conda info --envs | awk '{print $1}' | grep -Fxq "$1"
}

# Check/create main FOCUS env
if conda_env_exists "FOCUS"; then
    echo "Conda environment 'FOCUS' already exists."
else
    echo "Creating conda environment 'FOCUS' with python=3.11 ..."
    conda create -y -n FOCUS python=3.11
    echo "Installing dependencies from requirements.txt into 'FOCUS'..."
    conda run -n FOCUS pip install -r requirements.txt
fi

# Locate src/tools directory
TOOLS_DIR="tools"
if [ ! -d "$TOOLS_DIR" ]; then
    echo "Directory $TOOLS_DIR does not exist. Exiting."
    exit 1
fi

# Iterate over subfolders of src/tools and create corresponding envs
for subfolder in "$TOOLS_DIR"/*/
do
    # Remove trailing slash and get just folder name
    subfolder_name=$(basename "${subfolder}")
    env_name="FOCUS_${subfolder_name}"

    if conda_env_exists "$env_name"; then
        echo "Conda environment '$env_name' already exists."
    else
        echo "Creating conda environment '$env_name' with python=3.11 ..."
        conda create -y -n "$env_name" python=3.11

        req_file="${TOOLS_DIR}/${subfolder_name}/requirements.txt"
        if [ -f "$req_file" ]; then
            echo "Installing dependencies from $req_file into '$env_name'..."
            conda run -n "$env_name" pip install -r "$req_file"
        else
            echo "Warning: requirements.txt not found in $subfolder_name, skipping dependency install."
        fi

        # Install OpenJDK (Java) in the tools env for Java-dependent tools like ASHLAR
        echo "Installing OpenJDK in '$env_name'..."
        conda install -y -n "$env_name" -c conda-forge openjdk
    fi
done

echo "All environments checked and created as needed."
