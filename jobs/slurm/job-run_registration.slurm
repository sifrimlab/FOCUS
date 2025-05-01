#!/bin/bash -l
#SBATCH --account=lp_phd_thomas
#SBATCH --cluster=wice
#SBATCH --nodes=1
#SBATCH --partition=batch
#SBATCH --time=10:00:00
#SBATCH --ntasks-per-node=8
#SBATCH --output=raman-maldi/jobs/output/slurm-%j.out

export PATH="${VSC_DATA}/miniconda3/bin:${PATH}"
source activate multimodal
python3 -u raman-maldi/run_pipeline.py -c $1 -r t

