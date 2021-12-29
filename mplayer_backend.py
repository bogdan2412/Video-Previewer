import argparse
import logging
import os
import shutil
import subprocess

from base_backend import BaseBackend
from util import add_app_path_arg


class MPlayerBackend(BaseBackend):
    # Reverse scale applied in get_video_info
    def capture_time_to_seconds(self, time):
        return time / self.duration_scale

    # Add path args for mplayer and midentify
    @staticmethod
    def get_argument_parser_group(parser):
        group = parser.add_argument_group(
                "MPlayer backend options",
                "Only necessary if you choose this backend.")
        app_list = ("mplayer", "midentify")
        for app in app_list:
            add_app_path_arg(group, app=app)
        return group

    # Determine video's information using the 'midentify' application
    def load_file(self, file_name):
        self.file_name = file_name
        logging.debug(
                "Using '%s' to get video's information."
                % self.args.path_midentify)

        process = subprocess.Popen(
                [str(self.args.path_midentify), str(file_name)],
                shell=False,
                stdout=subprocess.PIPE)
        output = process.stdout.read()
        process.wait()

        info = {}
        # Convert information outputted by midentify into a cross-backend form.
        info_conv = {
            "ID_LENGTH": ("duration", float),
            "ID_VIDEO_WIDTH": ("width", int),
            "ID_VIDEO_HEIGHT": ("height", int),
            "ID_VIDEO_FPS": ("video_framerate", float),
            "ID_VIDEO_BITRATE": ("video_bitrate", float),
            "ID_VIDEO_FORMAT": ("video_codec", str),
            "ID_AUDIO_NCH": ("audio_channels", int),
            "ID_AUDIO_RATE": ("audio_rate", float),
            "ID_AUDIO_BITRATE": ("audio_bitrate", float),
            "ID_AUDIO_CODEC": ("audio_codec", str),
        }
        for line in output.decode("utf-8").splitlines():
            (key, value) = line.split("=")
            value = value.replace("\\", "").replace("\n", "")
            if key in info_conv:
                info[info_conv[key][0]] = info_conv[key][1](value)

        self.duration_scale = 1
        # midentify doesn't work properly on some wmv files and it returns
        # ID_VIDEO_FPS == 1000. In this case we have to scale the timings of
        # the frames so that mplayer correctly captures all the needed frames.
        if info["video_framerate"] == 1000:
            logging.info("Not properly supported WMV format detected. "
                         "Adjusting times...")

            # Binary search the amount with which to scale the duration so that
            # capturing doesn't produce any errors
            left = 0.0
            right = 2.0
            EPS = 0.001
            while right - left >= EPS:
                middle = (right + left) * 0.5
                if self.capture_frame(info["duration"] * middle):
                    left = middle + EPS
                else:
                    right = middle - EPS

            self.duration_scale = left - EPS
            info["duration"] *= self.duration_scale
            logging.debug("The determined scale is %f." % self.duration_scale)
            logging.info("Finished adjusting times. Starting frame capture.")

            # Delete unreliable information
            del info["video_framerate"]
            del info["video_bitrate"]

        self.info = info
        return self.info

    # Capture frames using the 'mplayer' application.
    def capture_frame(self, capture_time, destination=None):
        # TODO: figure out how to run this with shell=False so we have proper
        # escaping of file names
        process = subprocess.Popen(
            "%s -really-quiet -nosound -vo png:z=3:outdir='%s' -frames 1 "
            "-ss %f '%s'" % (
                self.args.path_mplayer,
                self.tmp_dir,
                capture_time,
                self.file_name),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        output = process.stdout.read()
        error = process.stderr.read()
        process.wait()

        if not os.path.isfile("%s/00000001.png" % self.tmp_dir):
            if destination is not None:
                logging.error(
                        "Something went wrong when trying to capture "
                        "frame at %d seconds" % capture_time)
            return False
        else:
            if destination is not None:
                shutil.move("%s/00000001.png" % self.tmp_dir, destination)
            else:
                os.remove("%s/00000001.png" % self.tmp_dir)
            # The timestamp will not actually be equal to capture_time always,
            # but it's hard to determine it exactly.
            return capture_time


__all__ = ["MPlayerBackend"]
