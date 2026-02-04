import sys
import gi
import platform
import os
import argparse
import time
import math
# GStreamer kutuphanelerini yukle
gi.require_version('Gst', '1.0')
gi.require_version('GstRtspServer', '1.0')
from gi.repository import GLib, Gst, GstRtspServer
os.environ["GST_DEBUG_DUMP_DOT_DIR"] = os.getcwd()

from probes import osd_sink_pad_buffer_probe

from common.platform_info import PlatformInfo
from common.FPS import PERF_DATA
from common.utils import create_dynamic_labels

# Sabitler
MUXER_OUTPUT_WIDTH = 1920
MUXER_OUTPUT_HEIGHT = 1080
MUXER_BATCH_TIMEOUT_USEC = 4000000
TILED_OUTPUT_WIDTH = 1920
TILED_OUTPUT_HEIGHT = 1080
IS_TEGRA = platform.machine() == 'aarch64'
pgie_conf_file="/apps/deepstream-yolo-e2e/config/pgie/config_pgie_yolo_seg.txt"


def bus_call(bus, message, loop):
    t = message.type
    if t == Gst.MessageType.EOS:
        sys.stdout.write("End-of-stream\n")
        loop.quit()
    elif t==Gst.MessageType.WARNING:
        err, debug = message.parse_warning()
        sys.stderr.write("Warning: %s: %s\n" % (err, debug))
    elif t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        sys.stderr.write("Error: %s: %s\n" % (err, debug))
        loop.quit()
    return True


def cb_newpad(decodebin, decoder_src_pad, data):
    print("In cb_newpad\n")
    caps = decoder_src_pad.get_current_caps()
    if not caps:
        caps = decoder_src_pad.query_caps()
    gststruct = caps.get_structure(0)
    gstname = gststruct.get_name()
    source_bin = data
    features = caps.get_features(0)

    # Need to check if the pad created by the decodebin is for video and not
    # audio.
    print("gstname=", gstname)
    if (gstname.find("video") != -1):
        print("features=", features)
        if features.contains("memory:NVMM"):
            # Get the source bin ghost pad
            bin_ghost_pad = source_bin.get_static_pad("src")
            if not bin_ghost_pad.set_target(decoder_src_pad):
                sys.stderr.write("Failed to link decoder src pad to source bin ghost pad\n")
        else:
            sys.stderr.write(" Error: Decodebin did not pick nvidia decoder plugin.\n")


def decodebin_child_added(child_proxy, Object, name, user_data):
    platform_info = PlatformInfo()
    print("Decodebin child added:", name, "\n")
    if name.find("decodebin") != -1:
        Object.connect("child-added", decodebin_child_added, user_data)

    if (name.find("nvv4l2decoder") != -1):
        if (platform_info.is_integrated_gpu()):
            Object.set_property("enable-max-performance", True)
            Object.set_property("drop-frame-interval", 0)
            Object.set_property("num-extra-surfaces", 0)

    if "source" in name:
        source_element = child_proxy.get_by_name("source")
        if source_element.find_property('drop-on-latency') != None:
            Object.set_property("drop-on-latency", True)


def create_source_bin(index, uri):
    print("Creating source bin")

    bin_name = "source-bin-%02d" % index
    print(bin_name)
    nbin = Gst.Bin.new(bin_name)
    if not nbin:
        sys.stderr.write(" Unable to create source bin \n")

    uri_decode_bin = Gst.ElementFactory.make("uridecodebin", "uri-decode-bin")

    if not uri_decode_bin:
        sys.stderr.write(" Unable to create uri decode bin \n")

    uri_decode_bin.set_property("uri", uri)
    uri_decode_bin.connect("pad-added", cb_newpad, nbin)
    uri_decode_bin.connect("child-added", decodebin_child_added, nbin)

    Gst.Bin.add(nbin, uri_decode_bin)
    bin_pad = nbin.add_pad(Gst.GhostPad.new_no_target("src", Gst.PadDirection.SRC))
    if not bin_pad:
        sys.stderr.write(" Failed to add ghost pad in source bin \n")
        return None
    return nbin




def main(args):

    # Ayarlar
    sources = args.source
    number_sources = len(sources)
    batch_size = args.batch_size if args.batch_size > 0 else number_sources

    # GStreamer Başlat
    Gst.init(None)
    pipeline = Gst.Pipeline.new("deepstream-linear-pipeline")
    loop = GLib.MainLoop()

    # 1. Stream Muxer (Kaynak birlestirici)
    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    streammux.set_property('width', args.mux_width)
    streammux.set_property('height', args.mux_height)
    streammux.set_property('batch-size', batch_size)
    streammux.set_property('batched-push-timeout', MUXER_BATCH_TIMEOUT_USEC)
    streammux.set_property('live-source', 1)
    pipeline.add(streammux)

    # Kaynaklari olustur ve Muxer'a bagla
    for i in range(number_sources):
        uri_name = sources[i]
        source_bin = create_source_bin(i, uri_name)
        pipeline.add(source_bin)

        padname = "sink_%u" % i
        sinkpad = streammux.request_pad_simple(padname)
        srcpad = source_bin.get_static_pad("src")
        srcpad.link(sinkpad)


    # 2. Inference (PGIE) - Model
    pgie = Gst.ElementFactory.make("nvinfer", "primary-inference")
    pgie.set_property('config-file-path', pgie_conf_file)
    pgie.set_property("batch-size", batch_size)
    pgie.set_property('output-tensor-meta', True)
    pipeline.add(pgie)

    # 3. Tiler (Izgara gorunumu) - Bu demux/mux dongusu yerine cok daha kararlidir
    tiler = Gst.ElementFactory.make("nvmultistreamtiler", "nvtiler")
    tiler.set_property("rows", int(math.sqrt(number_sources)))
    tiler.set_property("columns", int(math.ceil((1.0 * number_sources) / int(math.sqrt(number_sources)))))
    tiler.set_property("width", TILED_OUTPUT_WIDTH)
    tiler.set_property("height", TILED_OUTPUT_HEIGHT)
    pipeline.add(tiler)

    # Convert
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "nvvidconv")
    pipeline.add(nvvidconv)

    # TEK OSD (GLOBAL)
    osd = Gst.ElementFactory.make("nvdsosd", "global_osd")
    osd.set_property('display-mask', True)
    pipeline.add(osd)

    # OSD sonrası queue
    queue_post_osd = Gst.ElementFactory.make("queue", "queue_post_osd")
    queue_post_osd.set_property("max-size-buffers", 1)
    queue_post_osd.set_property("leaky", 2)
    pipeline.add(queue_post_osd)

    # Global Tee
    tee_global = Gst.ElementFactory.make("tee", "global_tee")
    pipeline.add(tee_global)

    streammux.link(pgie)
    pgie.link(tiler)
    tiler.link(nvvidconv)
    nvvidconv.link(osd)
    osd.link(queue_post_osd)
    queue_post_osd.link(tee_global)

    dynamic_labels = create_dynamic_labels(pgie_conf_file)

    """
    # Probe Ekleme (PGIE Cikisina)
    pgie_src_pad = pgie.get_static_pad("src")
    if pgie_src_pad:
        pgie_src_pad.add_probe(Gst.PadProbeType.BUFFER, pgie_src_pad_buffer_probe, None)
    """

    osd_sink_pad = osd.get_static_pad("sink")
    if not osd_sink_pad:
        sys.stdout.write("Unable to create sink pad\n")
    else:
        osd_sink_pad.add_probe(Gst.PadProbeType.BUFFER, osd_sink_pad_buffer_probe, None, dynamic_labels, number_sources)

    # --- EKRAN CIKISI (DISPLAY SINK) ---
    queue_display = Gst.ElementFactory.make("queue", "queue_display")
    pipeline.add(queue_display)

    # Format Zorlayici (Siyah ekrani onlemek icin RGBA zorluyoruz)
    caps_filter = Gst.ElementFactory.make("capsfilter", "display_caps")
    caps = Gst.Caps.from_string("video/x-raw(memory:NVMM), format=RGBA")
    caps_filter.set_property("caps", caps)
    pipeline.add(caps_filter)

    # Sink Secimi
    sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")
    sink.set_property("sync", False)  # Canli yayin oldugu icin False
    sink.set_property("qos", False)
    pipeline.add(sink)

    # Ekran Baglantilari
    # Tee -> Queue -> Caps -> (Transform if Tegra) -> Sink
    tee_src_pad = tee_global.request_pad_simple("src_%u")
    queue_sink_pad = queue_display.get_static_pad("sink")
    tee_src_pad.link(queue_sink_pad)

    if IS_TEGRA:
        print("Platform: Jetson. nvegltransform ekleniyor.")
        transform = Gst.ElementFactory.make("nvegltransform", "nvegl-transform")
        pipeline.add(transform)
        queue_display.link(caps_filter)
        caps_filter.link(transform)
        transform.link(sink)
    else:
        print("Platform: dGPU. nvegltransform gerekmez.")
        queue_display.link(caps_filter)
        caps_filter.link(sink)

    # --- BUS HANDLER ---
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", bus_call, loop)

    # --- CALISTIRMA ---
    sys.stdout.write(f"Now playing: {sources}\n")
    pipeline.set_state(Gst.State.PLAYING)


    try:
        # Pipeline'in tam oturmasi icin 1 saniye nefes aldiriyoruz
        time.sleep(1)

        Gst.debug_bin_to_dot_file(
            pipeline,
            Gst.DebugGraphDetails.ALL,
            "pipeline_graph"
        )
        print(f"GRAFIK KAYDEDILDI: {os.getcwd()}/pipeline_graph.dot")
    except Exception as e:
        print(f"Grafik hatasi: {e}")

    sys.stdout.write("Running...\n")
    try:
        loop.run()
    except Exception as e:
        print(e)
    except KeyboardInterrupt:
        pass

    sys.stdout.write("Exiting...\n")
    pipeline.set_state(Gst.State.NULL)
    return 0



def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", required=True, help="RTSP URI or File path")
    parser.add_argument("--batch-size", type=int, default=0)
    parser.add_argument("--mux-width", type=int, default=1920)
    parser.add_argument("--mux-height", type=int, default=1080)
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    sys.exit(main(args))