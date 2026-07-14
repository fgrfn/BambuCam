"""Tests for camera detection (mocked — no real hardware needed)."""

from unittest.mock import MagicMock, patch

from bambucam.camera.detector import (
    _parse_libcamera_output,
    _parse_libcamera_resolutions,
    detect_cameras,
)
from bambucam.camera.models import CAMERA_USB_GENERIC, CAMERA_V2, Resolution

LIBCAMERA_SAMPLE_OUTPUT = """\
Available cameras
-----------------
0 : imx219 [3280x2464 10-bit RGGB] (/base/soc/i2c0mux/i2c@1/imx219@10)
    Modes: 'SRGGB10_CSI2P' : 3280x2464 [21.19 fps - (0, 0)/3280x2464 crop]
                             1920x1080 [47.57 fps - (0, 0)/3280x2464 crop]
                             1640x1232 [41.85 fps - (0, 0)/3280x2464 crop]
                             640x480   [206.65 fps - (0, 0)/3280x2464 crop]
"""

MULTI_CAMERA_OUTPUT = """\
Available cameras
-----------------
0 : imx219 [3280x2464 10-bit RGGB] (/camera0)
    Modes: 3280x2464 [21.19 fps]
           1920x1080 [47.57 fps]
1 : imx708 [4608x2592 10-bit RGGB] (/camera1)
    Modes: 4608x2592 [14.35 fps]
           2304x1296 [56.03 fps]
"""


class TestParseLibcameraOutput:
    def test_detects_imx219(self):
        cameras = _parse_libcamera_output(LIBCAMERA_SAMPLE_OUTPUT)
        assert len(cameras) == 1
        camera = cameras[0]
        assert camera.model is CAMERA_V2
        assert camera.backend == "picamera2"
        assert camera.index == 0

    def test_parse_resolutions(self):
        resolutions = _parse_libcamera_resolutions(LIBCAMERA_SAMPLE_OUTPUT, "imx219")
        assert Resolution(3280, 2464) in resolutions
        assert Resolution(1920, 1080) in resolutions
        assert Resolution(640, 480) in resolutions

    def test_multiple_camera_modes_do_not_mix(self):
        cameras = _parse_libcamera_output(MULTI_CAMERA_OUTPUT)

        assert len(cameras) == 2
        assert Resolution(4608, 2592) not in cameras[0].detected_resolutions
        assert Resolution(3280, 2464) not in cameras[1].detected_resolutions
        assert cameras[0].detected_resolutions == [
            Resolution(3280, 2464),
            Resolution(1920, 1080),
        ]
        assert cameras[1].detected_resolutions == [
            Resolution(4608, 2592),
            Resolution(2304, 1296),
        ]

    def test_unknown_sensor_uses_generic_model(self):
        cameras = _parse_libcamera_output("0 : futurecam [1920x1080] (/camera0)\n")
        assert cameras[0].model is CAMERA_USB_GENERIC

    def test_empty_output(self):
        assert _parse_libcamera_output("") == []


class TestDetectCameras:
    @patch("bambucam.camera.detector.subprocess.run")
    def test_detect_with_libcamera(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = LIBCAMERA_SAMPLE_OUTPUT
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        with patch("bambucam.camera.detector._detect_v4l2", return_value=[]):
            cameras = detect_cameras()

        assert len(cameras) == 1
        assert cameras[0].model is CAMERA_V2

    @patch("bambucam.camera.detector._detect_libcamera", return_value=[])
    @patch("bambucam.camera.detector._detect_v4l2", return_value=[])
    def test_no_cameras(self, mock_v4l2, mock_libcam):
        assert detect_cameras() == []
