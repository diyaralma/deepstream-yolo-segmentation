import pyds
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst
from collections import defaultdict

object_trackers = defaultdict(lambda: defaultdict(list))
last_seen = defaultdict(lambda: defaultdict(int))

FRAME_EXPIRATION_LIMIT = 60


def purge_old_objects(current_frame_num):
    for pad_index, objects in list(object_trackers.items()):
        for object_id in list(objects.keys()):
            if current_frame_num - last_seen[pad_index][object_id] > FRAME_EXPIRATION_LIMIT:
                del object_trackers[pad_index][object_id]
                del last_seen[pad_index][object_id]
            else:
                if len(object_trackers[pad_index][object_id]) > 25:
                    object_trackers[pad_index][object_id] = object_trackers[pad_index][object_id][-25:]


# Function for probe to extract metadata
def sink_pad_buffer_probe(pad, info, u_data, perf_data):
    frame_number = 0
    num_rects = 0

    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Unable to get GstBuffer")
        return Gst.PadProbeReturn.OK

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list
    while l_frame is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        l_obj = frame_meta.obj_meta_list
        while l_obj is not None:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
            except StopIteration:
                break

            # process metadata

            try:
                l_obj = l_obj.next
            except StopIteration:
                break

        stream_index = "stream{0}".format(frame_meta.pad_index)
        perf_data.update_fps(stream_index)

        try:
            l_frame = l_frame.next
        except StopIteration:
            break

    return Gst.PadProbeReturn.OK


def osd_sink_pad_buffer_probe(pad, info, u_data, dynamic_labels, number_sources):
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Unable to get GstBuffer ")
        return Gst.PadProbeReturn.OK

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list

    while l_frame is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        pad_index = frame_meta.pad_index
        frame_number = frame_meta.frame_num
        l_obj = frame_meta.obj_meta_list

        display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)

        while l_obj is not None:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
            except StopIteration:
                break

            object_id = obj_meta.object_id
            class_id = obj_meta.class_id

            # Renk ayari
            color = dynamic_labels.get(class_id)

            # BOUNDING BOX GIZLEME
            obj_rect = obj_meta.rect_params
            obj_rect.border_width = 0  # Kenarlik genisligini 0 yaparak kutuyu gizliyoruz

            # Arka plan rengini tamamen seffaf yapiyoruz
            obj_rect.has_bg_color = 0

            # KOORDINATLARI DETECTOR'DAN ALMA
            # pipeline'da tracker olmadigi icin tracker_bbox_info bos doner.
            # Bu yuzden rect_params kullaniyoruz.
            obj_coords_x = int(obj_rect.left)
            obj_coords_y = int(obj_rect.top)
            obj_coords_w = int(obj_rect.width)
            obj_coords_h = int(obj_rect.height)

            # Merkez Nokta Hesabi
            center_x = obj_coords_x + (obj_coords_w // 2)
            center_y = obj_coords_y + (obj_coords_h // 2)

            # Izi surmek icin alt orta nokta (Trail icin)
            bottom_center_x = int(obj_coords_x + obj_coords_w / 2)
            bottom_center_y = int(obj_coords_y + obj_coords_h)

            # Trail (Iz) Mantigi (Tracker olmadigi icin object_id surekli degisebilir,
            # ama kodun yapisini bozmamak icin birakiyorum)
            object_trackers[pad_index][object_id].append((bottom_center_x, bottom_center_y))
            last_seen[pad_index][object_id] = frame_number

            if len(object_trackers[pad_index][object_id]) > 20:
                object_trackers[pad_index][object_id].pop(0)

            # YAZIYI ORTALAMA
            # Yaziyi tam merkeze koyuyoruz.
            text_x = center_x - 20
            text_y = center_y - 10

            # Ekrandan tasmayi onle
            if text_x < 1: text_x = 1
            if text_y < 1: text_y = 1

            # Font buyuklugu hesaplama
            min_fsize = 8
            max_fsize = 12
            if number_sources > 1:
                max_fsize = max(min_fsize, max_fsize - (number_sources - 1))

            # Font boyutu nesne boyutuna gore dinamik
            font_size = min_fsize + (max_fsize - min_fsize) * (obj_coords_h / 100)
            font_size = max(min_fsize, min(max_fsize, int(font_size)))

            # Yazi Ayarlari
            text_params = obj_meta.text_params
            text_params.display_text = f"{pyds.get_string(text_params.display_text).capitalize()}"
            text_params.x_offset = int(text_x)
            text_params.y_offset = int(text_y)

            # Yazi Stili
            text_params.font_params.font_name = "Serif"
            text_params.font_params.font_size = int(font_size)
            text_params.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)  # Beyaz Yazi
            text_params.set_bg_clr = 1
            # Arka plani class rengi ile yari seffaf yapiyoruz
            text_params.text_bg_clr.set(color.red, color.green, color.blue, 0.6)

            # Trail (Kuyruk) Cizimi
            trail = object_trackers[pad_index][object_id]
            for i in range(len(trail) - 1):
                x1, y1 = trail[i]
                if x1 > 0 and y1 > 0:
                    if i % 16 == 0:
                        pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)
                        display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)

                    display_meta.num_circles += 1
                    # Circle params indexi dogru yonetilmeli, basitlik icin sona ekliyoruz
                    circle_idx = display_meta.num_circles - 1

                    if circle_idx < 16:  # Max 16 daire cizilebilir bir meta paketinde
                        py_nvosd_circle_params = display_meta.circle_params[circle_idx]
                        py_nvosd_circle_params.circle_color.set(color.red, color.green, color.blue, 0.9)
                        py_nvosd_circle_params.radius = 3
                        py_nvosd_circle_params.xc = x1
                        py_nvosd_circle_params.yc = y1

            try:
                l_obj = l_obj.next
            except StopIteration:
                break

        pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)
        purge_old_objects(frame_number)

        try:
            l_frame = l_frame.next
        except StopIteration:
            break

    return Gst.PadProbeReturn.OK
