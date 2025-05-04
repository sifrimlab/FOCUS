import matplotlib.pyplot as plt
import tkinter as tk
import tkinter.scrolledtext as scroll
import numpy as np
import matplotlib.axis

from typing import Callable, List
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.axes import Axes
import screeninfo





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
        self.root.state('normal')
        self.root.title('Landmark Selection Tool')

        super().__init__(self.root, width = self.screen_size[0], height = self.screen_size[1])

        

    def createWidgets(self):
        
        fig, self.axs = plt.subplots(1, 2, figsize=(self.screen_width / self.dpi, 0.7 * self.screen_height / self.dpi), sharex=False, sharey=False)
        self.canvas = FigureCanvasTkAgg(fig, master=self.root)
        
        self.canvas.get_tk_widget().pack()
        # creating the Matplotlib toolbar 
        toolbar = NavigationToolbar2Tk(self.canvas, self.root) 
        toolbar.update() 
    
        # placing the toolbar on the Tkinter window 
        self.canvas.get_tk_widget().pack()
        
        left_frame = tk.Frame(self.root)
        commands_label = tk.Label(left_frame, text='COMMANDS')
        commands = ['reset', 'undo', 'share', 'register', '1', '2', 'r + 1', 'r + 2']
        length_command = max([len(command) for command in commands]) + 4
        text = [f'{"1" : <{length_command}} Add a control point to image 1 at cursor location',
               f'{"2" : <{length_command}} Add a control point to image 2 at cursor location',
               f'{"r + 1" : <{length_command}} Remove closest control point to cursor of image 1',
               f'{"r + 2" : <{length_command}} Remove closest control point to cursor of image 2',
               f'{"reset" : <{length_command}} Remove all control points in both images',
               f'{"share" : <{length_command}} Toggle sharing of axes between image 1 and image 2',
               f'{"undo" : <{length_command}} Undo placing of last control point',
               f'{"register" : <{length_command}} Register images based on control points of\n{"" : <{length_command}} both images and close tool']
        self.text = tk.Text(left_frame, height=len(commands), width=max([len(s.split("\n")[0]) for s in text]) + 1)
        self.text.insert(tk.END, '\n'.join(text))
        self.text.config(state=tk.DISABLED)
        commands_label.pack(side='top')
        self.text.pack(side='top')
        left_frame.pack(side='left', expand=True)
 
        middle_frame = tk.Frame(self.root)
        middle_left_frame = tk.Frame(middle_frame)
        max_width = max([len(s) for s in commands])
        self.sharebutton = tk.Button(master=middle_left_frame, text='share', width=max_width, command=self.share)
        self.plotbutton = tk.Button(master=middle_left_frame, text="reset", width=max_width, command=self.reset)
        padding = 10
        self.plotbutton.pack(side='top', fill='both', ipadx=padding, ipady=padding, padx=padding, pady=padding)
        self.sharebutton.pack(side='top', fill='both', ipadx=padding, ipady=padding, padx=padding, pady=padding)
        middle_left_frame.pack(side='left', expand=True)
        
        middle_right_frame = tk.Frame(middle_frame)
        self.undobutton = tk.Button(master=middle_right_frame, text='undo', width=max_width, command=self.undo)
        self.registerbutton = tk.Button(master=middle_right_frame, text='register', width=max_width, command=self.register_images)
        self.undobutton.pack(side='top', fill='both', ipadx=padding, ipady=padding, padx=padding, pady=padding)
        self.registerbutton.pack(side='top', fill='both', ipadx=padding, ipady=padding, padx=padding, pady=padding)
        middle_right_frame.pack(side='left', expand=True)
        middle_frame.pack(side='left', expand=True)
        
        right_frame = tk.Frame(self.root)
        console_label = tk.Label(right_frame, text='CONSOLE')
        self.console = scroll.ScrolledText(right_frame, height=len(commands), undo=True)
        self.console.insert(tk.END, 'Welcome to the Registration Tool!\n')
        self.console.config(state=tk.DISABLED)
        console_label.pack(side='top')
        self.console.pack(side='top')
        right_frame.pack(side='left', expand=True)
        
        self.corresponding_points = [[], []]
        self.points_added = []
        self.prev_x11 = 0
        self.prev_x12 = self.image1.shape[1]
        self.prev_y11 = 0
        self.prev_y12 = self.image1.shape[0]
        self.prev_x21 = 0
        self.prev_x22 = self.image2.shape[1]
        self.prev_y21 = 0
        self.prev_y22 = self.image2.shape[0]
        self.plot()
        
        self.canvas.mpl_connect('key_press_event', self.on_press)
        self.canvas.mpl_connect('key_release_event', self.on_release)
        self.canvas.mpl_connect('button_press_event', self.on_button)
        self.canvas.mpl_connect('button_release_event', self.on_button)
        

        
    def update_console(self, text):
        self.console.config(state=tk.NORMAL)
        self.console.see("end")
        self.console.insert(tk.END, text + '\n')
        self.console.config(state=tk.DISABLED)
        
    def plot(self):

        x11, x12 = self.axs[0].get_xlim()
        y11, y12 = self.axs[0].get_ylim()
        x21, x22 = self.axs[1].get_xlim()
        y21, y22 = self.axs[1].get_ylim()
            
        self.axs[0].clear()
        self.axs[0].imshow(self.image1, cmap='gray', aspect='equal', origin='lower')
        self.axs[0].set_title('Fixed image')
        self.axs[1].clear()
        self.axs[1].imshow(self.image2, cmap='gray', aspect='equal', origin='lower')
        self.axs[1].set_title('Moving image')
        self.canvas.draw()
        
        if x11 != self.prev_x11 and x12 != self.prev_x12 and y11 != self.prev_y11 and y12 != self.prev_y12:
            self.prev_x11 = x11
            self.prev_x12 = x12
            self.prev_y11 = y11
            self.prev_y12 = y12
            if self.shared:
                self.prev_x21 = int(np.round(x11 / self.image1.shape[1] * self.image2.shape[1]))
                self.prev_x22 = int(np.round(x12 / self.image1.shape[1] * self.image2.shape[1]))
                self.prev_y21 = int(np.round(y11 / self.image1.shape[0] * self.image2.shape[0]))
                self.prev_y22 = int(np.round(y12 / self.image1.shape[0] * self.image2.shape[0]))
        elif x21 != self.prev_x21 and x22 != self.prev_x22 and y21 != self.prev_y21 and y22 != self.prev_y22:
            self.prev_x21 = x21
            self.prev_x22 = x22
            self.prev_y21 = y21
            self.prev_y22 = y22
            if self.shared:
                self.prev_x11 = int(np.round(x21 / self.image2.shape[1] * self.image1.shape[1]))
                self.prev_x12 = int(np.round(x22 / self.image2.shape[1] * self.image1.shape[1]))
                self.prev_y11 = int(np.round(y21 / self.image2.shape[0] * self.image1.shape[0]))
                self.prev_y12 = int(np.round(y22 / self.image2.shape[0] * self.image1.shape[0]))
        
        self.axs[0].set_xlim(self.prev_x11, self.prev_x12)
        self.axs[0].set_ylim(self.prev_y11, self.prev_y12)
        self.axs[1].set_xlim(self.prev_x21, self.prev_x22)
        self.axs[1].set_ylim(self.prev_y21, self.prev_y22)
            
        if len(self.corresponding_points[0]) != 0 or len(self.corresponding_points[1]) != 0:
            for i, (x, y) in enumerate(self.corresponding_points[0]):
                self.axs[0].scatter(x, y, s=self.size_markers, c=f'C{i}')
            for i, (x, y) in enumerate(self.corresponding_points[1]):
                self.axs[1].scatter(x, y, s=self.size_markers, c=f'C{i}')
        
        self.prev_x11, self.prev_x12 = self.axs[0].get_xlim()
        self.prev_y11, self.prev_y12 = self.axs[0].get_ylim()
        self.prev_x21, self.prev_x22 = self.axs[1].get_xlim()
        self.prev_y21, self.prev_y22 = self.axs[1].get_ylim()

        self.canvas.draw()

    def reset(self):
        self.update_console('All control points are removed.')
        self.corresponding_points = [[], []]
        self.points_added = []
        self.plot()
        

        
    def undo(self):
        if len(self.points_added) > 0:
            index = self.points_added.pop()
            point = self.corresponding_points[index].pop()
            self.update_console(f'The control point (x = {point[0]}, y = {point[1]}) is removed from Image {index + 1}.')
            self.plot()
        else:
            self.update_console('There are no more control points to remove.')
        
    def register_images(self):
        self.callback(self.corresponding_points)
        self.root.quit()
        self.root.destroy()
        plt.close('all')
    
    def _ticker(self, ax: Axes):
        xticker = matplotlib.axis.Ticker()
        yticker = matplotlib.axis.Ticker()
        ax.xaxis.major = xticker
        ax.yaxis.major = yticker
        # The new ticker needs new locator and formatters
        xloc = matplotlib.ticker.AutoLocator()
        yloc = matplotlib.ticker.AutoLocator()
        xfmt = matplotlib.ticker.ScalarFormatter()
        yfmt = matplotlib.ticker.ScalarFormatter()

        ax.xaxis.set_major_locator(xloc)
        ax.yaxis.set_major_locator(yloc)
        ax.xaxis.set_major_formatter(xfmt)
        ax.yaxis.set_major_formatter(yfmt)
        
    def on_press(self, event):
        if event.key == '1':
            i = 0
            i_other = 1
        elif event.key == '2':
            i = 1
            i_other = 0
        elif event.key == 'r':
            self.removing = True
            return
        else:
            return
        x, y = int(np.round(event.xdata)), int(np.round(event.ydata))
        if self.removing:
            index = find_closest_point(self.corresponding_points[i], (x, y))
            if 0 <= index < len(self.corresponding_points[0]):
                point = self.corresponding_points[0].pop(index)
                self.update_console(f'The control point (x = {point[0]}, y = {point[1]}) is removed from Image {event.key}.')
            if 0 <= index < len(self.corresponding_points[1]):
                point = self.corresponding_points[1].pop(index)
                self.update_console(f'The control point (x = {point[0]}, y = {point[1]}) is removed from Image {event.key}.')
        else:
            if x and y and len(self.corresponding_points[i]) <= len(self.corresponding_points[i_other]):
                point = (int(np.round(x)), int(np.round(y)))
                self.corresponding_points[i].append(point)
                self.points_added.append(i)
                self.update_console(f'The control point (x = {point[0]}, y = {point[1]}) is added to Image {event.key}.')
        self.plot()
        
    def on_release(self, event):
        if event.key == 'r':
            self.removing = False
    
    def on_button(self, event):
        self.plot()

