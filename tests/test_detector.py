"""Tests for camera detection (mocked — no real hardware needed)."""

import subprocess
from unittest.mock import MagicMock, patch

from bambucam.camera.detector import (
    _parse_libcamera_output,
    _parse_libcamera_resolutions,
    detect_cameras,
)
from bambucam.camera.models import CAMERA_V2, Resolution


LIBCAMERA_SAMPLE_OUTPUT = """\
Available cameras
-----------------
0 : imx219 [3280x2464 10-bit RGGB] (/base/soc/i2c0mux/i2c@1/imx219@10)
    Modes: 'SRGGB10_CSI2P' : 3280x2464 [21.19 fps - (0, 0)/3280x2464 crop]
                             1920x1080 [47.57 fps - (0, 0)/3280x2464 crop]
                             1640x1232 [41.85 fps - (0, 0)/3280x2464 crop]
                             640x480   [206.65 fps - (0, 0)/3280x2464 crop]
"""


class TestParseLibcameraOutput:
    def test_detects_imx219(self):
        cameras = _parse_libcamera_output(LIBCAMERA_SAMPLE_OUTPUT)
        assert len(cameras) == 1
        cam = cameras[0]
        assert cam.model is CAMERA_V2
        assert cam.backend == "picamera2"
        assert cam.index == 0

    def test_parse_resolutions(self):
        resolutions = _parse_libcamera_resolutions(LIBCAMERA_SAMPLE_OUTPUT, "imx219")
        assert Resolution(3280, 2464) in resolutions
        assert Resolution(1920, 1080) in resolutions
        assert Resolution(640, 480) in resolutions

    def test_empty_output(self):
        cameras = _parse_libcamera_output("")
        assert cameras == []


class TestDetectCameras:
    @patch("bambucam.camera.detector.subprocess.run")
    def test_detect_with_libcamera(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = LIBCAMERA_SAMPLE_OUTPUT
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        with patch("bambucam.camera.detector._detect_v4l2", return_value=[]):
            cameras = detect_cameras()

        assert len(cameras) == 1
        assert cameras[0].model is CAMERA_V2

    @patch("bambucam.camera.detector._detect_libcamera", return_value=[])
    @patch("bambucam.camera.detector._detect_v4l2", return_value=[])
    def test_no_cameras(self, mock_v4l2, mock_libcam):
        cameras = detect_cameras()
        assert cameras == []
