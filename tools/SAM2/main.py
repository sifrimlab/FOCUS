from transformers import Sam2Processor, Sam2Model
import torch, os, cv2
from PIL import Image
import numpy as np

def find_distributed_brightness_centers(img, n_regions, threshold=0, region_overlap=0.1):
    """
    Trova fino a N centri di luminosità distribuiti uniformemente nell'immagine.
    
    Args:
        img: Immagine in scala di grigi (numpy array 2D)
        n_regions: Numero di regioni in cui dividere l'immagine (ritorna al massimo N punti)
        threshold: Soglia di luminosità minima per considerare una regione valida
        region_overlap: Percentuale di overlap tra regioni adiacenti (0.0-1.0)
    
    Returns:
        centers: Lista di tuple (y, x) in coordinate globali dell'immagine
    """
    height, width = img.shape
    
    # Calcola la griglia NxN ottimale
    grid_size = int(np.ceil(np.sqrt(n_regions)))
    
    # Dimensioni di ogni regione
    region_height = height / grid_size
    region_width = width / grid_size
    
    # Calcola l'overlap in pixel
    overlap_h = int(region_height * region_overlap)
    overlap_w = int(region_width * region_overlap)
    
    centers = []
    
    for i in range(grid_size):
        for j in range(grid_size):
            # Se abbiamo già trovato n_regions centri, fermati
            if len(centers) >= n_regions:
                break
            
            # Calcola i bounds della regione corrente con overlap
            y_start = max(0, int(i * region_height) - overlap_h)
            y_end = min(height, int((i + 1) * region_height) + overlap_h)
            x_start = max(0, int(j * region_width) - overlap_w)
            x_end = min(width, int((j + 1) * region_width) + overlap_w)
            
            # Estrai la regione
            region = img[y_start:y_end, x_start:x_end]
            
            # Applica la soglia se specificata
            if threshold > 0:
                region_thresholded = np.where(region > threshold, region, 0)
            else:
                region_thresholded = region
            
            # Calcola il centro di luminosità nella regione
            total_intensity = region_thresholded.sum()
            
            # Se l'intensità totale è sotto la soglia, salta questa regione
            if total_intensity == 0:
                continue
            
            # Calcola il centro di massa nella regione locale
            y_indices, x_indices = np.indices(region.shape)
            y_local = (y_indices * region_thresholded).sum() / total_intensity
            x_local = (x_indices * region_thresholded).sum() / total_intensity
            
            # Converti in coordinate globali
            y_global = y_start + y_local
            x_global = x_start + x_local
            
            # Verifica che il punto sia valido e dentro i bounds dell'immagine
            if 0 <= y_global < height and 0 <= x_global < width:
                centers.append((int(y_global), int(x_global)))
        
        if len(centers) >= n_regions:
            break
    
    return centers


def refine_brightness_centers(img, initial_centers, window_size=50, threshold=0):
    """
    Raffina i centri di luminosità muovendoli verso il punto più luminoso in una finestra locale.
    
    Args:
        img: Immagine in scala di grigi
        initial_centers: Lista di tuple (y, x) - centri iniziali
        window_size: Dimensione della finestra di ricerca intorno a ogni centro
        threshold: Soglia di luminosità minima
    
    Returns:
        refined_centers: Lista di tuple (y, x) - centri raffinati
    """
    height, width = img.shape
    refined_centers = []
    
    for center_y, center_x in initial_centers:
        # Definisci la finestra locale
        y_start = max(0, center_y - window_size // 2)
        y_end = min(height, center_y + window_size // 2)
        x_start = max(0, center_x - window_size // 2)
        x_end = min(width, center_x + window_size // 2)
        
        # Estrai la finestra
        window = img[y_start:y_end, x_start:x_end]
        
        # Applica soglia
        if threshold > 0:
            window_thresholded = np.where(window > threshold, window, 0)
        else:
            window_thresholded = window
        
        # Trova il centro di luminosità nella finestra
        total_intensity = window_thresholded.sum()
        
        if total_intensity == 0:
            continue  # Salta questo centro se sotto soglia
        
        y_indices, x_indices = np.indices(window.shape)
        y_local = (y_indices * window_thresholded).sum() / total_intensity
        x_local = (x_indices * window_thresholded).sum() / total_intensity
        
        # Converti in coordinate globali
        y_refined = y_start + y_local
        x_refined = x_start + x_local
        
        refined_centers.append((int(y_refined), int(x_refined)))
    
    return refined_centers


# Versione combinata (consigliata)
def find_n_brightness_centers(img, n_regions, threshold=0, refine=True, 
                               region_overlap=0.1, refinement_window=50):
    """
    Trova fino a N centri di luminosità distribuiti nell'immagine.
    
    Args:
        img: Immagine in scala di grigi (numpy array 2D)
        n_regions: Numero massimo di centri da trovare
        threshold: Soglia di luminosità minima (0-255)
        refine: Se True, raffina i centri verso il punto più luminoso locale
        region_overlap: Overlap tra regioni adiacenti (0.0-1.0)
        refinement_window: Dimensione della finestra per il raffinamento
    
    Returns:
        centers: Lista di tuple (y, x) in coordinate globali (al massimo n_regions elementi)
    """
    # Trova i centri iniziali distribuiti uniformemente
    initial_centers = find_distributed_brightness_centers(
        img, n_regions, threshold, region_overlap
    )
    
    if not initial_centers:
        return []
    
    # Raffina i centri se richiesto
    if refine:
        refined_centers = refine_brightness_centers(
            img, initial_centers, refinement_window, threshold
        )
        return refined_centers
    else:
        return initial_centers

if __name__ == "__main__":
    source_dir = "/data/"

    # Initiate the Meta SAM2 model from Huggingface
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = Sam2Model.from_pretrained("facebook/sam2.1-hiera-large").to(device)
    processor = Sam2Processor.from_pretrained("facebook/sam2.1-hiera-large")

    # Load the grayscale mosaic image
    grayscale_mosaic: np.ndarray = np.load(os.path.join(source_dir, "grayscale_mosaic.npy"))

    # Saturate the image to enhance contrast and apply Gaussian smoothing
    p2, p98 = np.percentile(grayscale_mosaic, (3, 97))
    grayscale_mosaic_processed = np.clip((grayscale_mosaic - p2) * 255.0 / (p98 - p2), 0, 255).astype(np.uint8)
    grayscale_mosaic_processed = cv2.GaussianBlur(grayscale_mosaic_processed, (5, 5), 2)

    N_REGIONS = 16  # Numero di regioni da esplorare
    brightness_centers = find_n_brightness_centers(
        img=grayscale_mosaic_processed,
        n_regions=N_REGIONS,
        threshold=20,
        refine=True,
        region_overlap=0.15,  # 15% di overlap tra regioni
        refinement_window=grayscale_mosaic.shape[0] // N_REGIONS  # Finestra di 100 pixel per raffinamento
    )
    
    print(f"Found {len(brightness_centers)} brightness centers out of {N_REGIONS} regions")

    # Convert the image into RGB format
    grayscale_mosaic_rgb = Image.fromarray(grayscale_mosaic_processed).convert("RGB")

    positive_points = [[int(x), int(y)] for y, x in brightness_centers]

    # Take the four corners as negative labels
    negative_points = [
        [0, 0],
        [0, grayscale_mosaic.size[0]-1],
        [grayscale_mosaic.size[1]-1, 0],
        [grayscale_mosaic.size[1]-1, grayscale_mosaic.size[0]-1]
    ]

    # Define the input labels
    input_points = [[positive_points + negative_points]]
    input_labels = [[[1]*len(positive_points) + [0]*len(negative_points)]]

    model_inputs = processor(
        images=grayscale_mosaic,
        input_points=input_points,
        input_labels=input_labels,
        return_tensors="pt"
    ).to(device)

    # Predict the segmentation mask
    with torch.no_grad():
        model_outputs = model(**model_inputs, multimask_output=False)

    masks = processor.post_process_masks(
        model_outputs.pred_masks.cpu(),
        model_inputs["original_sizes"]
    )[0]

    # Check if the segmentation mask is at least 25% of the image area
    segmentation_mask = masks[0][0].numpy()
    min_area = 0.0 * (grayscale_mosaic.size[0] * grayscale_mosaic.size[1])
    
    # If the segmentation is too small, use the entire image as the mask
    if segmentation_mask.sum() < min_area:
        segmentation_mask = np.ones_like(segmentation_mask)
    else:
        # Find the largest contour in the segmentation mask
        contours, _ = cv2.findContours(segmentation_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        largest_contour = max(contours, key=cv2.contourArea)

        # Create a new mask with only the largest contour filled in
        new_mask = np.zeros_like(segmentation_mask, dtype=np.uint8)
        cv2.drawContours(new_mask, [largest_contour], -1, 1, thickness=cv2.FILLED)
        segmentation_mask = new_mask

    # Save the segmentation mask
    np.save(os.path.join(source_dir, "segmentation_mask.npy"), segmentation_mask)
