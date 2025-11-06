from transformers import Sam2Processor, Sam2Model
import torch, os, cv2
from PIL import Image
import numpy as np

def center_of_brightness(img, threshold=0):
    if threshold > 0:
        img = np.where(img > threshold, img, 0)
    y_indices, x_indices = np.indices(img.shape)
    total_intensity = img.sum()
    if total_intensity == 0:
        return None  # or (np.nan, np.nan) if no bright region
    y_cog = (y_indices * img).sum() / total_intensity
    x_cog = (x_indices * img).sum() / total_intensity
    return (y_cog, x_cog)

def random_points_in_circle(centroid, image_shape, radius, number_of_points):
    Y, X = image_shape
    center_y, center_x = centroid
    theta = np.random.uniform(0, 2 * np.pi, number_of_points)
    u = np.random.uniform(0, 1, number_of_points)
    s = radius * np.sqrt(u)
    x = center_x + s * np.cos(theta)
    y = center_y + s * np.sin(theta)
    points = list(zip(y, x))

    points = [[int(p[0]), int(p[1])] for p in points]
    return points

if __name__ == "__main__":
    source_dir = "/data/"

    # Initiate the Meta SAM2 model from Huggingface
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = Sam2Model.from_pretrained("facebook/sam2.1-hiera-large").to(device)
    processor = Sam2Processor.from_pretrained("facebook/sam2.1-hiera-large")

    # Load the grayscale mosaic image
    grayscale_mosaic = np.load(os.path.join(source_dir, "grayscale_mosaic.npy"))

    # Saturate the image to enhance contrast and apply Gaussian smoothing
    p2, p98 = np.percentile(grayscale_mosaic, (3, 97))
    grayscale_mosaic = np.clip((grayscale_mosaic - p2) * 255.0 / (p98 - p2), 0, 255).astype(np.uint8)
    grayscale_mosaic = cv2.GaussianBlur(grayscale_mosaic, (5, 5), 2)

    # Compute the centroid of brightness
    centroid = center_of_brightness(grayscale_mosaic, threshold=20)

    # Convert the image into RGB format
    grayscale_mosaic = Image.fromarray(grayscale_mosaic).convert("RGB")

    # Generate a list of random points around the centroid to use as positive labels
    positive_points = random_points_in_circle(
        centroid,
        grayscale_mosaic.size,
        radius=grayscale_mosaic.size[0]//10,
        number_of_points=10
    )

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
    min_area = 0.25 * (grayscale_mosaic.size[0] * grayscale_mosaic.size[1])
    
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