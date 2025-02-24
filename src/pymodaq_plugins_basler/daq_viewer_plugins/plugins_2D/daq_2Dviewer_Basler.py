
from pymodaq.utils.parameter import Parameter
from pymodaq.utils.data import DataFromPlugins, DataToExport
from pymodaq.utils.daq_utils import ThreadCommand
from pymodaq.control_modules.viewer_utility_classes import main

from pymodaq_plugins_basler.hardware.daq_2Dviewer_GenericPylablibCamera import DAQ_2DViewer_GenericPylablibCamera
from pymodaq_plugins_basler.hardware.basler import DartCamera


class DAQ_2DViewer_Basler(DAQ_2DViewer_GenericPylablibCamera):
    """Viewer for Basler cameras
    """
    controller: DartCamera
    live_mode_available = True

    # For Basler, this returns a list of friendly names
    camera_list = [cam.GetFriendlyName() for cam in DartCamera.list_cameras()]

    # Update the params
    params = DAQ_2DViewer_GenericPylablibCamera.params + [
        {'title': 'Automatic exposure:', 'name': 'auto_exposure', 'type': 'bool', 'value': False},
        {'title': 'Gain (dB)', 'name': 'gain', 'type': 'float', 'value': 0},#, 'limits': [0, 18]},
        {'title': 'Pixel size (um)', 'name': 'pixel_length', 'type': 'float', 'value': 1, 'default' : 1, 'visible': False},
    ]
    params[next((i for i, item in enumerate(params) if item["name"] == "camera_list"), None)]['limits'] = camera_list  # type: ignore

    def init_controller(self) -> DartCamera:
        # Define the camera controller.
        # Use any argument necessary (serial_number, camera index, etc.) depending on the camera

        # Init camera with currently selected friendly name
        friendly_name = self.settings["camera_list"]
        self.emit_status(ThreadCommand('Update_Status', [f"Trying to connect to {friendly_name}", 'log']))
        camera_list = DartCamera.list_cameras()
        for cam in camera_list:
            if cam.GetFriendlyName() == friendly_name:
                name = cam.GetFullName()
                return DartCamera(name=name, callback=self.callback)
        self.emit_status(ThreadCommand('Update_Status', ["Camera not found", 'log']))
        raise ValueError(f"Camera with name {friendly_name} not found anymore.")

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

        # Check if pixel length is known
        if self.controller.pixel_length is None:
            model = self.controller.camera.GetDeviceInfo().GetModelName()
            self.emit_status(ThreadCommand('Update_Status', [(f"No pixel length known for camera model '{model}', defaulting to user-chosen one"), 'log']))
            self.settings.child('pixel_length').show()

        # Check gain mode
        if self.controller.raw_gain:
            self.settings.child('gain').setOpts(type="int")
            self.settings.child('gain').setOpts(title="Gain (raw)")
        self.settings.child('gain').setValue(self.controller.gain)

        # Get camera name
        self.settings.child('camera_info').setValue(self.controller.get_device_info()[1])

        # Set exposure time
        self.controller.exposure = self.settings.child('timing_opts', 'exposure_time').value() / 1000

        # FPS visibility
        self.settings.child('timing_opts', 'fps').setOpts(visible=self.settings.child('timing_opts', 'fps_on').value())

        # Update image parameters
        (x0, xend, y0, yend, xbin, ybin) = self.controller.get_roi()
        height = xend - x0
        width = yend - y0
        self.settings.child('binning').setValue(xbin)
        self.settings.child('hdet').setValue(width)
        self.settings.child('vdet').setValue(height)

        # Here in the original is the callback

        self._prepare_view()

        info = "Initialized camera"
        initialized = True
        return info, initialized

    def commit_settings(self, param: Parameter) -> None:
        """Apply the consequences of a change of value in the detector settings

        Parameters
        ----------
        param: Parameter
            A given parameter (within detector_settings) whose value has been changed by the user
        """
        if param.name() == "auto_exposure":
            self.controller.camera.ExposureAuto.SetValue(
                "Continuous" if self.settings['auto_exposure'] else "Off")
        elif param.name() == "exposure_time":
            self.controller.exposure = param.value()/1000
        elif param.name() == "gain":
            self.controller.gain = param.value()
        elif param.name() == "pixel_length":
            self.controller.pixel_length = param.value()
        else:
            super().commit_settings(param=param)

    def grab_data(self, Naverage: int = 1, live: bool = False, **kwargs) -> None:
        if live:
            self._prepare_view()
            self.controller.start_grabbing()
        else:
            self._prepare_view()
            self.emit_data()

    def stop(self):
        self.controller.stop_grabbing()

    def callback(self, array) -> None:
        self.dte_signal.emit(DataToExport('Camera', data=[DataFromPlugins(
            name='Camera Image',
            data=[array],
            dim=self.data_shape,
            labels=[f'Camera_{self.data_shape}'],
            axes=self.axes)]))
        if self.settings.child('timing_opts', 'fps_on').value():
            self.update_fps()


if __name__ == '__main__':
    main(__file__, init=False)
