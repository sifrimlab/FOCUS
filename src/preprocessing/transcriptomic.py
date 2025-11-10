import os, sys, tqdm
import anndata as ad
import numpy as np
import scanpy as sc
import pandas as pd

class SpatialTranscriptomic:
    '''
    Class for handling spot-based spatial transcriptomic data.
    '''

    def __init__(
        self,
        source_path: str, 
        sample_id: str,
        modality_name: str
    ) -> None:

        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Source path {source_path} does not exist.")
        if not os.access(source_path, os.R_OK):
            raise PermissionError(f"Source path {source_path} is not readable.")
        
        self.source_path = source_path
        self.sample_id = sample_id
        self.modality_name = modality_name
        self.input_path = os.path.join(source_path, sample_id, modality_name)
        self.output_path = os.path.join(source_path, sample_id, "preprocessing", modality_name)

        # Create output directory if it doesn't exist
        os.makedirs(self.output_path, exist_ok=True)
        self.load_data()

    def load_data(self) -> None:
        '''
        Load spatial transcriptomic data from the source path.
        The first AnnData object found in the sample directory is loaded.
        '''
        
        # Scan the sample directory for AnnData files
        for file in os.listdir(self.input_path):
            if file.endswith('.h5ad'):
                adata_path = os.path.join(self.input_path, file)
                self.data = sc.read_h5ad(adata_path)
                print(f"Loaded ST data from {adata_path}")
                return
            
    def preprocess_data(self,
        min_count_per_spot: int,
        max_count_per_spot: int,
        min_spots_per_gene: float,
        total_counts_normalize: bool = True,
        log1p_transform: bool = True) -> ad.AnnData:
        '''
        Preprocess the spatial transcriptomic data using ScanPy.
        
        Parameters:
        min_count_per_spot:
            Minimum total counts per spot to retain the spot.
        max_count_per_spot:
            Maximum total counts per spot to retain the spot.
        min_spots_per_gene:
            Minimum percentage of spots in which a gene must be expressed to retain the gene.
        total_counts_normalize:
            Whether to perform total counts normalization.
        log1p_transform:
            Whether to perform log1p transformation.
        '''

        if self.data is None:
            raise ValueError("Data not loaded. Please load data before preprocessing.")

        if not (0.0 <= min_spots_per_gene <= 1.0):
            raise ValueError("min_spots_per_gene must be between 0 and 1.")

        self.data.layers['raw'] = self.data.X.copy()

        # Calculate QC metrics (mitochondrial genes, counts, etc.)
        self.data.var['mt'] = self.data.var_names.str.upper().str.startswith('MT-')
        sc.pp.calculate_qc_metrics(self.data, qc_vars=['mt'], inplace=True)

        sc.pp.filter_cells(self.data, min_genes=min_count_per_spot)
        sc.pp.filter_cells(self.data, max_genes=max_count_per_spot)
        sc.pp.filter_genes(self.data, min_cells=np.floor(self.data.n_obs * min_spots_per_gene))

        if total_counts_normalize:
            sc.pp.normalize_total(self.data, target_sum=1e4, inplace=True)

        if log1p_transform:
            sc.pp.log1p(self.data)

        # Include the sample ID in the AnnData object
        self.data.obs['sample_id'] = self.sample_id

        # Make the observation names unique
        self.data.obs_names = [f"{self.sample_id}_{obs_name}" for obs_name in self.data.obs_names]

        # Save the preprocessed data
        output_file = os.path.join(self.output_path, f"{self.sample_id}.h5ad")
        self.data.write_h5ad(output_file)
        return self.data

class SpatialTranscriptomicDataset():
    '''
    Class for handling multiple spatial transcriptomic samples.
    '''

    def __init__(self, samples: list[SpatialTranscriptomic]) -> None:
        self.samples = samples
        self.combined_data: ad.AnnData = None

    def process_dataset(self, 
        min_count_per_spot: int,
        max_count_per_spot: int,
        min_spots_per_gene: float,
        total_counts_normalize: bool = True,
        log1p_transform: bool = True) -> ad.AnnData:
        '''
        Process and combine multiple spatial transcriptomic samples.
        
        Parameters:
        min_count_per_spot:
            Minimum total counts per spot to retain the spot.
        max_count_per_spot:
            Maximum total counts per spot to retain the spot.
        min_spots_per_gene:
            Minimum number of spots in which a gene must be expressed to retain the gene.
        total_counts_normalize:
            Whether to perform total counts normalization.
        log1p_transform:
            Whether to perform log1p transformation.
        '''

        adata_list = []
        for sample in tqdm.tqdm(self.samples, desc="Processing ST Samples", unit="sample"):
            preprocessed_data = sample.preprocess_data(
                min_count_per_spot=min_count_per_spot,
                max_count_per_spot=max_count_per_spot,
                min_spots_per_gene=min_spots_per_gene,
                total_counts_normalize=total_counts_normalize,
                log1p_transform=log1p_transform
            )
            adata_list.append(preprocessed_data)

        # Combine the data considering that each sample may have different genes
        self.combined_data = ad.concat(adata_list)

        # Save the combined data
        combined_output_path = os.path.join(
            self.samples[0].source_path,
            "merged",
            "preprocessing"
        )
        os.makedirs(os.path.dirname(combined_output_path), exist_ok=True)

        combined_output_file = os.path.join(combined_output_path, f"{self.samples[0].modality_name}.h5ad")
        self.combined_data.write_h5ad(combined_output_file)
        return self.combined_data