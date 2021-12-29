#!/usr/bin/env python3

__version__ = "0.2.99.04"

__copyright__ = """
Copyright (c) 2009-2021 Bogdan Tataroiu
"""

__license__ = """
All source code available in this repository is covered by a GPLv2 license.
"""

import argparse
import copy
import logging
import os
import pathlib
import shutil
import subprocess
import tempfile

from mplayer_backend import MPlayerBackend
from gstreamer_backend import GStreamerBackend
from util import add_app_path_arg, safe_int_log


# Returns a humanized string for a given amount of seconds
def time_format(seconds):
    seconds = int(seconds)
    return "%d:%02d:%02d" % (
        seconds / 3600,
        (seconds % 3600) / 60,
        seconds % 60)


# Returns a humanized string for a given amount of bytes
def file_size_format(bytes, precision=2):
    bytes = int(bytes)
    if bytes == 0:
        return '0 B'
    log = safe_int_log(bytes, 1024)

    return "%.*f%s" % (
        precision,
        bytes / (1024.0 ** log),
        ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"][log])


backends = {
        "mplayer": MPlayerBackend,
        "gstreamer": GStreamerBackend
}


class CLIMain:
    def __init__(self):
        # Build command line arguments parser
        parser = argparse.ArgumentParser(
            usage="%(prog)s [options] FILE [FILE ...]",
            description=(
                "Cross-platform python tool which generates a video's "
                "index preview with multiple screen capture thumbnails."),
            add_help=False)
        parser.add_argument(
                "--version",
                action="version",
                version="%%(prog)s %s" % __version__)

        # Custom help flag which adds flags from all backends
        class CustomHelpAction(argparse.Action):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, nargs=0, **kwargs)

            def __call__(self, parser, namespace, values, option_string=None):
                for backend in backends.values():
                    argument_group = backend.get_argument_parser_group(parser)
                    if argument_group:
                        parser.add_argument_group(argument_group)

                parser.print_help()
                parser.exit()
        parser.add_argument(
                "-h", "--help",
                action=CustomHelpAction,
                help="show this help message and exit")

        logging_group = parser.add_mutually_exclusive_group()
        logging_group.set_defaults(logging_level=logging.INFO)
        logging_group.add_argument(
                "-v", "--verbose",
                help="Print more detalied information",
                action="store_const",
                const=logging.DEBUG,
                dest="logging_level")
        logging_group.add_argument(
                "-q", "--quiet",
                help="Refrain from outputing anything",
                action="store_const",
                const=logging.CRITICAL,
                dest="logging_level")

        # Add options to specify paths for each needed application
        self.app_list = ("convert", "montage")
        for app in self.app_list:
            add_app_path_arg(parser, app=app)

        # Add options related to the resulting thumbnail such as
        # number of rows or columns, width and height of the thumbnails,
        # the space between them etc
        capture_args = parser.add_argument_group("Capture options")
        capture_args.add_argument(
                "-r", "--rows",
                help=(
                    "Number of rows the generated grid "
                    "should contain (default %(default)s)."),
                type=int,
                dest="grid_rows",
                default=6)
        capture_args.add_argument(
                "-c", "--cols", "--columns",
                help=(
                    "Number of columns the generated grid "
                    "should contain (default %(default)s)."),
                type=int,
                dest="grid_cols",
                default=4)
        capture_args.add_argument(
                "-t", "--title",
                help="Title for the thumbnail (video's name is default).",
                dest="title",
                default=None)
        capture_args.add_argument(
                "-W", "--width",
                help="The width of a single image in the grid in pixels.",
                type=int,
                dest="thumbnail_width",
                default=None)
        capture_args.add_argument(
                "-H", "--height",
                help=(
                    "The height of a single image in the grid in pixels. "
                    "If only one of the width and height argument are "
                    "specified, the other one will be determined so that the "
                    "aspect ratio of the movie is preserved."),
                type=int,
                dest="thumbnail_height",
                default=None)
        capture_args.add_argument(
                "-S", "--spacing",
                help=(
                    "The space between images in the grid in pixels. "
                    "(default %(default)s)"),
                type=int,
                dest="grid_spacing",
                default=4)
        capture_args.add_argument(
                "--focus",
                help=(
                    "Focus on the beginning or the ending of the movie. That "
                    "means a greater number of thumbnails will be generated "
                    "in the specified area than in the other part. For "
                    "example if the focus is on the beginning of the movie, "
                    "the frequency of captures drops as time goes by. "
                    "Possible values are 'begin', 'end' and 'none'. (default "
                    "is '%(default)s')"),
                choices=("begin", "end", "none"),
                dest="capture_focus",
                default="none")

        # Add style related options
        style_args = parser.add_argument_group("Style options")
        style_args.add_argument(
                "--background",
                help="Background color (e.g. '#00ff00')",
                dest="background",
                default="#2f2f2f")
        # TODO: better handling of font family arguments
        style_args.add_argument(
                "--font-family",
                help="Path to TTF file for text",
                dest="font_family",
                default=(
                    "/usr/share/fonts/truetype/ttf-dejavu/DejaVuSansMono.ttf"))
        style_args.add_argument(
                "--font-size",
                help="Size of text in pixels",
                type=int,
                dest="font_size",
                default=12)
        style_args.add_argument(
                "--font-color",
                help="Color of the text (e.g. 'black', '#000000')",
                dest="font_color",
                default="#eeeeee")
        style_args.add_argument(
                "--heading-font-family",
                help="Path to TTF file for heading",
                dest="heading_font_family",
                default=(
                    "/usr/share/fonts/truetype/ttf-dejavu/"
                    "DejaVuSansMono-Bold.ttf"))
        style_args.add_argument(
                "--heading-font-size",
                help="Size of heading in pixels",
                type=int,
                dest="heading_font_size",
                default=24)
        style_args.add_argument(
                "--heading-font-color",
                help="Color of the heading (e.g. 'black', '#000000')",
                dest="heading_color",
                default="#575757")

        parser.add_argument(
                "files",
                nargs="+",
                metavar="FILE",
                type=pathlib.Path)

        # Add backend options
        parser.add_argument(
                "-b", "--backend",
                help=(
                    "Backend used to capture images from video. Possible "
                    "values are 'gstreamer' (default) and 'mplayer'. The "
                    "gstreamer backend is recommended because it is faster, "
                    "has better support for video formats and more correctly "
                    "determines thumbnail timestamps."),
                choices=list(backends.keys()),
                dest="backend",
                default="gstreamer")

        # Obtain backend, add backend argument group and reparse
        args, _ = parser.parse_known_args()
        backend = backends[args.backend]
        backend_argument_group = backend.get_argument_parser_group(parser)
        if backend_argument_group:
            parser.add_argument_group(backend_argument_group)

        args = parser.parse_args()
        self.args = args
        self.backend = backend
        self.files = args.files

    def start(self):
        logging.basicConfig(level=self.args.logging_level)

        # Create temporary directory
        with tempfile.TemporaryDirectory(prefix="video_previewer") as tmp_dir:
            self.tmp_dir = tmp_dir

            self.backend = self.backend(self.args, self.tmp_dir)
            # Start working
            for file in self.files:
                if not file.is_file():
                    logging.error("File '%s' does not exist." % file)
                    continue

                self.process_file(file)

    # Generate thumbnail for a video
    def process_file(self, file_name):
        logging.info("Started processing file '%s'" % file_name)
        info = self.backend.load_file(file_name)

        width = self.args.thumbnail_width
        height = self.args.thumbnail_height
        # If neither width nor height is specified in the options, determine
        # the values so that both of the sizes are greater than 150 and the
        # movie's aspect ratio is preserved
        if width is None and height is None:
            if info["height"] < info["width"]:
                height = 150
            else:
                width = 150

        # If one of width or height is specified in options and the other is
        # not, determine the value for the other one that will preserve the
        # movie's aspect ratio
        if width is not None and height is None:
            height = int(width * info["height"] / info["width"])
        if height is not None and width is None:
            width = int(height * info["width"] / info["height"])

        logging.info("Individual thumbnails will be resized to %dx%d"
                     % (width, height))

        # Determine list of capture times to pass along back to the backend
        logging.debug("Calculating frame capture times.")
        frame_count = self.args.grid_rows * self.args.grid_cols
        part_length = (
                float(
                    info["duration"] - 2 * self.backend.frame_capture_padding)
                / (frame_count + 1))
        if self.args.capture_focus == "none":
            # All the time intervals between two frames should be equal length.
            frame_times = [part_length + self.backend.frame_capture_padding]
            last = frame_times[0]
            for i in range(1, frame_count):
                last = last + part_length
                frame_times.append(last)
        else:
            # The list of time intervals between two frames for the 'end' case
            # should look something like this:
            # (N = number of intervals == frame_count + 1)
            # base + delta * (N - 1); base + delta * (N - 2); ...; base
            # The interval is simmetrical for the 'begin' case

            # Their sum must equal (duration - 2 * padding):
            # base * N + delta * ((N - 1) * N / 2) == (duration - 2 * padding)
            # base + delta * (N - 1) / 2 == (duration - 2 * padding) / N
            base = part_length * 0.2
            duration = (
                    info["duration"] - 2 * self.backend.frame_capture_padding)
            delta = (duration / (frame_count + 1) - base) * 2 / frame_count
            # Calculate frame times for "begin" and convert them if focus is at
            # "end"
            frame_times = [base + self.backend.frame_capture_padding]
            last = frame_times[0]
            for i in range(1, frame_count):
                last = last + base + delta * i
                frame_times.append(last)
            if self.args.capture_focus == "end":
                for i in range(frame_count):
                    frame_times[i] = info["duration"] - frame_times[i]
                frame_times.reverse()

        # Capture frames
        frame_files = self.backend.capture_frames(frame_times)
        count = 0
        for file, time in frame_files:
            count += 1
            logging.debug("Resizing and annotating frame %d." % count)
            self.resize_and_annotate_frame(
                    file,
                    width,
                    height,
                    self.backend.capture_time_to_seconds(time))

        logging.info("Finished capturing frames. Creating montage.")
        if self.create_montage(file_name, info, self.tmp_dir, frame_files):
            destination = file_name.with_suffix(".png")
            shutil.move("%s/montage.png" % self.tmp_dir, str(destination))
            logging.info("Saving final thumbnail to '%s'" % destination)

        # Cleanup
        self.backend.unload_file()
        for file, time in frame_files:
            os.remove(file)

    # Transform a captured frame into a thumbnail by resizing it and annotating
    # it's timestamp
    def resize_and_annotate_frame(self, file_name, width, height, time):
        process = subprocess.Popen(
            [str(self.args.path_convert), file_name,
             "-resize", "%dx%d!" % (width, height),
             "-fill", self.args.font_color,
             "-undercolor", "%s80" % self.args.background,
             "-font", self.args.font_family,
             "-pointsize", str(self.args.font_size),
             "-gravity", "NorthEast",
             "-annotate", "+0+0", " %s " % time_format(time),
             "-bordercolor", self.args.font_color,
             "-border", "1x1",
             file_name],
            shell=False)
        process.wait()

    # Create a montage of all frame captures from the tmp directory
    def create_montage(self, file_name, info, tmp_dir, files):
        rows = self.args.grid_rows
        cols = self.args.grid_cols
        if len(files) != rows * cols:
            rows = int(math.ceil(float(len(files)) / cols))
            logging.info("Only %d captures, so the "
                         "grid will be %d by %d" % (len(files), rows, cols))

        montage_file_name = "%s/montage.png" % tmp_dir
        process = subprocess.Popen(
            [str(self.args.path_montage),
             "-geometry", "+%d+%d" % (self.args.grid_spacing,
                                      self.args.grid_spacing),
             "-background", self.args.background,
             "-fill", self.args.font_color,
             "-tile", "%dx%d" % (cols, rows)]
            + [item[0] for item in files]
            + [montage_file_name],
            shell=False)
        process.wait()
        if not os.path.isfile(montage_file_name):
            logging.error("Error creating montage.")
            return False

        # Annotate montage with title and header
        title = self.args.title or self.backend.info.get("title", None)
        if title is None:
            title = file_name.name
        header = self.get_header_text(file_name, info)
        process = subprocess.Popen(
            [str(self.args.path_convert),
             "-background", self.args.background,
             "-bordercolor", self.args.background,

             # Title
             "-fill", self.args.heading_color,
             "-font", self.args.heading_font_family,
             "-pointsize", str(self.args.heading_font_size),
             "label:%s" % title,

             # Header
             "-fill", self.args.font_color,
             "-font", self.args.font_family,
             "-pointsize", str(self.args.font_size),
             "label:%s" % header,

             # Border for title and header
             "-border", "%dx%d" % (self.args.grid_spacing, 0),

             # Montage
             montage_file_name,
             # Border for montage
             "-border", "%dx%d" % (self.args.grid_spacing,
                                   self.args.grid_spacing),
             "-append",
             montage_file_name],
            shell=False)
        process.wait()
        return True

    # Determine what will be written to the thumbnail's header
    def get_header_text(self, file_name, info):
        file_size = file_name.stat().st_size
        text = "Size   : %s (%d bytes)\n" % (
                file_size_format(file_size),
                file_size)
        text += "Length : %s\n" % time_format(
                self.backend.capture_time_to_seconds(info["duration"]))

        video_info = []
        if "width" in info and "height" in info:
            video_info.append("%dx%d" % (info["width"], info["height"]))
        if "video_codec" in info:
            video_info.append("%s" % info["video_codec"])
        if "video_framerate" in info:
            video_info.append("%.2f frames/sec" % info["video_framerate"])
        if "video_bitrate" in info:
            video_info.append("%.2f kb/sec" % (info["video_bitrate"] / 1024.0))
        if "video_interlaced" in info and info["video_interlaced"]:
            video_info.append("interlaced")
        if len(video_info):
            text += "Video  : " + ", ".join(video_info) + "\n"

        audio_info = []
        if "audio_channels" in info:
            audio_info.append("%d channel(s)" % info["audio_channels"])
        if "audio_codec" in info:
            audio_info.append("%s" % info["audio_codec"])
        if "audio_rate" in info:
            audio_info.append("%.2f kHz" % (info["audio_rate"] / 1000.0))
        if "audio_bitrate" in info:
            audio_info.append("%.2f kb/sec" % (info["audio_bitrate"] / 1024.0))
        if len(audio_info):
            text += "Audio  : " + ", ".join(audio_info)

        logging.debug("Created image header text:\n%s" % text)
        return text


main = CLIMain()
main.start()
