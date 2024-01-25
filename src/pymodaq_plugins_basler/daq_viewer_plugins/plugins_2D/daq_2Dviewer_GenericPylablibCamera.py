"""
Copied (and slightly modified) from https://github.com/rgeneaux/pymodaq_plugins_test_pylablib
"""


from pymodaq.utils.daq_utils import ThreadCommand
from pymodaq.utils.data import DataFromPlugins, Axis, DataToExport
from pymodaq.control_modules.viewer_utility_classes import DAQ_Viewer_base, comon_parameters, main
from pymodaq.utils.parameter import Parameter
from qtpy import QtWidgets, QtCore
from time import perf_counter
import numpy as np


class DAQ_2DViewer_GenericPylablibCamera(DAQ_Viewer_base):
    """
    IMPORTANT: THIS IS A GENERIC CLASS THAT DOES NOT WORK ON ITS OWN!

    It is meant to be used for cameras supported by the pylablib library, see here:
    https://pylablib.readthedocs.io/en/latest/devices/cameras_root.html

    The class needs to be subclassed, the subclass only has to define the camera_list and init_controller methods
    and the plugin will work.
    """

    params = comon_parameters + [
        {'title': 'Camera:', 'name': 'camera_list', 'type': 'list', 'limits': []},
        {'title': 'Camera model:', 'name': 'camera_info', 'type': 'str', 'value': '', 'readonly': True},
        {'title': 'Update ROI', 'name': 'update_roi', 'type': 'bool_push', 'value': False},
        {'title': 'Clear ROI+Bin', 'name': 'clear_roi', 'type': 'bool_push', 'value': False},
        {'title': 'Binning', 'name': 'binning', 'type': 'list', 'limits': [1, 2]},
        {'title': 'Image width', 'name': 'hdet', 'type': 'int', 'value': 1, 'readonly': True},
        {'title': 'Image height', 'name': 'vdet', 'type': 'int', 'value': 1, 'readonly': True},
        {'title': 'Timing', 'name': 'timing_opts', 'type': 'group', 'children':
            [{'title': 'Exposure Time (ms)', 'name': 'exposure_time', 'type': 'int', 'value': 1},
             {'title': 'Compute FPS', 'name': 'fps_on', 'type': 'bool', 'value': True},
             {'title': 'FPS', 'name': 'fps', 'type': 'float', 'value': 0.0, 'readonly': True}]
         }
    ]
    callback_signal = QtCore.Signal()
    roi_pos_size = QtCore.QRectF(0, 0, 10, 10)
    axes = []

    def init_controller(self):
        raise NotImplementedError('This is a generic camera plugin for which .init_controller() has not been defined.')

    def ini_attributes(self):
        self.controller: None

        self.x_axis = None
        self.y_axis = None
        self.last_tick = 0.0  # time counter used to compute FPS
        self.fps = 0.0

        self.data_shape = 'Data2D'
        self.callback_thread = None

    def commit_settings(self, param: Parameter):
        """Apply the consequences of a change of value in the detector settings

        Parameters
        ----------
        param: Parameter
            A given parameter (within detector_settings) whose value has been changed by the user
        """
        if param.name() == "exposure_time":
            self.controller.set_exposure(param.value() / 1000)

        if param.name() == "fps_on":
            self.settings.child('timing_opts', 'fps').setOpts(visible=param.value())

        if param.name() == "update_roi":
            if param.value():  # Switching on ROI

                # We handle ROI and binning separately for clarity
                (old_x, _, old_y, _, xbin, ybin) = self.controller.get_roi()  # Get current binning

                x0 = self.roi_pos_size.x()
                y0 = self.roi_pos_size.y()
                width = self.roi_pos_size.width()
                height = self.roi_pos_size.height()

                # Values need to be rescaled by binning factor and shifted by current x0,y0 to be correct.
                new_x = (old_x + x0) * xbin
                new_y = (old_y + y0) * xbin
                new_width = width * ybin
                new_height = height * ybin

                new_roi = (new_x, new_width, xbin, new_y, new_height, ybin)
                self.update_rois(new_roi)
                param.setValue(False)

        if param.name() == 'binning':
            # We handle ROI and binning separately for clarity
            (x0, w, y0, h, *_) = self.controller.get_roi()  # Get current ROI
            xbin = self.settings.child('binning').value()
            ybin = self.settings.child('binning').value()
            new_roi = (x0, w, xbin, y0, h, ybin)
            self.update_rois(new_roi)

        if param.name() == "clear_roi":
            if param.value():  # Switching on ROI
                wdet, hdet = self.controller.get_detector_size()
                # self.settings.child('ROIselect', 'x0').setValue(0)
                # self.settings.child('ROIselect', 'width').setValue(wdet)
                self.settings.child('binning').setValue(1)
                #
                # self.settings.child('ROIselect', 'y0').setValue(0)
                # new_height = self.settings.child('ROIselect', 'height').setValue(hdet)

                new_roi = (0, wdet, 1, 0, hdet, 1)
                self.update_rois(new_roi)
                param.setValue(False)

    def ROISelect(self, roi_pos_size):
        self.roi_pos_size = roi_pos_size

    def ini_detector(self, controller=None):
        """Detector communication initialization

        Parameters
        ----------
        controller: (object)
            custom object of a PyMoDAQ plugin (Slave case). None if only one actuator/detector by controller
            (Master case)

        Returns
        -------
        info: str
        initialized: bool
            False if initialization failed otherwise True
        """
        # Initialize camera class
        self.ini_detector_init(old_controller=controller,
                               new_controller=self.init_controller())

        # Get camera name
        self.settings.child('camera_info').setValue(self.controller.get_device_info()[1])

        # Set exposure time
        self.controller.set_exposure(self.settings.child('timing_opts', 'exposure_time').value() / 1000)

        # FPS visibility
        self.settings.child('timing_opts', 'fps').setOpts(visible=self.settings.child('timing_opts', 'fps_on').value())

        # Update image parameters
        (hstart, hend, vstart, vend, hbin, vbin) = self.controller.get_roi()
        height = hend - hstart
        width = vend - vstart
        self.settings.child('binning').setValue(hbin)
        self.settings.child('hdet').setValue(width)
        self.settings.child('vdet').setValue(height)

        # Way to define a wait function with arguments
        wait_func = lambda: self.controller.wait_for_frame(since='lastread', nframes=1, timeout=20.0)
        callback = PylablibCallback(wait_func)

        self.callback_thread = QtCore.QThread()  # creation of a Qt5 thread
        callback.moveToThread(self.callback_thread)  # callback object will live within this thread
        callback.data_sig.connect(
            self.emit_data)  # when the wait for acquisition returns (with data taken), emit_data will be fired

        self.callback_signal.connect(callback.wait_for_acquisition)
        self.callback_thread.callback = callback
        self.callback_thread.start()

        self._prepare_view()

        info = "Initialized camera"
        initialized = True
        return info, initialized

    def _prepare_view(self):
        """Preparing a data viewer by emitting temporary data. Typically, needs to be called whenever the
        ROIs are changed"""
        # wx = self.settings.child('rois', 'width').value()
        # wy = self.settings.child('rois', 'height').value()
        # bx = self.settings.child('rois', 'x_binning').value()
        # by = self.settings.child('rois', 'y_binning').value()
        #
        # sizex = wx // bx
        # sizey = wy // by
        (hstart, hend, vstart, vend, *_) = self.controller.get_roi()
        height = hend - hstart
        width = vend - vstart

        self.settings.child('hdet').setValue(width)
        self.settings.child('vdet').setValue(height)
        mock_data = np.zeros((width, height))

        self.x_axis = Axis(data=np.linspace(0, width, width, endpoint=False), label='Pixels', index=0)

        if width != 1 and height != 1:
            data_shape = 'Data2D'
            self.y_axis = Axis(data=np.linspace(0, height, height, endpoint=False), label='Pixels', index=1)
            self.axes = [self.x_axis, self.y_axis]

        else:
            data_shape = 'Data1D'
            self.x_axis.index = 0
            self.axes = [self.x_axis]

        if data_shape != self.data_shape:
            self.data_shape = data_shape
            # init the viewers
            self.dte_signal_temp.emit(
                DataToExport('Camera',
                             data=[DataFromPlugins(name='Camera Image',
                                                   data=[np.squeeze(mock_data)],
                                                   dim=self.data_shape,
                                                   labels=[f'Camera_{self.data_shape}'],
                                                   axes=self.axes)]))
            QtWidgets.QApplication.processEvents()

    def update_rois(self, new_roi):
        # In pylablib, ROIs compare as tuples
        (new_x, new_width, new_xbinning, new_y, new_height, new_ybinning) = new_roi
        if new_roi != self.controller.get_roi():
            # self.controller.set_attribute_value("ROIs",[new_roi])
            self.controller.set_roi(hstart=new_x,
                                    hend=new_x + new_width,
                                    vstart=new_y,
                                    vend=new_y + new_height,
                                    hbin=new_xbinning,
                                    vbin=new_ybinning)
            self.emit_status(ThreadCommand('Update_Status', [f'Changed ROI: {new_roi}']))
            self.controller.clear_acquisition()
            self.controller.setup_acquisition()
            # Finally, prepare view for displaying the new data
            self._prepare_view()

    def grab_data(self, Naverage=1, **kwargs):
        """
        Grabs the data. Synchronous method (kinda).
        ----------
        Naverage: (int) Number of averaging
        kwargs: (dict) of others optionals arguments
        """
        try:
            self._prepare_view()
            # Warning, acquisition_in_progress returns 1,0 and not a real bool
            if not self.controller.acquisition_in_progress():
                self.controller.clear_acquisition()
                self.controller.start_acquisition()
            # Then start the acquisition
            self.callback_signal.emit()  # will trigger the wait for acquisition

        except Exception as e:
            self.emit_status(ThreadCommand('Update_Status', [str(e), "log"]))

    def emit_data(self):
        """
            Function used to emit data obtained by callback.
            See Also
            --------
            daq_utils.ThreadCommand
        """
        try:
            # Get  data from buffer
            frame = self.controller.read_newest_image()
            # Emit the frame.
            if frame is not None:  # happens for last frame when stopping camera
                self.dte_signal.emit(
                    DataToExport('Camera',
                                 data=[DataFromPlugins(name='Camera Image',
                                                       data=[np.squeeze(frame)],
                                                       dim=self.data_shape,
                                                       labels=[f'Camera_{self.data_shape}'],
                                                       axes=self.axes)]))
            if self.settings.child('timing_opts', 'fps_on').value():
                self.update_fps()

            # To make sure that timed events are executed in continuous grab mode
            QtWidgets.QApplication.processEvents()

        except Exception as e:
            self.emit_status(ThreadCommand('Update_Status', [str(e), 'log']))

    def update_fps(self):
        current_tick = perf_counter()
        frame_time = current_tick - self.last_tick

        if self.last_tick != 0.0 and frame_time != 0.0:
            # We don't update FPS for the first frame, and we also avoid divisions by zero

            if self.fps == 0.0:
                self.fps = 1 / frame_time
            else:
                # If we already have an FPS calculated, we smooth its evolution
                self.fps = 0.9 * self.fps + 0.1 / frame_time

        self.last_tick = current_tick

        # Update reading
        self.settings.child('timing_opts', 'fps').setValue(round(self.fps, 1))

    def callback(self):
        """optional asynchrone method called when the detector has finished its acquisition of data"""
        raise NotImplementedError

    def close(self):
        """
        Terminate the communication protocol
        """
        # Terminate the communication
        self.controller.close()
        self.controller = None  # Garbage collect the controller
        self.status.initialized = False
        self.status.controller = None
        self.status.info = ""

    def stop(self):
        """Stop the acquisition."""
        self.controller.stop_acquisition()
        self.controller.clear_acquisition()
        return ''


class PylablibCallback(QtCore.QObject):
    """Callback object """
    data_sig = QtCore.Signal()

    def __init__(self, wait_fn):
        super().__init__()
        # Set the wait function
        self.wait_fn = wait_fn

    def wait_for_acquisition(self):
        new_data = self.wait_fn()
        if new_data is not False:  # will be returned if the main thread called CancelWait
            self.data_sig.emit()


if __name__ == '__main__':
    main(__file__)
