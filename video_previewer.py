#!/usr/bin/python

__version__ = "0.1"

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

# Determine video's information using the 'midentify' application
def get_video_info(file_name):
    logging.debug(
        "Using '%s' to get video's information." % options.path_midentify
    )

    process = subprocess.Popen([options.path_midentify, file_name],
                               shell=False, stdout=subprocess.PIPE)
    info = process.stdout.readlines()
    process.wait()

    info_dict = {}
    for line in info:
        (key, value) = line.split("=")
        value = value.replace("\\", "").replace("\n", "")
        if key in ("ID_VIDEO_WIDTH", "ID_VIDEO_HEIGHT", "ID_AUDIO_NCH"):
            value = int(value)
        if key in ("ID_LENGTH", "ID_VIDEO_FPS", "ID_VIDEO_BITRATE",
                   "ID_AUDIO_RATE", "ID_AUDIO_BITRATE"):
            value = float(value)
        info_dict[key] = value

    return info_dict

# Returns a humanized string for a given amount of seconds
def time_format(seconds):
    seconds = int(seconds)
    return "%d:%02d:%02d" % (
        seconds / 3600,
        (seconds % 3600) / 60,
        seconds % 60
    )

# Returns a humanized string for a given amount of bytes
def file_size_format(bytes, precision=2):
    bytes = int(bytes)
    if bytes == 0:
        return '0 B'
    log = int(math.floor(math.log(bytes, 1024)))
    # Better safe than sorry
    if 1024 ** log > bytes:
        log -= 1
    if 1024 ** (log + 1) <= bytes:
        log += 1

    return "%.*f%s" % (
        precision,
        bytes / (1024.0 ** log),
        ["B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"][log]
    )

# Capture the video's image at a certain time and save it to
# destination. If destination is None then the generated file
# is simply deleted and True or False is returned depending on
# if the capture was successful.
def capture_frame(file_name, time, tmp_dir, destination=None):
    # TODO: figure out how to run this with shell=False
    # so we have proper escaping of file names
    process = subprocess.Popen(
        "%s -really-quiet -nosound "
        "-vo png:z=3:outdir='%s' "
        "-frames 1 -ss %f '%s'" % (options.path_mplayer,
                                   tmp_dir,
                                   time, file_name),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    output = process.stdout.read()
    error = process.stderr.read()
    process.wait()

    if not os.path.isfile("%s/00000001.png" % tmp_dir):
        if destination is not None:
            logging.error("Something went wrong when trying "
                          "to capture frame at %d seconds" % time)
        return False
    else:
        if destination is not None:
            shutil.move("%s/00000001.png" % tmp_dir, destination)
        else:
            os.remove("%s/00000001.png" % tmp_dir)
        return True

# Transform a captured frame into a thumbnail by resizing it
# and annotating it's timestamp
def resize_and_annotate_frame(file_name, width, height, time):
    process = subprocess.Popen(
        [options.path_convert, file_name,
         "-resize", "%dx%d!" % (width, height),
         "-fill", options.font_color,
         "-undercolor", "%s80" % options.background,
         "-font", options.font_family,
         "-pointsize", str(options.font_size),
         "-gravity", "NorthEast",
         "-annotate", "+0+0", " %s " % time_format(time),
         "-bordercolor", options.font_color,
         "-border", "1x1",
         file_name],
        shell=False
    )
    process.wait()

# Determine what will be written to the thumbnail's header
def get_header_text(file_name, info):
    file_size = os.stat(file_name).st_size
    text  = "Size   : %s (%d bytes)\n" % (file_size_format(file_size), file_size)
    text += "Length : %s\n" % time_format(info["ID_LENGTH"])

    # midentify doesn't work properly on some wmv files
    # and it returns ID_VIDEO_FPS == 1000. In this case
    # we don't print that information
    if info["ID_VIDEO_FPS"] == 1000:
         text += "Video  : %dx%d (%s)\n" % (
            info["ID_VIDEO_WIDTH"],
            info["ID_VIDEO_HEIGHT"],
            info["ID_VIDEO_FORMAT"]
        )
    else:
        text += "Video  : %dx%d (%s, %.2f frames/sec, %.2f kb/sec)\n" % (
            info["ID_VIDEO_WIDTH"],
            info["ID_VIDEO_HEIGHT"],
            info["ID_VIDEO_FORMAT"],
            info["ID_VIDEO_FPS"],
            (info["ID_VIDEO_BITRATE"] / 1024.0)
        )
    text += "Audio  : %d chan (%s, %.2f kHz, %.2f kb/sec)" % (
        info["ID_AUDIO_NCH"],
        info["ID_AUDIO_CODEC"],
        (info["ID_AUDIO_RATE"] / 1000.0),
        (info["ID_AUDIO_BITRATE"] / 1024.0)
    )

    logging.debug("Created image header text:\n%s" % text)
    return text

# Create a montage of all frame captures from the tmp directory
def create_montage(file_name, info, tmp_dir, files):
    rows = options.grid_rows
    cols = options.grid_cols
    if len(files) != rows * cols:
        rows = int(math.ceil(float(len(files)) / cols))
        logging.info("Only %d captures, so the "
                     "grid will be %d by %d" % (len(files), rows, cols))

    montage_file_name = "%s/montage.png" % tmp_dir
    process = subprocess.Popen(
        [options.path_montage,
         "-geometry", "+%d+%d" % (options.grid_spacing, options.grid_spacing),
         "-background", options.background,
         "-fill", options.font_color,
         "-tile", "%dx%d" % (cols, rows)]
        + files
        + [montage_file_name],
        shell=False
    )
    process.wait()
    if not os.path.isfile(montage_file_name):
        logging.error("Error creating montage.")
        return False

    # Annotate montage with title and header
    title = options.title
    if title is None:
        title = os.path.basename(file_name)
    header = get_header_text(file_name, info)
    process = subprocess.Popen(
        [options.path_convert,
         "-background", options.background,
         "-bordercolor", options.background,

         # Title
         "-fill", options.heading_color,
         "-font", options.heading_font_family,
         "-pointsize", str(options.heading_font_size),
         "label:%s" % title,

         # Header
         "-fill", options.font_color,
         "-font", options.font_family,
         "-pointsize", str(options.font_size),
         "label:%s" % header,

         # Border for title and header
         "-border", "%dx%d" % (options.grid_spacing, 0),

         # Montage
         montage_file_name,
         # Border for montage
         "-border", "%dx%d" % (options.grid_spacing,
                               options.grid_spacing),
         "-append",
         montage_file_name],
        shell=False
    )
    process.wait()
    return True

# Generate thumbnail for a video
def process_file(file_name):
    logging.info("Started processing file '%s'" % file_name)
    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="video_thumbnailer")
    info = get_video_info(file_name)

    width = options.thumbnail_width
    height = options.thumbnail_height
    # If neither width nor height is specified in the options,
    # determine the values so that both of the sizes are greater
    # than 150 and the movie's aspect ratio is preserved
    if width is None and height is None:
        if info["ID_VIDEO_HEIGHT"] < info["ID_VIDEO_WIDTH"]:
            height = 150
        else:
            width = 150

    # If one of width or height is specified in options and the
    # other is not, determine the value for the other one that
    # will preserve the movie's aspect ratio
    if width is not None and height is None:
        height = int(width * info["ID_VIDEO_HEIGHT"] / info["ID_VIDEO_WIDTH"])
    if height is not None and width is None:
        width = int(height * info["ID_VIDEO_WIDTH"] / info["ID_VIDEO_HEIGHT"])

    logging.info("Individual thumbnails will be "
                 "resized to %dx%d" % (width, height))

    # Determine at which points in the video should a thumbnail be taken
    logging.debug("Calculating frame capture times.")
    length = info["ID_LENGTH"] - 1
    length_scale = 1.0
    frames = options.grid_rows * options.grid_cols

    # midentify doesn't work properly on some wmv files
    # and it returns ID_VIDEO_FPS == 1000. In this case
    # we have to scale the length of the movie so that
    # mplayer correctly captures all the needed frames.
    if info["ID_VIDEO_FPS"] == 1000:
        logging.info("Not properly supported WMV format detected. "
                     "Adjusting times...")

        # Binary search the ammount with which to scale the length
        # so that capturing doesn't produce any errors
        left = 0.0
        right = 2.0
        EPS = 0.001
        while right - left >= EPS:
            middle = (right + left) * 0.5
            if capture_frame(file_name, length * middle, tmp_dir):
                left = middle + EPS
            else:
                right = middle - EPS

        length_scale = left - EPS
        length *= length_scale
        logging.debug("The determined scale is %f." % length_scale)
        logging.info("Finished adjusting times. Starting frame capture.")

    # Determining list of capture times
    part_length = float(length - 1) / (frames - 1)
    times = [1]
    last = 1.0
    if options.capture_focus == "none":
        # All the time intervals between two frames should be equal length.
        for i in range(1, frames):
            last = last + part_length
            times.append(last)
    else:
        # The list of time intervals between two frames
        # for the 'end' case should look something like this:
        # (N = number of intervals == frames - 1)
        # base + delta * (N - 1); base + delta * (N - 2); ...; base
        # The interval is simmetrical for the 'begin' case

        # Their sum must equal (length - 1):
        # base * N + delta * ((N - 1) * N / 2) == (length - 1)
        # base + delta * (N - 1) / 2 == (length - 1) / N
        base = part_length * 0.2
        delta = ((length - 1) / (frames - 1) - base) * 2 / (frames - 2)
        if options.capture_focus == "begin":
            for i in range(0, frames - 1):
                last = last + base + delta * i
                times.append(last)
        else:
            for i in range(frames - 2, -1, -1):
                last = last + base + delta * i
                times.append(last)

    # Capture frames
    frame_files = []
    for time in times:
        logging.debug(
            "Capturing frame number %d at %d seconds." % (
                len(frame_files) + 1, int(time / length_scale)
            )
        )
        frame_file_name = "%s/frame-%0*d.png" % (
            tmp_dir,
            int(math.ceil(math.log(length + 1, 10))),
            time
        )
        if capture_frame(file_name, time, tmp_dir, frame_file_name):
            resize_and_annotate_frame(frame_file_name,
                                      width, height,
                                      time / length_scale)
            frame_files.append(frame_file_name)

    logging.info("Finished capturing frames. Creating montage.")
    if create_montage(file_name, info, tmp_dir, frame_files):
        destination = file_name.rsplit(".", 1)[0]
        shutil.move("%s/montage.png" % tmp_dir,
                    "%s.png" % destination)
        logging.info("Saving final thumbnail to '%s.png'" % destination)

    for file in frame_files:
        os.remove(file)
    os.rmdir(tmp_dir)

# Build command line arguments parser
parser = optparse.OptionParser(
    usage="Usage: %prog [options] file [file ...]",
    description="Cross-platform python tool which generates a video's "
                "index preview with multiple screen capture thumbnails.",
    version="%%prog %s" % __version__
)

parser.set_defaults(
    logging_level=logging.INFO,
)
parser.add_option("-v", "--verbose",
                  help="Print more detalied information",
                  action="store_const", const=logging.DEBUG,
                  dest="logging_level")
parser.add_option("-q", "--quiet",
                  help="Refrain from outputing anything",
                  action="store_const", const=logging.CRITICAL,
                  dest="logging_level")

# Add options to specify paths for each needed application
app_list = ("mplayer", "midentify", "convert", "montage")
for app in app_list:
    parser.set_defaults(
        **{"path_%s" % app: which_or_None(app)}
    )
    parser.add_option(
        "--path-%s" % app,
        help="Specify own path for '%s' application (optional)" % app,
        action="store", dest="path_%s" % app
    )

# Add options related to the resulting thumbnail such as
# number of rows or columns, width and height of the thumbnails,
# the space between them etc
capture_opts = optparse.OptionGroup(parser, "Capture options")
capture_opts.add_option("-r", "--rows",
                        help="Number of rows the generated grid "
                             "should contain (default %default).",
                        action="store", type="int",
                        dest="grid_rows", default=6)
capture_opts.add_option("-c", "--cols", "--columns",
                        help="Number of columns the generated grid "
                             "should contain (default %default).",
                        action="store", type="int",
                        dest="grid_cols", default=4)
capture_opts.add_option("-t", "--title",
                        help="Title for the thumbnail "
                             "(video's name is default).",
                        action="store",
                        dest="title", default=None)
capture_opts.add_option("-W", "--width",
                        help="The width of a single image "
                             "in the grid in pixels.",
                        action="store", type="int",
                        dest="thumbnail_width", default=None)
capture_opts.add_option("-H", "--height",
                        help="The height of a single image "
                             "in the grid in pixels. "
                             "If only one of the width and height argument "
                             "are specified, the other one will be "
                             "determined so that the aspect ratio "
                             "of the movie is preserved.",
                        action="store", type="int",
                        dest="thumbnail_height", default=None)
capture_opts.add_option("-S", "--spacing",
                        help="The space between images "
                             "in the grid in pixels. (default %default)",
                        action="store", type="int",
                        dest="grid_spacing", default=4)
capture_opts.add_option("--focus",
                        help="Focus on the beginning or the ending of "
                             "the movie. That means a greater number of "
                             "thumbnails will be generated in the "
                             "specified area than in the other part. "
                             "For example if the focus is on the beginning "
                             "of the movie, the frequency of captures drops "
                             "as time goes by. "
                             "Possible values are 'begin', 'end' and 'none'."
                             " (default is 'none')",
                        action="store", type="string",
                        dest="capture_focus", default="none")
parser.add_option_group(capture_opts)

# Add style related options
style_options = optparse.OptionGroup(parser, "Style options")
style_options.add_option("--background",
                         help="Background color (e.g. '#00ff00')",
                         action="store", type="string",
                         dest="background", default="#2f2f2f")
# TODO: better handling of font family arguments
style_options.add_option("--font-family",
                         help="Path to TTF file for text",
                         action="store", type="string",
                         dest="font_family",
                         default="/usr/share/fonts/dejavu/DejaVuSansMono.ttf")
style_options.add_option("--font-size",
                         help="Size of text in pixels",
                         action="store", type="int",
                         dest="font_size", default=12)
style_options.add_option("--font-color",
                         help="Color of the text (e.g. 'black', '#000000')",
                         action="store", type="string",
                         dest="font_color", default="#eeeeee")
style_options.add_option("--heading-font-family",
                         help="Path to TTF file for heading",
                         action="store", type="string",
                         dest="heading_font_family",
                         default="/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf")
style_options.add_option("--heading-font-size",
                         help="Size of heading in pixels",
                         action="store", type="int",
                         dest="heading_font_size", default=24)
style_options.add_option("--heading-font-color",
                         help="Color of the heading (e.g. 'black', '#000000')",
                         action="store", type="string",
                         dest="heading_color", default="#575757")
parser.add_option_group(style_options)

# Parse arguments
(options, args) = parser.parse_args()

logging.basicConfig(level=options.logging_level)
# Check that all dependencies are met.
for app in app_list:
    app_path = getattr(options, "path_%s" % app)
    if app_path is None or not os.access(app_path, os.F_OK | os.R_OK | os.X_OK):
        parser.error("Could not find application '%s' in the path. "
                     "Please specify it's location using the "
                     "--path-%s argument." % (app, app))

if options.capture_focus not in ('none', 'begin', 'end'):
    parser.error("--focus argument only accepts 'none', 'begin' and 'end'")

# Check that we have at least one file to parse.
if len(args) == 0:
    parser.error("Please specify at least one file for "
                 "which to generate the thumbnails")

# Start working
for file in args:
    import os
    if not os.path.isfile(file):
        logging.error("File '%s' does not exist." % file)
        continue

    process_file(file)

