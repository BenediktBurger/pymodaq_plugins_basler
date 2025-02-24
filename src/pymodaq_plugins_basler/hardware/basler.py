import logging
from typing import Any, Callable, List, Optional, Tuple, Union

from numpy.typing import NDArray
from pypylon import pylon
from qtpy import QtCore

if not hasattr(QtCore, "pyqtSignal"):
    QtCore.pyqtSignal = QtCore.Signal  # type: ignore

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


pixel_lengths: dict[str, float] = {
    # camera model name: pixel length in µm
    "daA1280-54um": 3.75,
    "daA2500-14um": 2.2,
    "daA3840-45um": 2,
    "acA640-120gm": 5.6,
    "acA645-100gm": 5.6,
}


class DartCamera:
    """Control a Basler Dart camera in the style of pylablib.

    It wraps an :class:`pylon.InstantCamera` instance.

    :param name: Full name of the device.
    :param callback: Callback method for each grabbed image
    """

    tlFactory: pylon.TlFactory
    camera: pylon.InstantCamera

    def __init__(self, name: str, callback: Optional[Callable] = None, **kwargs):
        super().__init__(**kwargs)
        # create camera object
        self.tlFactory = pylon.TlFactory.GetInstance()
        self.camera = pylon.InstantCamera()
        self._exposure = None
        self._gain = None

        # register configuration event handler
        self.configurationEventHandler = ConfigurationHandler()
        self.camera.RegisterConfiguration(
            self.configurationEventHandler,
            pylon.RegistrationMode_ReplaceAll,
            pylon.Cleanup_None,
        )
        # configure camera events
        self.imageEventHandler = ImageEventHandler()
        self.camera.RegisterImageEventHandler(
            self.imageEventHandler, pylon.RegistrationMode_Append, pylon.Cleanup_None
        )

        self._pixel_length: Optional[float] = None
        self.attributes = {}
        self.open(name=name)
        if callback is not None:
            self.set_callback(callback=callback)

    def open(self, name: str) -> None:
        device = self.tlFactory.CreateDevice(name)
        self.camera.Attach(device)
        self.camera.Open()
        self.attributes["PixelWidth"] = self.pixel_length
        self.check_attribute_names()

    def check_attribute_names(self):
        possible_exposures = ["ExposureTime", "ExposureTimeAbs"]
        for exp in possible_exposures:
            try:
                if hasattr(self.camera, exp):
                    self._exposure = getattr(self.camera, exp)
                    break
            except pylon.LogicalErrorException:
                pass

        possible_gains = ["Gain", "GainRaw"]
        for gain in possible_gains:
            try:
                if hasattr(self.camera, gain):
                    self._gain = getattr(self.camera, gain)
                    break
            except pylon.LogicalErrorException:
                pass

    def set_callback(
        self, callback: Callable[[NDArray], None], replace_all: bool = True
    ) -> None:
        """Setup a callback method for continuous acquisition.

        :param callback: Method to be used in continuous mode. It should accept an array as input.
        :param bool replace_all: Whether to remove all previously set callback methods.
        """
        if replace_all:
            try:
                self.imageEventHandler.signals.imageGrabbed.disconnect()
            except TypeError:
                pass  # not connected
        self.imageEventHandler.signals.imageGrabbed.connect(callback)

    # Methods in the style of pylablib
    @staticmethod
    def list_cameras() -> List[pylon.InstantCamera]:
        """List all available cameras as camera info objects."""
        tlFactory = pylon.TlFactory.GetInstance()
        return tlFactory.EnumerateDevices()

    def get_device_info(self) -> List[Any]:
        """Get camera information.

        Return tuple ``(name, model, serial, devclass, devversion, vendor, friendly_name, user_name,
        props)``.
        """
        devInfo: pylon.DeviceInfo = self.camera.GetDeviceInfo()
        return [
            devInfo.GetFullName(),
            devInfo.GetModelName(),
            devInfo.GetSerialNumber(),
            devInfo.GetDeviceClass(),
            devInfo.GetDeviceVersion(),
            devInfo.GetVendorName(),
            devInfo.GetFriendlyName(),
            devInfo.GetUserDefinedName(),
            None,
        ]

    @property
    def exposure(self) -> float:
        """Get the exposure time in s."""
        return self._exposure.GetValue() / 1e6

    @exposure.setter
    def exposure(self, value: float) -> None:
        """Set the exposure time in s."""
        self._exposure.SetValue(value * 1e6)

    @property
    def gain(self) -> float:
        """Get the gain"""
        return self._gain.GetValue()

    @gain.setter
    def gain(self, value: float) -> None:
        """Set the gain"""
        self._gain.SetValue(value)

    def get_roi(self) -> Tuple[float, float, float, float, int, int]:
        """Return x0, width, y0, height, xbin, ybin."""
        x0 = self.camera.OffsetX.GetValue()
        width = self.camera.Width.GetValue()
        y0 = self.camera.OffsetY.GetValue()
        height = self.camera.Height.GetValue()
        xbin = self.camera.BinningHorizontal.GetValue()
        ybin = self.camera.BinningVertical.GetValue()
        return x0, x0 + width, y0, y0 + height, xbin, ybin

    def set_roi(
        self, hstart: int, hend: int, vstart: int, vend: int, hbin: int, vbin: int
    ) -> None:
        camera = self.camera
        m_width, m_height = self.get_detector_size()
        inc = camera.Width.Inc  # minimum step size
        hstart = detector_clamp(hstart, m_width) // inc * inc
        vstart = detector_clamp(vstart, m_height) // inc * inc
        # Set the offset to 0 first, to allow full range of width values.
        camera.OffsetX.SetValue(0)
        camera.Width.SetValue((detector_clamp(hend, m_width) - hstart) // inc * inc)
        camera.OffsetX.SetValue(hstart)
        camera.OffsetY.SetValue(0)
        camera.Height.SetValue((detector_clamp(vend, m_height) - vstart) // inc * inc)
        camera.OffsetY.SetValue(vstart)
        camera.BinningHorizontal.SetValue(int(hbin))
        camera.BinningVertical.SetValue(int(vbin))

    def get_detector_size(self) -> Tuple[int, int]:
        """Return width and height of detector in pixels."""
        return self.camera.SensorWidth.GetValue(), self.camera.SensorHeight.GetValue()

    def wait_for_frame(
        self, since="lastread", nframes=1, timeout=20.0, error_on_stopped=False
    ):
        """
        Wait for one or several new camera frames.

        `since` specifies the reference point for waiting to acquire `nframes` frames;
        can be "lastread"`` (from the last read frame), ``"lastwait"`` (wait for the last successful
          :meth:`wait_for_frame` call),
        ``"now"`` (from the start of the current call), or ``"start"`` (from the acquisition start,
        i.e., wait until `nframes` frames have been acquired).
        `timeout` can be either a number, ``None`` (infinite timeout), or a tuple ``(timeout,
        frame_timeout)``,
        in which case the call times out if the total time exceeds ``timeout``, or a single frame
        wait exceeds ``frame_timeout``.
        If the call times out, raise ``TimeoutError``.
        If ``error_on_stopped==True`` and the acquisition is not running, raise ``Error``;
        otherwise, simply return ``False`` without waiting.
        """
        raise NotImplementedError("Not implemented")

    def get_all_attributes(self):
        return self.attributes

    def get_attribute_value(self, name, error_on_missing=True):
        """Get the camera attribute with the given name"""
        return self.attributes[name]

    def clear_acquisition(self):
        """Stop acquisition"""
        pass  # TODO

    def setup_acquisition(self):
        """Start acquisition in continuous mode."""
        pass  # TODO

    def acquisition_in_progress(self):
        raise NotImplementedError("Not implemented")

    def read_newest_image(self):
        return self.get_one()

    def close(self) -> None:
        self.camera.Close()
        self.camera.DetachDevice()
        self._pixel_length = None
        self.attributes.pop("PixelWidth", None)  # drop it

    # additional methods, for use in the code
    def get_single_result(self, timeout_ms: int = 1000) -> pylon.GrabResult:
        """Get one image and return the pylon `GrabResult`."""
        args = []
        if timeout_ms is not None:
            args.append(timeout_ms)
        if args:
            result: pylon.GrabResult = self.camera.GrabOne(*args)
        else:
            result = self.camera.GrabOne()
        if result.GrabSucceeded():
            return result
        else:
            raise TimeoutError("Grabbing exceeded timeout")

    def get_one(self, timeout_ms: int = 1000):
        """Get one image and return the (numpy) array of it."""
        result = self.get_single_result(timeout_ms=1000)
        result.GetArray()

    def start_grabbing(self, max_frame_rate: int = 1000) -> None:
        """Start continuously to grab data.

        Whenever a grab succeeded, the callback defined in :meth:`set_callback` is called.
        """
        try:
            self.camera.AcquisitionFrameRate.SetValue(max_frame_rate)
        except pylon.LogicalErrorException:
            pass
        self.camera.StartGrabbing(
            pylon.GrabStrategy_LatestImageOnly, pylon.GrabLoop_ProvidedByInstantCamera
        )

    def stop_grabbing(self) -> None:
        self.camera.StopGrabbing()

    @property
    def pixel_length(self) -> float:
        """Get the pixel length of the camera in µm.

        Returns None if the pixel length of the specific model is not known
        """
        if self._pixel_length is None:
            model = self.camera.GetDeviceInfo().GetModelName()
            try:
                self._pixel_length = pixel_lengths[model]
            except KeyError:
                self._pixel_length = None
        return self._pixel_length

    @pixel_length.setter
    def pixel_length(self, value):
        self._pixel_length = value


class ConfigurationHandler(pylon.ConfigurationEventHandler):
    """Handle the configuration events."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.signals = self.ConfigurationHandlerSignals()

    class ConfigurationHandlerSignals(QtCore.QObject):
        """Signals for the CameraEventHandler."""

        cameraRemoved = QtCore.pyqtSignal(object)

    def OnOpened(self, camera: pylon.InstantCamera) -> None:
        """Standard configuration after being opened."""
        camera.PixelFormat.SetValue("Mono12")
        camera.GainAuto.SetValue("Off")
        camera.ExposureAuto.SetValue("Off")

    def OnCameraDeviceRemoved(self, camera: pylon.InstantCamera) -> None:
        """Emit a signal that the camera is removed."""
        self.signals.cameraRemoved.emit(camera)


class ImageEventHandler(pylon.ImageEventHandler):
    """Handle the events and translates them to signals/slots."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.signals = self.ImageEventHandlerSignals()

    class ImageEventHandlerSignals(QtCore.QObject):
        """Signals for the ImageEventHandler."""

        imageGrabbed = QtCore.pyqtSignal(object)

    def OnImageSkipped(self, camera: pylon.InstantCamera, countOfSkippedImages: int) -> None:
        """Handle a skipped image."""
        log.warning(f"{countOfSkippedImages} images have been skipped.")

    def OnImageGrabbed(self, camera: pylon.InstantCamera, grabResult: pylon.GrabResult) -> None:
        """Process a grabbed image."""
        if grabResult.GrabSucceeded():
            self.signals.imageGrabbed.emit(grabResult.GetArray())
        else:
            log.warning(
                (
                    f"Grab failed with code {grabResult.GetErrorCode()}, "
                    f"{grabResult.GetErrorDescription()}."
                )
            )


def detector_clamp(value: Union[float, int], max_value: int) -> int:
    """Clamp a value to possible detector position."""
    return max(0, min(int(value), max_value))
