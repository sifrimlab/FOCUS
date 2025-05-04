import tkinter as tk
import tkinter.scrolledtext as scroll
import numpy as np
from tkinter import ttk
from PIL import Image, ImageTk
from typing import Callable, List
import screeninfo

from constants import LandmarkSelectionGUIWidgets

class ZoomableImage(tk.Frame):
    def __init__(self, parent: tk.Tk, image: np.ndarray, image_tag: str, selected_landmark_callback: Callable):
        
        super().__init__(parent)

        self.canvas = tk.Canvas(self, cursor="cross")
        self.scroll_x = tk.Scrollbar(self, orient="horizontal")
        self.scroll_y = tk.Scrollbar(self, orient="vertical")
        
        # Configure grid
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll_x.grid(row=1, column=0, sticky="ew")
        self.scroll_y.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        # Image handling
        self._original_pil_image = self._nparray_to_pil(image)
        self._display_image = self._original_pil_image.copy()
        self._tk_image = None
        self.image_id = None
        self.image_tag = image_tag
        
        # Zoom and scroll tracking
        self.zoom_level = 1.0
        self.canvas.config(xscrollcommand=self.scroll_x.set, yscrollcommand=self.scroll_y.set)
        self.scroll_x.config(command=self.canvas.xview)
        self.scroll_y.config(command=self.canvas.yview)
        self.canvas.config(scrollregion=self.canvas.bbox("all"))
        self.DOT_SIZE = 20
        
        # Bind events
        self.canvas.bind("<Configure>", self._fit_to_canvas)
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<MouseWheel>", self.zoom_mousewheel)  # Windows mouse scroll
        self.canvas.bind("<Button-4>", self.zoom_mousewheel)    # Linux scroll up
        self.canvas.bind("<Button-5>", self.zoom_mousewheel)    # Linux scroll down
        self.selected_landmark_callback = selected_landmark_callback

        # Store the selected landmarks to draw the dots
        self.selected_landmarks: List[tuple[int, int]] = []

    def _nparray_to_pil(self, array: np.ndarray) -> Image.Image:
        '''
        Convert a numpy array to a PIL image.

        Parameters
        ----------
        array : np.ndarray
            The numpy array to convert.

        Returns
        ----------
        Image.Image
            The converted PIL image.
        '''
        if not isinstance(array, np.ndarray):
            raise TypeError("The input must be a numpy array.")
        

        # Ensure proper array format
        if array.dtype != np.uint8:
            array = (array * 255).astype(np.uint8)

        # Handle grayscale/RGB conversion
        if len(array.shape) == 2:  # Grayscale
            pil_img = Image.fromarray(array, mode='L')
        else:  # RGB (assuming 3-channel array)
            pil_img = Image.fromarray(array, mode='RGB')
            
        return pil_img

    def _render_image(self) -> None:
        '''
        Render the image on the canvas with the current zoom level.
        '''

        self._tk_image = ImageTk.PhotoImage(self._display_image, master = self.canvas)

        if self.image_id is None:
            self.image_id = self.canvas.create_image(0, 0, anchor="nw", image=self._tk_image)
        else:
            self.canvas.itemconfig(self.image_id, image=self._tk_image)

        self.canvas.config(scrollregion=self.canvas.bbox(self.image_id))

    def _fit_to_canvas(self, event = None) -> None:
        '''
        Fit the image to the canvas size.
        This method is called when the canvas is resized.
        '''

        # Get the current size of the canvas
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()

        img_width, img_height = self._original_pil_image.size
        
        # Calculate the scaling factor to fit the image to the canvas
        scale_w = canvas_width / img_width
        scale_h = canvas_height / img_height
        self.zoom_level = min(scale_w, scale_h)

        self._rescale_image()

    def _rescale_image(self) -> None:
        new_size = (
            max(1, int(self._original_pil_image.width * self.zoom_level)),
            max(1, int(self._original_pil_image.height * self.zoom_level)),
        )
        self._display_image = self._original_pil_image.resize(new_size)
        self._render_image()

    def on_click(self, event):
        '''
        Event callback to handle the mouse click on the canvas.
        It converts the canvas coordinates to image coordinates and calls the callback function.
        It also draws a dot on the canvas at the clicked position.
        '''

        # Convert canvas coordinates to image coordinates
        x_canvas = int(self.canvas.canvasx(event.x))
        y_canvas = int(self.canvas.canvasy(event.y))
        
        # Adjust for zoom and scroll
        x_image = int(x_canvas / self.zoom_level)
        y_image = int(y_canvas / self.zoom_level)
        
        # Save the landmark and draw a dot
        self.selected_landmarks.append((x_image, y_image))
        self.selected_landmark_callback(x_image, y_image, self.image_tag)
        self.draw_dots(x_canvas, y_canvas)

    def draw_dots(self) -> None:
        '''
        Draw dots on the canvas for each selected landmark.
        Before drawing, it clears the previous dots.
        '''

        # Delete all the previous dots
        self.canvas.delete("dot")

        # Draw dots for each selected landmark
        for x, y in self.selected_landmarks:
            self.canvas.create_oval(
                x - self.DOT_SIZE, y - self.DOT_SIZE,
                x + self.DOT_SIZE, y + self.DOT_SIZE,
                fill = "red",
                tags = "dot"
            )

    def zoom_mousewheel(self, event) -> None:
        '''
        Event callback to handle the mouse wheel scroll for zooming in/out.
        '''

        # Get the mouse position relative to the canvas
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)

        # Cross-platform handling
        if event.num == 4 or event.delta > 0:
            zoom_factor = 1.1
        elif event.num == 5 or event.delta < 0:
            zoom_factor = 0.9
        else:
            return  # No effective zoom

        new_zoom = self.zoom_level * zoom_factor
        # Clamp zoom to prevent excessive zooming
        if new_zoom < 20:
            self.zoom_level = new_zoom
            # Scale all canvas items
            self.canvas.scale("all", x, y, zoom_factor, zoom_factor)

            # Update scroll region
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

            self.draw_dots()

    def update_selected_landmarks(self, landmarks: List[tuple[int, int]]) -> None:
        '''
        Update the selected landmarks and redraw the dots.

        Parameters
        ----------
        landmarks : List[tuple[int, int]]
            The list of selected landmarks.
        '''

        if not isinstance(landmarks, list):
            raise TypeError("The landmarks must be a list of tuples.") 
        if not all(isinstance(landmark, tuple) and len(landmark) == 2 for landmark in landmarks):
            raise TypeError("Each landmark must be a tuple of two integers.")
        
        # Update the selected landmarks and redraw the dots
        self.selected_landmarks = landmarks
        self.draw_dots()

    def reset_zoom(self) -> None:
        '''
        Reset the zoom level to the original size.
        '''

        self.zoom_level = 1.0
        self.canvas.config(scrollregion=self.canvas.bbox("all"))
        self.canvas.delete("dot")
        self.draw_dots()

class LandmarkSelectionGUI(tk.Frame):
    '''
    A GUI to allow the user to select landmarks on two images to proceed with alignment.

    Parameters
    ----------
    fixed_image : np.ndarray
        The fixed image to be used for alignment.
    moving_image : np.ndarray
        The moving image to be aligned to the fixed image.
    save_landmarks_callback : Callable
        A callback function to save the selected landmarks.
    '''

    def __init__(self, fixed_image: np.ndarray, moving_image: np.ndarray, save_landmarks_callback: Callable):

        if not isinstance(fixed_image, np.ndarray) or not isinstance(moving_image, np.ndarray):
            raise TypeError("The images must be numpy arrays.")
        if not callable(save_landmarks_callback):
            raise TypeError("The callback function must be callable.")
        
        self.save_landmarks_callback = save_landmarks_callback
        self.fixed_image = fixed_image
        self.moving_image = moving_image
        
        # Verify if the application can access a display and retrive the screen size
        self.screen_size: tuple[int, int, int] = (0, 0)
        try:
            for screen in screeninfo.get_monitors():
                if screen.is_primary:
                    self.screen_size = (screen.width, screen.height)
                    break
        except Exception as e:
            raise RuntimeError("Could not access the display. Please check your display settings.") from e

        # Define the TKinter window
        self.root = tk.Tk()
        #ttk.Style().theme_use('arc')
        self.root.state('normal')
        self.root.title('Landmark Selection Tool')

        # Store the widgets that constitute the GUI
        self.widgets = dict()

        # Define the list of inserted landmarks, with a tag to identify the image
        self.landmarks: list[tuple[str, tuple[int, int]]] = []  # (image, (x, y))

        super().__init__(self.root, width = self.screen_size[0], height = self.screen_size[1])

        # Create the layout of the GUI
        self._create_layout()

    def _write_to_console(self, text: str) -> None:
        '''
        Write a message to console

        Parameters
        ----------
        text : str
            The message to be written to the console
        '''

        if type(text) != str:
            raise TypeError("The text must be a string.")
        
        if LandmarkSelectionGUIWidgets.CONSOLE not in self.widgets:
            raise RuntimeError("The console widget is not defined.")
        else:
            console: scroll.ScrolledText = self.widgets[LandmarkSelectionGUIWidgets.CONSOLE]
        
        console.config(state=tk.NORMAL)
        console.see("end")
        console.insert(tk.END, text + '\n')
        console.config(state=tk.DISABLED)

    def _clear_selection(self) -> None:
        '''
        Clear the landmarks selection and start again
        '''

        self.landmarks = []

        # Update the images to remove the landmarks
        self.widgets[LandmarkSelectionGUIWidgets.FIXED_IMAGE].update_selected_landmarks([])
        self.widgets[LandmarkSelectionGUIWidgets.MOVING_IMAGE].update_selected_landmarks([])

        self._write_to_console("All landmarks have been removed")
        
    def _undo_selection(self) -> None:
        '''
        Remove the last inserted landmark
        '''

        if len(self.landmarks) > 0:
            removed_landmark = self.landmarks.pop()

            # Update the images to remove the landmark
            self.widgets[LandmarkSelectionGUIWidgets.FIXED_IMAGE].update_selected_landmarks([landmark[1] for landmark in self.landmarks if landmark[0] == "fixed"])
            self.widgets[LandmarkSelectionGUIWidgets.MOVING_IMAGE].update_selected_landmarks([landmark[1] for landmark in self.landmarks if landmark[0] == "moving"])

            self._write_to_console(f"The landmark {removed_landmark} has been removed from the {removed_landmark[0]} image")
        else:
            self._write_to_console("There are no more landmarks to remove.")

    def _reset_zoom(self) -> None:
        '''
        Reset the zoom level of the images to the original size
        '''

        self.widgets[LandmarkSelectionGUIWidgets.FIXED_IMAGE].reset_zoom()
        self.widgets[LandmarkSelectionGUIWidgets.MOVING_IMAGE].reset_zoom()

    def _confirm_selection(self) -> None:
        '''
        Confirm the selected landmarks, trigger the callback and close the GUI.
        Before this, perform some checks to ensure the landmarks are valid.
        '''

        # Separate the landmarks based on the image they belong to
        fixed_landmarks = np.array([landmark[1] for landmark in self.landmarks if landmark[0] == "fixed"], dtype = np.int32)
        moving_landmarks = np.array([landmark[1] for landmark in self.landmarks if landmark[0] == "moving"], dtype = np.int32)

        if fixed_landmarks.shape[0] != moving_landmarks.shape[0]:
            self._write_to_console("The number of landmarks in the fixed and moving images do not match. Please select the same number of landmarks.")
            return
        if fixed_landmarks.shape[0] < 3:
            self._write_to_console("At least 3 landmarks are required to perform the registration. Please select more landmarks.")
            return

        # Call the callback to save the landmarks for alignment
        self.save_landmarks_callback(fixed_landmarks, moving_landmarks)
        self._write_to_console("Landmarks have been saved for alignment.")

        # Close the GUI and deallocate the resources
        self.root.quit()
        self.root.destroy()
                
    def _insert_landmark(self, x: int, y: int, image_tag: str) -> None:
        '''
        Insert a landmark at the specified coordinates in the specified image.

        Parameters
        ----------
        x : int
            The x-coordinate of the landmark.
        y : int
            The y-coordinate of the landmark.
        image_tag : str
            The tag of the image where the landmark is inserted.
        '''

        if image_tag not in [LandmarkSelectionGUIWidgets.FIXED_IMAGE, LandmarkSelectionGUIWidgets.MOVING_IMAGE]:
            raise ValueError("The image tag must be either 'fixed' or 'moving'.")
        
        self.landmarks.append((image_tag, (x, y)))
        self._write_to_console(f"The landmark ({x}, {y}) has been added to the {image_tag} image.")

    def _create_layout(self) -> None:
        '''
        Create the layout of the GUI.
        This method creates the canvas and align the widgets
        '''

        # Main container frame
        main_frame = tk.Frame(self.root)
        main_frame.grid(row=0, column=0, sticky="nsew")
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # Top row for images
        top_frame = tk.Frame(main_frame)
        top_frame.grid(row=0, column=0, sticky="nsew")
        main_frame.grid_rowconfigure(0, weight=3)


        # Bottom row for infobox, buttons, console
        bottom_frame = tk.Frame(main_frame)
        bottom_frame.grid(row=1, column=0, sticky="nsew")
        main_frame.grid_rowconfigure(1, weight=1)

        # Make the design responsive
        top_frame.grid_columnconfigure(0, weight=1)
        top_frame.grid_columnconfigure(1, weight=1)
        top_frame.grid_rowconfigure(0, weight=1)

        bottom_frame.grid_columnconfigure(0, weight=1)
        bottom_frame.grid_columnconfigure(1, weight=0)  # buttons shouldn't expand
        bottom_frame.grid_columnconfigure(2, weight=1)
        bottom_frame.grid_rowconfigure(0, weight=1)

        # Top row: show the two images
        self.widgets[LandmarkSelectionGUIWidgets.FIXED_IMAGE] = ZoomableImage(top_frame, self.fixed_image, "fixed", self._insert_landmark)
        #self.widgets[LandmarkSelectionGUIWidgets.MOVING_IMAGE] = ZoomableImage(top_frame, self.moving_image, "moving", self._insert_landmark)
        self.widgets[LandmarkSelectionGUIWidgets.FIXED_IMAGE].grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        #self.widgets[LandmarkSelectionGUIWidgets.MOVING_IMAGE].grid(row=0, column=1, padx=5, pady=5, sticky="nsew")

        # Bottom row: show the infobox, buttons and console
        self.widgets[LandmarkSelectionGUIWidgets.INFOBOX] = tk.Text(bottom_frame, height=10, state = tk.DISABLED)
        self.widgets[LandmarkSelectionGUIWidgets.INFOBOX].grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        
        buttons_frame = tk.Frame(bottom_frame)
        buttons_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")

        # Create the buttons
        self.widgets[LandmarkSelectionGUIWidgets.CLEAR_LANDMARKS] = tk.Button(buttons_frame, text="Clear Landmarks", command=self._clear_selection)
        self.widgets[LandmarkSelectionGUIWidgets.UNDO] = tk.Button(buttons_frame, text="Undo", command=self._undo_selection)
        self.widgets[LandmarkSelectionGUIWidgets.CONFIRM] = tk.Button(buttons_frame, text="Confirm", command=self._confirm_selection)
        self.widgets[LandmarkSelectionGUIWidgets.RESET_ZOOM] = tk.Button(buttons_frame, text="Reset Zoom", command=self._reset_zoom)

        self.widgets[LandmarkSelectionGUIWidgets.CLEAR_LANDMARKS].pack(pady = 2)
        self.widgets[LandmarkSelectionGUIWidgets.UNDO].pack(pady = 2)
        self.widgets[LandmarkSelectionGUIWidgets.CONFIRM].pack(pady = 2)
        self.widgets[LandmarkSelectionGUIWidgets.RESET_ZOOM].pack(pady = 2)

        # Create the console
        self.widgets[LandmarkSelectionGUIWidgets.CONSOLE] = scroll.ScrolledText(bottom_frame, height=10, state = tk.DISABLED)
        self.widgets[LandmarkSelectionGUIWidgets.CONSOLE].grid(row=0, column=2, padx=5, pady=5, sticky="nsew")

        # Fill the infobox with instructions
        info_lines = [
            "Add a landmark by left-clicking on the image you want to add the point to."
            "Zoom in and out using the mouse wheel.",
            "Scroll the image using the scrollbars.",
            "Delete all the landmarks by clicking the 'Clear Landmarks' button.",
            "Remove the last inserted landmark by clicking the 'Undo' button.",
            "Reset the zoom level by clicking the 'Reset Zoom' button.",
            "Confirm the selected landmarks by clicking the 'Confirm' button.",
            "At least 3 landmarks are required to perform the registration.",
        ]
        self.widgets[LandmarkSelectionGUIWidgets.INFOBOX].config(state=tk.NORMAL)
        self.widgets[LandmarkSelectionGUIWidgets.INFOBOX].insert(tk.END, "\n".join(info_lines))
        self.widgets[LandmarkSelectionGUIWidgets.INFOBOX].config(state=tk.DISABLED)

        # Constrain the window to fit the elements
        self.root.update()
        self.root.minsize(self.root.winfo_width(), self.root.winfo_height())

