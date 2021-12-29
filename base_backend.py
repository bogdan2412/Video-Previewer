import logging

from util import safe_int_log


# Base-class for capturing backends
class BaseBackend(object):
    def __init__(self, args, tmp_dir):
        self.args = args
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
    def get_argument_parser_group(parser):
        return None

    # Load a video file and determine information about it such as width,
    # height, framerate, etc.
    # The file name should be stored in self.file_name and the information
    # should be stored in a dict in self.info and returned by the method.
    def load_file(self, file_name):
        self.file_name = file_name
        raise NotImplementedError(
            "This method should be overriden by subclasses.")

    # Unload the file
    def unload_file(self):
        self.file_name = None

    # Capture the loaded video's image at a certain time and save it to
    # destination. If the capture was not successful False is returned.
    # However, if the capture was successful, the frame timestamp should be
    # returned since the backend may not be able to capture the frame at
    # exactly the specified time. If destination is None then the generated
    # file is simply deleted.
    def capture_frame(self, capture_time, destination=None):
        raise NotImplementedError(
            "This method should be overriden by subclasses.")

    # Receives a list of capture times of all the frames and captures them.
    # Returns a list of the captured frame files.
    def capture_frames(self, frame_times):
        frame_files = []
        for time in frame_times:
            current = len(frame_files) + 1
            logging.debug(
                    "Capturing frame number %d at %f seconds."
                    % (current, self.capture_time_to_seconds(time)))
            frame_file_name = "frame-%0*d.png" % (
                    safe_int_log(len(frame_times), 10) + 1, current)
            frame_file = self.tmp_dir / frame_file_name
            capture_time = self.capture_frame(time, frame_file)
            if capture_time:
                frame_files.append((frame_file, capture_time))
        return frame_files


__all__ = ["BaseBackend"]
