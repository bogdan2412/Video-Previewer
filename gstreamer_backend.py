import logging
import os
import shutil
import threading
import time

from base_backend import BaseBackend


class GStreamerBackend(BaseBackend):
    def __init__(self, options, tmp_dir):
        self.options = options
        self.tmp_dir = tmp_dir
        self.frame_capture_padding = 0.5 * 1000000000

        # Import python gstreamer libraries
        global gi, GObject, Gst
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import GObject, Gst

        # Create gstreamer player object
        Gst.init(None)
        self.player = Gst.Pipeline.new("player")

        # File source and universal decoder
        filesrc = Gst.ElementFactory.make("filesrc", "file-source")
        decoder = Gst.ElementFactory.make("decodebin", "decoder")
        decoder.connect("pad-added", self.decoder_callback)

        # PNG encoder, Multiple File sink and colorspace converter required by
        # PNG encoder
        colorspace = Gst.ElementFactory.make("videoconvert", "video-sink")
        pngenc = Gst.ElementFactory.make("pngenc", "png-encoder")
        pngenc.set_property("snapshot", True)
        multifilesink = Gst.ElementFactory.make(
                "multifilesink",
                "multi-file-sink")
        multifilesink.set_property(
                "location",
                os.path.join(self.tmp_dir, "output-%05d.png"))
        multifilesink.set_property("post-messages", True)

        # Add elements to player pipeline
        self.player.add(filesrc)
        self.player.add(decoder)
        self.player.add(colorspace)
        self.player.add(pngenc)
        self.player.add(multifilesink)
        Gst.Element.link(filesrc, decoder)
        Gst.Element.link(colorspace, pngenc)
        Gst.Element.link(pngenc, multifilesink)

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)

        # Initialize GObject MainLoop in a new thread
        GObject.threads_init()
        self.main_loop = GObject.MainLoop()

        def main_loop_thread():
            self.main_loop.run()
        self.main_loop_thread = threading.Thread(target=main_loop_thread)
        self.main_loop_thread.daemon = True
        self.main_loop_thread.start()

    # Decoder callback, used to link video pad to sink
    def decoder_callback(self, decoder, pad):
        caps = pad.get_current_caps()
        cap_count = caps.get_size()
        for index in range(cap_count):
            structure_name = caps.get_structure(index).get_name()
            if structure_name.startswith("video"):
                video_sink = self.player.get_by_name("video-sink")
                for video_pad in video_sink.sinkpads:
                    pad.link(video_pad)

    # Handle gstreamer messages
    def on_message(self, bus, message):
        if message.type == Gst.MessageType.TAG:
            tag_conv = {
                Gst.TAG_VIDEO_CODEC: "video_codec",
                Gst.TAG_AUDIO_CODEC: "audio_codec",
                Gst.TAG_TITLE: "title",
                Gst.TAG_BITRATE: "audio_bitrate",
            }

            tags = message.parse_tag()
            for tag_index in range(tags.n_tags()):
                tag_name = tags.nth_tag_name(tag_index)
                tag_size = tags.get_tag_size(tag_name)
                tag_value = tags.get_value_index(tag_name, 0)

                if tag_size != 1:
                    tag_values = [
                            tags.get_value_index(tag_name, index)
                            for index in range(tag_size)
                            ]
                    logging.info(
                            "Unexpected tag with multiple values: %s - %s"
                            % (tag_name, tag_values))

                if tag_name in tag_conv:
                    self.info[tag_conv[tag_name]] = tag_value

        # A frame has been captured
        if message.type == Gst.MessageType.ELEMENT and \
           message.src.get_property("name") == "multi-file-sink":
            structure = message.get_structure()
            self._capture["capture_time"] = structure["timestamp"]
            self._capture["file_name"] = structure["filename"]
            self._capture["done"] = True
            self.player.set_state(Gst.State.PAUSED)

        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            logging.error(debug)
            self.main_loop.quit()
            raise error

    # Converts nanoseconds into seconds.
    def capture_time_to_seconds(self, time):
        return time / 1000000000.0

    # Initialize gstreamer player with the given file
    def load_file(self, file_name):
        self.file_name = file_name
        self.player.get_by_name("file-source").set_property(
            "location", file_name)

        # Initialize the player
        self.player.set_state(Gst.State.PAUSED)
        # Since all gstreamer calls are asynchronous, video information will
        # not be immediately available so we retry to determine it every 0.1
        # seconds until we succeed.
        self.info = {}
        while True:
            query_success, duration = self.player.query_duration(
                    Gst.Format(Gst.Format.TIME))

            if not query_success:
                time.sleep(0.1)
                continue

            self.info["duration"] = duration
            break

        # Determine other stream information. Some of the information will get
        # added through Gst.MESSAGE_TAG messages sent by gstreamer.
        video_info_conv = {
            "width": ("width", int),
            "height": ("height", int),
            "framerate": ("video_framerate", float),
            "interlaced": ("video_interlaced", bool),
        }
        audio_info_conv = {
            "channels": ("audio_channels", int),
            "rate": ("audio_rate", float),
        }
        decoder = self.player.get_by_name("decoder")
        for pad in decoder.srcpads:
            caps = pad.get_current_caps()
            cap_count = caps.get_size()
            for index in range(cap_count):
                cap = caps.get_structure(index)
                for field_index in range(cap.n_fields()):
                    name = cap.nth_field_name(field_index)
                    if cap.get_name().startswith("video"):
                        if name in video_info_conv:
                            value = cap.get_value(name)
                            self.info[video_info_conv[name][0]] = \
                                video_info_conv[name][1](value)
                    elif cap.get_name().startswith("audio"):
                        if name in audio_info_conv:
                            value = cap.get_value(name)
                            self.info[audio_info_conv[name][0]] = \
                                audio_info_conv[name][1](value)
        return self.info

    def unload_file(self):
        self.player.set_state(Gst.State.NULL)
        self.file_name = None

    def capture_frame(self, capture_time, destination=None):
        self.player.seek_simple(
                Gst.Format(Gst.Format.TIME),
                Gst.SeekFlags.FLUSH,
                capture_time)
        self.player.set_state(Gst.State.PLAYING)

        # Wait for frame to be captured
        self._capture = {"done": False}
        while not self._capture["done"]:
            time.sleep(0.1)

        if destination is not None:
            shutil.move(self._capture["file_name"], destination)
        else:
            os.remove(self._capture["file_name"])
        return self._capture["capture_time"]


__all__ = ["GStreamerBackend"]
