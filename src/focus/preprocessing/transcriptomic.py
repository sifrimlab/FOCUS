import os
import anndata as ad
import numpy as np
import scanpy as sc

from focus.constants import MODALITY_PREPROCESSING, MODALITY_PREPROCESSING_MERGED

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
                return
            
    def preprocess_data(self,
        min_count_per_spot: int | None,
        max_count_per_spot: int | None,
        min_genes_per_spot: int | None = None,
        max_genes_per_spot: int | None = None
        ) -> str:
        '''
        Preprocess the spatial transcriptomic data using ScanPy.
        
        Parameters:
        ----------
        min_count_per_spot:
            Minimum total counts per spot to retain the spot.
        max_count_per_spot:
            Maximum total counts per spot to retain the spot.

        Returns:
        -------
            output_file: str
                Path to the preprocessed AnnData file.
        '''

        if min_count_per_spot is not None and not (min_count_per_spot > 0):
            raise ValueError("min_count_per_spot must be greater than 0.")

        if max_count_per_spot is not None and not (max_count_per_spot > 0):
            raise ValueError("max_count_per_spot must be greater than 0.")
        if min_genes_per_spot is not None and not (min_genes_per_spot > 0):
            raise ValueError("min_genes_per_spot must be greater than 0.")
        if max_genes_per_spot is not None and not (max_genes_per_spot > 0):
            raise ValueError("max_genes_per_spot must be greater than 0.")

        self.load_data()

        self.data.layers['raw'] = self.data.X.copy()

        # Calculate QC metrics (mitochondrial genes, counts, etc.)
        self.data.var['mt'] = self.data.var_names.str.upper().str.startswith('MT-')
        sc.pp.calculate_qc_metrics(self.data, qc_vars=['mt'], inplace=True)

        if min_count_per_spot is not None:
            sc.pp.filter_cells(self.data, min_counts=min_count_per_spot)
        if max_count_per_spot is not None:
            sc.pp.filter_cells(self.data, max_counts=max_count_per_spot)
        if min_genes_per_spot is not None:
            sc.pp.filter_cells(self.data, min_genes=min_genes_per_spot)
        if max_genes_per_spot is not None:
            sc.pp.filter_cells(self.data, max_genes=max_genes_per_spot)

        # Include the sample ID in the AnnData object
        self.data.obs['sample_id'] = self.sample_id

        # Check if the observation names already include the sample ID
        if not (all(obs_name.startswith(f"{self.sample_id}_") for obs_name in self.data.obs_names)):
            # Make the observation names unique
            self.data.obs_names = [f"{self.sample_id}_{obs_name}" for obs_name in self.data.obs_names]

        # Save the preprocessed data
        output_file = MODALITY_PREPROCESSING(self.source_path, self.sample_id, self.modality_name, "h5ad")
        self.data.write_h5ad(output_file)
        return output_file

class SpatialTranscriptomicDataset():
    '''
    Class for handling multiple spatial transcriptomic samples.
    '''

    def __init__(self, path: str, samples: list[SpatialTranscriptomic]) -> None:
        self.path = path
        self.samples = samples

    def process_dataset(self, 
        min_count_per_spot: int | None = None,
        max_count_per_spot: int | None = None,
        min_genes_per_spot: int | None = None,
        max_genes_per_spot: int | None = None,
        min_spots_per_gene: float| None = None,
        min_count_spots_ratio_per_gene: float | None = None,
        total_counts_normalize: bool = True,
        log1p_transform: bool = True,
        force_recomputing: bool = False
    ) -> dict[str, str]:
        '''
        Process and combine multiple spatial transcriptomic samples.
        
        Parameters:
        ----------
        min_count_per_spot:
            Minimum total counts per spot to retain the spot.
        max_count_per_spot:
            Maximum total counts per spot to retain the spot.
        min_genes_per_spot:
            Minimum number of genes detected per spot to retain the spot.
        max_genes_per_spot:
            Maximum number of genes detected per spot to retain the spot.
        min_spots_per_gene:
            Minimum number of spots in which a gene must be expressed to retain the gene.
        min_count_spots_ratio_per_gene:
            Minimum ratio between the number of spots a gene is expressed in and the total count of that gene across those spots.
        total_counts_normalize:
            Whether to perform total counts normalization.
        log1p_transform:
            Whether to perform log1p transformation.
        force_recomputing:
            Whether to force recomputing the preprocessing even if preprocessed data exists.

        Returns:
        -------
            processed_samples: dict[str, str]
                A dictionary with keys as sample identifiers and values as paths to the preprocessed data. It includes the merged data under the key 'merged'.
        '''

        # Check the input parameters
        if min_count_per_spot is not None and not (min_count_per_spot > 0):
            raise ValueError("min_count_per_spot must be greater than 0.")
        if max_count_per_spot is not None and not (max_count_per_spot > 0):
            raise ValueError("max_count_per_spot must be greater than 0.")
        if min_genes_per_spot is not None and not (min_genes_per_spot > 0):
            raise ValueError("min_genes_per_spot must be greater than 0.")
        if max_genes_per_spot is not None and not (max_genes_per_spot > 0):
            raise ValueError("max_genes_per_spot must be greater than 0.")
        if min_spots_per_gene is not None and not (0 < min_spots_per_gene < 1):
            raise ValueError("min_spots_per_gene must be between 0 and 1.")
        if min_count_spots_ratio_per_gene is not None and not (min_count_spots_ratio_per_gene > 0):
            raise ValueError("min_count_spots_ratio_per_gene must be greater than 0.")
        if type(total_counts_normalize) is not bool:
            raise ValueError("total_counts_normalize must be a boolean.")
        if type(log1p_transform) is not bool:
            raise ValueError("log1p_transform must be a boolean.")
        if type(force_recomputing) is not bool:
            raise ValueError("force_recomputing must be a boolean.")
        
        print("1/2 - Processing Spatial Transcriptomic Samples")

        processed_samples: dict[str, str] = {}
        adata_list: list[ad.AnnData] = []
        all_samples_processed = True

        for sample in self.samples:
            print(f"Preprocessing sample {sample.sample_id}")

            if force_recomputing == False:
                # Check if preprocessed data already exists
                preprocessed_file = MODALITY_PREPROCESSING(self.path, sample.sample_id, sample.modality_name, "h5ad")

                if os.path.exists(preprocessed_file):
                    print(f"Sample {sample.sample_id} already preprocessed. Using cached results.")
                    processed_samples[sample.sample_id] = preprocessed_file
                    
                    # Load the preprocessed data for merging
                    adata = sc.read_h5ad(processed_samples[sample.sample_id])
                    adata_list.append(adata)
                    continue
                else:
                    all_samples_processed = False

            processed_samples[sample.sample_id] = sample.preprocess_data(
                min_count_per_spot=min_count_per_spot,
                max_count_per_spot=max_count_per_spot,
                min_genes_per_spot=min_genes_per_spot,
                max_genes_per_spot=max_genes_per_spot
            )

            # Load the preprocessed data for merging
            adata = sc.read_h5ad(processed_samples[sample.sample_id])
            adata_list.append(adata)

        # Combine the data considering that each sample may have different genes
        print("2/2 - Generating combined Spatial Transcriptomic dataset")

        if all_samples_processed and force_recomputing == False and os.path.exists(MODALITY_PREPROCESSING_MERGED(self.path, self.samples[0].modality_name, "h5ad")):
            combined_output_file = MODALITY_PREPROCESSING_MERGED(self.path, self.samples[0].modality_name, "h5ad")
            print("Combined dataset already exists. Using cached results.")
            processed_samples["merged"] = combined_output_file
            return processed_samples
        
        self.combined_data = ad.concat(adata_list)

        # TODO: Move this and the following filter to the sample class
        # Filter genes that are not expressed in at least min_spots_per_gene percentage of spots in the smallest sample
        NUM_SAMPLES_FILTER = 0.05
        print(f"Unfiltered genes in combined dataset: {self.combined_data.n_vars}")
        if min_spots_per_gene is not None:
            samples = self.combined_data.obs['sample_id'].unique()
            num_samples = len(samples)
            
            # Array to count how many samples each gene is kept in
            gene_preserved_counts = np.zeros(self.combined_data.n_vars, dtype=int)

            for sample in samples:
                sample_mask = self.combined_data.obs['sample_id'] == sample
                ad_sample = self.combined_data[sample_mask, :]

                sample_size = ad_sample.n_obs
                min_cells = int(np.floor(sample_size * min_spots_per_gene))

                # Keep mask per gene for current sample
                keep_mask = np.array((ad_sample.X > 0).sum(axis=0)).flatten() >= min_cells

                # Increment count for genes passing in this sample
                gene_preserved_counts += keep_mask.astype(int)

            # Minimum number of samples a gene must be preserved in (at least 5%)
            min_samples_required = np.ceil(NUM_SAMPLES_FILTER * num_samples)

            # Keep genes preserved in >= 5% of samples
            combined_keep_mask = gene_preserved_counts >= min_samples_required

            self.combined_data = self.combined_data[:, combined_keep_mask].copy()
            print(f"Filtered genes in combined dataset after applying min_spots_per_gene={min_spots_per_gene}: {self.combined_data.n_vars}")

        # Filter again the genes based on min_count_spots_ratio_per_gene
        if min_count_spots_ratio_per_gene is not None:
            num_samples = len(self.samples)
            gene_preserved_counts = np.zeros(self.combined_data.n_vars, dtype=int)

            X = self.combined_data.X.toarray() if hasattr(self.combined_data.X, 'toarray') else self.combined_data.X

            for sample in self.samples:
                sample_mask = self.combined_data.obs['sample_id'] == sample.sample_id
                X_sample = X[sample_mask, :]

                expressed_counts = np.sum(X_sample > 0, axis=0)
                total_counts = np.sum(X_sample, axis=0)

                keep_mask = total_counts >= min_count_spots_ratio_per_gene * expressed_counts

                gene_preserved_counts += keep_mask.astype(int)

            min_samples_required = np.ceil(NUM_SAMPLES_FILTER * num_samples)
            combined_keep_mask = gene_preserved_counts >= min_samples_required

            self.combined_data = self.combined_data[:, combined_keep_mask].copy()
            print(f"Filtered genes in combined dataset after applying min_count_spots_ratio_per_gene={min_count_spots_ratio_per_gene}: {self.combined_data.n_vars}")

        if total_counts_normalize:
            sc.pp.normalize_total(self.combined_data, target_sum=1e4, inplace=True)
        if log1p_transform:
            sc.pp.log1p(self.combined_data)

        # Save the combined data
        combined_output_file = MODALITY_PREPROCESSING_MERGED(self.path, self.samples[0].modality_name, "h5ad")
        self.combined_data.write_h5ad(combined_output_file)
       
        processed_samples["merged"] = combined_output_file
        return processed_samples