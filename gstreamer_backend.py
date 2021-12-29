import os
import shutil

from base_backend import BaseBackend


class GStreamerBackend(BaseBackend):
    def __init__(self, options, tmp_dir):
        self.options = options
        self.tmp_dir = tmp_dir
        self.frame_capture_padding = 0.5 * 1000000000

        # Import python gstreamer libraries
        global pygst, gst, gobject, threading, time
        import pygst
        pygst.require("0.10")
        import gst
        import gobject
        import threading
        import time

        # Create gstreamer player object
        self.player = gst.Pipeline("player")

        # File source and universal decoder
        filesrc = gst.element_factory_make("filesrc", "file-source")
        decoder = gst.element_factory_make("decodebin", "decoder")
        decoder.connect("new-decoded-pad", self.decoder_callback)

        # PNG encoder, Multiple File sink and colorspace converter required by
        # PNG encoder
        colorspace = gst.element_factory_make("ffmpegcolorspace", "video-sink")
        pngenc = gst.element_factory_make("pngenc", "png-encoder")
        pngenc.set_property("snapshot", True)
        multifilesink = gst.element_factory_make(
                "multifilesink",
                "multi-file-sink")
        multifilesink.set_property(
                "location",
                os.path.join(self.tmp_dir, "output-%05d.png"))
        multifilesink.set_property("post-messages", True)

        # Add elements to player pipeline
        self.player.add(filesrc, decoder, colorspace, pngenc, multifilesink)
        gst.element_link_many(filesrc, decoder)
        gst.element_link_many(colorspace, pngenc, multifilesink)

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)

        # Initialize gobject MainLoop in a new thread
        gobject.threads_init()
        self.main_loop = gobject.MainLoop()

        def main_loop_thread():
            self.main_loop.run()
        self.main_loop_thread = threading.Thread(target=main_loop_thread)
        self.main_loop_thread.daemon = True
        self.main_loop_thread.start()

    # Decoder callback, used to link video pad to sink
    def decoder_callback(self, decoder, pad, data):
        structure_name = pad.get_caps()[0].get_name()
        if structure_name.startswith("video"):
            video_pad = self.player.get_by_name("video-sink").get_pad("sink")
            pad.link(video_pad)

    # Handle gstreamer messages
    def on_message(self, bug, message):
        if message.type == gst.MESSAGE_TAG:
            tag_conv = {
                gst.TAG_VIDEO_CODEC: "video_codec",
                gst.TAG_AUDIO_CODEC: "audio_codec",
                gst.TAG_TITLE: "title",
                gst.TAG_BITRATE: "audio_bitrate",
            }
            for id in range(message.structure.n_fields()):
                name = message.structure.nth_field_name(id)
                if name in tag_conv:
                    self.info[tag_conv[name]] = message.structure[name]
        # A frame has been captured
        if message.type == gst.MESSAGE_ELEMENT and \
           message.src.get_property("name") == "multi-file-sink":
            self._capture["capture_time"] = message.structure["timestamp"]
            self._capture["file_name"] = message.structure["filename"]
            self._capture["done"] = True

    # Converts nanoseconds into seconds.
    def capture_time_to_seconds(self, time):
        return time / 1000000000.0

    # Initialize gstreamer player with the given file
    def load_file(self, file_name):
        self.file_name = file_name
        self.player.get_by_name("file-source").set_property(
            "location", file_name)

        # Initialize the player
        self.player.set_state(gst.STATE_PAUSED)
        # Since all gstreamer calls are asynchronous, video information will
        # not be immediately available so we retry to determine it every 0.1
        # seconds until we succeed.
        self.info = {}
        while True:
            try:
                self.info["duration"] = self.player.query_duration(
                    gst.Format(gst.FORMAT_TIME), None)[0]
                time.sleep(0.1)
                break
            except gst.QueryError:
                pass

        # Determine other stream information. Some of the information will get
        # added through gst.MESSAGE_TAG messages sent by gstreamer.
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
        for i in self.player.get_by_name("decoder").src_pads():
            caps = i.get_caps()[0]
            for id in range(caps.n_fields()):
                name = caps.nth_field_name(id)
                if caps.get_name().startswith("video"):
                    if name in video_info_conv:
                        self.info[video_info_conv[name][0]] = \
                            video_info_conv[name][1](caps[name])
                elif caps.get_name().startswith("audio"):
                    if name in audio_info_conv:
                        self.info[audio_info_conv[name][0]] = \
                            audio_info_conv[name][1](caps[name])
        return self.info

    def unload_file(self):
        self.player.set_state(gst.STATE_NULL)
        self.file_name = None

    def capture_frame(self, capture_time, destination=None):
        self.player.seek_simple(
                gst.Format(gst.FORMAT_TIME),
                gst.SEEK_FLAG_FLUSH,
                capture_time)
        self.player.set_state(gst.STATE_PLAYING)

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
