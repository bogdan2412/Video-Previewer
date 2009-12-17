#!/usr/bin/python

__version__ = "0.1.9"

__copyright__ = """
Copyright (c) 2009, bogdan2412
"""

__license__ = """
All source code available in this repository is covered by a GPLv2 license.
"""

import optparse
import logging
import subprocess
import math
import shutil
import os

def which_or_None(str):
    import which
    try:
        return which.which(str)
    except which.WhichError:
        return None

# Returns a humanized string for a given amount of seconds
def time_format(seconds):
    seconds = int(seconds)
    return "%d:%02d:%02d" % (
        seconds / 3600,
        (seconds % 3600) / 60,
        seconds % 60
    )

# Safe conversion of logarithm to floor integer value
def safe_int_log(value, base):
    log = int(math.floor(math.log(value, base)))
    while base ** log > value:
        log -= 1
    while base ** (log + 1) <= value:
        log += 1
    return log

# Returns a humanized string for a given amount of bytes
def file_size_format(bytes, precision=2):
    bytes = int(bytes)
    if bytes == 0:
        return '0 B'
    log = safe_int_log(bytes, 1024)

    return "%.*f%s" % (
        precision,
        bytes / (1024.0 ** log),
        ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"][log]
    )

# Defines a new optparse type to use with application paths
def check_apppath(option, opt, value):
    if not os.access(value, os.F_OK | os.R_OK | os.X_OK):
        raise optparse.OptionValueError(
            "option %s: Specified application could not be found" % opt)
    return value

class OptionAppPath(optparse.Option):
    from copy import copy
    TYPES = optparse.Option.TYPES + ("apppath",)
    TYPE_CHECKER = copy(optparse.Option.TYPE_CHECKER)
    TYPE_CHECKER["apppath"] = check_apppath

# Subclass of optparse.OptionParser which checks that all "apppath" options
# have non-Null values.
class OptionParser(optparse.OptionParser):
    def check_values(self, values, args):
        def check_option(option):
            if (option.type == "apppath" and
                getattr(values, option.dest, None) is None):
                self.error("Could not find application needed to run. "
                    "Please specify it's location using the %s argument."
                    % option.get_opt_string())

        for option in self.option_list:
            check_option(option)
        for group in self.option_groups:
            # Skip over backend groups if they were not selected
            if (group.title.lower().find("backend") != -1
                and group.title.lower().find(values.backend) == -1):
                continue

            for option in group.option_list:
                check_option(option)

        return (values, args)

# Base-class for capturing backends
class Backend:
    def __init__(self, options, tmp_dir):
        self.options = options
        self.tmp_dir = tmp_dir
        # When calculating frame capture times, this specifies a padding at the
        # beginning and ending of the video so that there are no problems with
        # the capture times not being in bounds.
        self.frame_capture_padding = 0.5

    # Converts a frame capture time into seconds.
    def capture_time_to_seconds(self, time):
        return time

    # Should be subclassed if the backend is configurable from command-line
    @staticmethod
    def get_option_parser_group(parser):
        return None

    # Determine video information such as width, height, framerate, etc.
    # The information should be stored in a dict in self.info and also returned
    def get_video_info(self, file_name):
        raise NotImplementedError(
            "This method should be overriden by subclasses.")

    # Capture the video's image at a certain time and save it to destination
    # If destination is None then the generated file is simply deleted and True
    # or False is returned depending on if the capture was successful.
    def capture_frame(self, file_name, time, destination=None):
        raise NotImplementedError(
            "This method should be overriden by subclasses.")

    # Receives a list of capture times of all the frames and captures, resizes
    # and annotates them. Returns a list of files.
    def capture_frames(self, file_name, frame_times):
        frame_files = []
        for time in frame_times:
            current = len(frame_files) +1
            logging.debug("Capturing frame number %d at %f seconds."
                % (current, self.capture_time_to_seconds(time)))
            frame_file_name = "%s/frame-%0*d.png" % (
                self.tmp_dir, safe_int_log(len(frame_times), 10) + 1, current)
            if self.capture_frame(file_name, time, frame_file_name):
                frame_files.append((frame_file_name, time))
        return frame_files

class MPlayerBackend(Backend):
    # Reverse scale applied in get_video_info
    def capture_time_to_seconds(self, time):
        return time / self.duration_scale

    # Add path options for mplayer and midentify
    @staticmethod
    def get_option_parser_group(parser):
        optgroup = optparse.OptionGroup(parser, "MPlayer Backend Options",
            "Only necessary if you choose this backend.")
        app_list = ("mplayer", "midentify")
        for app in app_list:
            optgroup.add_option(
                "--path-%s" % app,
                help="Specify own path for '%s' application (optional)" % app,
                action="store", type="apppath", dest="path_%s" % app,
                default=which_or_None(app))
        return optgroup

    # Determine video's information using the 'midentify' application
    def get_video_info(self, file_name):
        logging.debug("Using '%s' to get video's information."
                      % self.options.path_midentify)

        process = subprocess.Popen([self.options.path_midentify, file_name],
            shell=False, stdout=subprocess.PIPE)
        output = process.stdout.readlines()
        process.wait()

        info = {}
        # Convert information outputted by midentify into a cross-backend form.
        info_conv = {
            "ID_LENGTH": ("duration", float),
            "ID_VIDEO_WIDTH": ("width", int),
            "ID_VIDEO_HEIGHT": ("height", int),
            "ID_VIDEO_FPS": ("video_framerate", float),
            "ID_VIDEO_BITRATE": ("video_bitrate", float),
            "ID_VIDEO_FORMAT": ("video_format", str),
            "ID_AUDIO_NCH": ("audio_channels", int),
            "ID_AUDIO_RATE": ("audio_rate", float),
            "ID_AUDIO_BITRATE": ("audio_bitrate", float),
            "ID_AUDIO_CODEC": ("audio_codec", str),
        }
        for line in output:
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
                if self.capture_frame(file_name, info["duration"] * middle):
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
    def capture_frame(self, file_name, time, destination=None):
        # TODO: figure out how to run this with shell=False so we have proper
        # escaping of file names
        process = subprocess.Popen(
            "%s -really-quiet -nosound -vo png:z=3:outdir='%s' -frames 1 "
            "-ss %f '%s'" % (
                self.options.path_mplayer, self.tmp_dir, time, file_name),
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        output = process.stdout.read()
        error = process.stderr.read()
        process.wait()

        if not os.path.isfile("%s/00000001.png" % self.tmp_dir):
            if destination is not None:
                logging.error("Something went wrong when trying to capture "
                    "frame at %d seconds" % time)
            return False
        else:
            if destination is not None:
                shutil.move("%s/00000001.png" % self.tmp_dir, destination)
            else:
                os.remove("%s/00000001.png" % self.tmp_dir)
            return True

class GStreamerBackend(Backend):
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Gstreamer backend isn't done yet.")

class CLIMain:
    def __init__(self):
        # Build command line arguments parser
        parser = OptionParser(
            usage="Usage: %prog [options] file [file ...]",
            description="Cross-platform python tool which generates a video's "
                "index preview with multiple screen capture thumbnails.",
            version="%%prog %s" % __version__,
            option_class=OptionAppPath)

        parser.set_defaults(logging_level=logging.INFO)
        parser.add_option("-v", "--verbose",
            help="Print more detalied information",
            action="store_const", const=logging.DEBUG, dest="logging_level")
        parser.add_option("-q", "--quiet",
            help="Refrain from outputing anything",
            action="store_const", const=logging.CRITICAL, dest="logging_level")

        # Add options to specify paths for each needed application
        self.app_list = ("convert", "montage")
        for app in self.app_list:
            parser.add_option(
                "--path-%s" % app,
                help="Specify own path for '%s' application (optional)" % app,
                action="store", type="apppath", dest="path_%s" % app,
                default=which_or_None(app))

        # Add options related to the resulting thumbnail such as
        # number of rows or columns, width and height of the thumbnails,
        # the space between them etc
        capture_opts = optparse.OptionGroup(parser, "Capture options")
        capture_opts.add_option("-r", "--rows",
            help="Number of rows the generated grid "
                 "should contain (default %default).",
            action="store", type="int", dest="grid_rows", default=6)
        capture_opts.add_option("-c", "--cols", "--columns",
            help="Number of columns the generated grid "
                 "should contain (default %default).",
            action="store", type="int", dest="grid_cols", default=4)
        capture_opts.add_option("-t", "--title",
            help="Title for the thumbnail (video's name is default).",
            action="store", dest="title", default=None)
        capture_opts.add_option("-W", "--width",
            help="The width of a single image in the grid in pixels.",
            action="store", type="int", dest="thumbnail_width", default=None)
        capture_opts.add_option("-H", "--height",
            help="The height of a single image in the grid in pixels. "
                 "If only one of the width and height argument are specified, "
                 "the other one will be determined so that the aspect ratio "
                 "of the movie is preserved.",
            action="store", type="int", dest="thumbnail_height", default=None)
        capture_opts.add_option("-S", "--spacing",
            help="The space between images in the grid in pixels. "
                 "(default %default)",
            action="store", type="int", dest="grid_spacing", default=4)
        capture_opts.add_option("--focus",
            help="Focus on the beginning or the ending of the movie. That "
                 "means a greater number of thumbnails will be generated in "
                 "the specified area than in the other part. For example if "
                 "the focus is on the beginning of the movie, the frequency "
                 "of captures drops as time goes by. Possible values are "
                 "'begin', 'end' and 'none'. (default is 'none')",
            action="store", type="choice", choices=("begin", "end", "none"),
            dest="capture_focus", default="none")
        parser.add_option_group(capture_opts)

        # Add style related options
        style_options = optparse.OptionGroup(parser, "Style options")
        style_options.add_option("--background",
            help="Background color (e.g. '#00ff00')",
            action="store", type="string", dest="background",
            default="#2f2f2f")
        # TODO: better handling of font family arguments
        style_options.add_option("--font-family",
            help="Path to TTF file for text",
            action="store", type="string", dest="font_family",
            default="/usr/share/fonts/truetype/ttf-dejavu/DejaVuSansMono.ttf")
        style_options.add_option("--font-size",
            help="Size of text in pixels",
            action="store", type="int", dest="font_size", default=12)
        style_options.add_option("--font-color",
            help="Color of the text (e.g. 'black', '#000000')",
            action="store", type="string", dest="font_color",
            default="#eeeeee")
        style_options.add_option("--heading-font-family",
            help="Path to TTF file for heading",
            action="store", type="string", dest="heading_font_family",
            default="/usr/share/fonts/truetype/ttf-dejavu/"
                    "DejaVuSansMono-Bold.ttf")
        style_options.add_option("--heading-font-size",
            help="Size of heading in pixels",
            action="store", type="int", dest="heading_font_size", default=24)
        style_options.add_option("--heading-font-color",
            help="Color of the heading (e.g. 'black', '#000000')",
            action="store", type="string", dest="heading_color",
            default="#575757")
        parser.add_option_group(style_options)

        # Add backend options
        self.backends = {"mplayer": MPlayerBackend,
            "gstreamer": GStreamerBackend}
        parser.add_option("-b", "--backend",
            help="Backend used to capture images from video",
            action="store", type="choice", choices=self.backends.keys(),
            dest="backend", default="mplayer")
        for backend in self.backends.values():
            option_group = backend.get_option_parser_group(parser)
            if option_group:
                parser.add_option_group(option_group)
        self.option_parser = parser

    def start(self):
        # Parse arguments
        (options, args) = self.option_parser.parse_args()
        logging.basicConfig(level=options.logging_level)

        # Check that we have at least one file to parse.
        if len(args) == 0:
            self.option_parser.error(
                "Please specify at least one file for which to generate the "
                "thumbnails")

        self.options, self.files = options, args

        # Create temporary directory
        import tempfile
        self.tmp_dir = tempfile.mkdtemp(prefix="video_previewer")

        self.backend = self.backends[self.options.backend](
            self.options, self.tmp_dir)
        # Start working
        for file in args:
            if not os.path.isfile(file):
                logging.error("File '%s' does not exist." % file)
                continue

            self.process_file(file)

        # Cleanup temporary directory
        os.rmdir(self.tmp_dir)

    # Generate thumbnail for a video
    def process_file(self, file_name):
        logging.info("Started processing file '%s'" % file_name)
        info = self.backend.get_video_info(file_name)

        width = self.options.thumbnail_width
        height = self.options.thumbnail_height
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
        frame_count = self.options.grid_rows * self.options.grid_cols
        part_length = float(
            info["duration"] - 2 * self.backend.frame_capture_padding
        ) / (frame_count + 1)
        if self.options.capture_focus == "none":
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
            duration = info["duration"] - 2 * self.backend.frame_capture_padding
            delta = (duration / (frame_count + 1) - base) * 2 / frame_count
            # Calculate frame times for "begin" and convert them if focus is at "end"
            frame_times = [base + self.backend.frame_capture_padding]
            last = frame_times[0]
            for i in range(1, frame_count):
                last = last + base + delta * i
                frame_times.append(last)
            if self.options.capture_focus == "end":
                for i in range(frame_count):
                    frame_times[i] = info["duration"] - frame_times[i]
                frame_times.reverse()

        # Capture frames
        frame_files = self.backend.capture_frames(file_name, frame_times)
        count = 0
        for file, time in frame_files:
            count += 1
            logging.debug("Resizing and annotating frame %d." % count)
            self.resize_and_annotate_frame(file, width, height,
                self.backend.capture_time_to_seconds(time))

        logging.info("Finished capturing frames. Creating montage.")
        if self.create_montage(file_name, info, self.tmp_dir, frame_files):
            destination = file_name.rsplit(".", 1)[0]
            shutil.move("%s/montage.png" % self.tmp_dir,
                        "%s.png" % destination)
            logging.info("Saving final thumbnail to '%s.png'" % destination)

        for file, time in frame_files:
            os.remove(file)

    # Transform a captured frame into a thumbnail by resizing it and annotating
    # it's timestamp
    def resize_and_annotate_frame(self, file_name, width, height, time):
        process = subprocess.Popen(
            [self.options.path_convert, file_name,
             "-resize", "%dx%d!" % (width, height),
             "-fill", self.options.font_color,
             "-undercolor", "%s80" % self.options.background,
             "-font", self.options.font_family,
             "-pointsize", str(self.options.font_size),
             "-gravity", "NorthEast",
             "-annotate", "+0+0", " %s " % time_format(time),
             "-bordercolor", self.options.font_color,
             "-border", "1x1",
             file_name],
            shell=False
        )
        process.wait()

    # Create a montage of all frame captures from the tmp directory
    def create_montage(self, file_name, info, tmp_dir, files):
        rows = self.options.grid_rows
        cols = self.options.grid_cols
        if len(files) != rows * cols:
            rows = int(math.ceil(float(len(files)) / cols))
            logging.info("Only %d captures, so the "
                         "grid will be %d by %d" % (len(files), rows, cols))

        montage_file_name = "%s/montage.png" % tmp_dir
        process = subprocess.Popen(
            [self.options.path_montage,
             "-geometry", "+%d+%d" % (self.options.grid_spacing,
                                      self.options.grid_spacing),
             "-background", self.options.background,
             "-fill", self.options.font_color,
             "-tile", "%dx%d" % (cols, rows)]
            + map(lambda item: item[0], files)
            + [montage_file_name],
            shell=False
        )
        process.wait()
        if not os.path.isfile(montage_file_name):
            logging.error("Error creating montage.")
            return False

        # Annotate montage with title and header
        title = self.options.title
        if title is None:
            title = os.path.basename(file_name)
        header = self.get_header_text(file_name, info)
        process = subprocess.Popen(
            [self.options.path_convert,
             "-background", self.options.background,
             "-bordercolor", self.options.background,

             # Title
             "-fill", self.options.heading_color,
             "-font", self.options.heading_font_family,
             "-pointsize", str(self.options.heading_font_size),
             "label:%s" % title,

             # Header
             "-fill", self.options.font_color,
             "-font", self.options.font_family,
             "-pointsize", str(self.options.font_size),
             "label:%s" % header,

             # Border for title and header
             "-border", "%dx%d" % (self.options.grid_spacing, 0),

             # Montage
             montage_file_name,
             # Border for montage
             "-border", "%dx%d" % (self.options.grid_spacing,
                                   self.options.grid_spacing),
             "-append",
             montage_file_name],
            shell=False
        )
        process.wait()
        return True

    # Determine what will be written to the thumbnail's header
    def get_header_text(self, file_name, info):
        file_size = os.stat(file_name).st_size
        text  = "Size   : %s (%d bytes)\n" % (file_size_format(file_size), file_size)
        text += "Length : %s\n" % time_format(
            self.backend.capture_time_to_seconds(info["duration"])
        )

        video_info = []
        if "width" in info and "height" in info:
            video_info.append("%dx%d" % (info["width"], info["height"]))
        if "video_format" in info:
            video_info.append("%s" % info["video_format"])
        if "video_framerate" in info:
            video_info.append("%.2f frames/sec" % info["video_framerate"])
        if "video_bitrate" in info:
            video_info.append("%.2f kb/sec" % (info["video_bitrate"] / 1024.0))
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
