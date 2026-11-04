# audio_manager.py - Audio recording, playback, and analysis for Notebook
# Compatible with xnotes-android AudioItem and .xnote format specifications.

import os
import time
import base64
import hashlib
import tempfile
import struct
import wave
import math

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstPbutils', '1.0')
from gi.repository import Gst, GstPbutils, GLib

Gst.init(None)


class AudioManager:
    """Manages optimized audio recording, playback, and waveform extraction for Notebook."""

    _instance = None

    @classmethod
    def get_default(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._record_pipeline = None
        self._record_path = None
        self._record_start_time = 0.0
        self._record_timer_id = None
        self._record_tick_cb = None
        self._is_recording = False
        self._record_bus = None
        self._live_peaks = []
        self._active_rid = 1

        self._player = None
        self._play_bus = None
        self._play_timer_id = None
        self._active_audio_item = None
        self._active_doc = None
        self._play_tick_cb = None
        self._play_finished_cb = None
        self._cached_audio_files = {}

    # =========================================================================
    # Audio Recording (Optimized with real-time level analysis)
    # =========================================================================
    @property
    def is_recording(self):
        return self._is_recording

    def start_recording(self, on_tick=None):
        """Start microphone audio recording with real-time amplitude capture."""
        if self._is_recording:
            return False

        # Stop playback if currently active
        self.stop_playback()

        fd, temp_path = tempfile.mkstemp(suffix=".m4a", prefix="xnote_rec_")
        os.close(fd)

        # Pipeline with level element for real-time waveform capture (50ms interval)
        pipeline = None
        # Attempt 1: AAC / MP4 with level
        try:
            pipe_str = (
                f"autoaudiosrc ! audioconvert ! audioresample ! "
                f"level name=rec_level interval=50000000 ! "
                f"avenc_aac ! mp4mux ! filesink location=\"{temp_path}\""
            )
            pipeline = Gst.parse_launch(pipe_str)
        except Exception:
            pass

        # Fallback 1: opus / ogg
        if not pipeline:
            temp_path = temp_path.replace(".m4a", ".ogg")
            try:
                pipe_str = (
                    f"autoaudiosrc ! audioconvert ! audioresample ! "
                    f"level name=rec_level interval=50000000 ! "
                    f"opusenc ! oggmux ! filesink location=\"{temp_path}\""
                )
                pipeline = Gst.parse_launch(pipe_str)
            except Exception:
                pass

        # Fallback 2: wav
        if not pipeline:
            temp_path = temp_path.replace(".ogg", ".wav")
            try:
                pipe_str = (
                    f"autoaudiosrc ! audioconvert ! audioresample ! "
                    f"level name=rec_level interval=50000000 ! "
                    f"wavenc ! filesink location=\"{temp_path}\""
                )
                pipeline = Gst.parse_launch(pipe_str)
            except Exception:
                pass

        # Fallback 3: basic wav without level
        if not pipeline:
            try:
                pipe_str = f"autoaudiosrc ! audioconvert ! audioresample ! wavenc ! filesink location=\"{temp_path}\""
                pipeline = Gst.parse_launch(pipe_str)
            except Exception:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                return False

        self._record_pipeline = pipeline
        self._live_peaks = []

        # Connect bus signal watch for level messages
        self._record_bus = self._record_pipeline.get_bus()
        self._record_bus.add_signal_watch()
        self._record_bus.connect("message::element", self._on_rec_level_message)

        ret = self._record_pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._cleanup_record_pipeline()
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return False

        self._record_path = temp_path
        self._record_start_time = time.time()
        self._record_tick_cb = on_tick
        self._is_recording = True

        def _on_rec_tick():
            if not self._is_recording:
                return False
            elapsed_ms = int((time.time() - self._record_start_time) * 1000)
            if self._record_tick_cb:
                self._record_tick_cb(elapsed_ms)
            return True

        self._record_timer_id = GLib.timeout_add(100, _on_rec_tick)
        return True

    def _on_rec_level_message(self, bus, msg):
        st = msg.get_structure()
        if st and st.get_name() == "level" and st.has_field("peak"):
            peaks = st.get_value("peak")
            if peaks:
                peak_db = peaks[0]
                if peak_db < -55.0:
                    amp = 18
                else:
                    # Convert dB (-55..0) to linear amplitude (0..255)
                    val = 10.0 ** (peak_db / 20.0)
                    amp = min(255, max(18, int(val * 255.0)))
                self._live_peaks.append(amp)

    def _cleanup_record_pipeline(self):
        if self._record_bus:
            try:
                self._record_bus.remove_signal_watch()
            except Exception:
                pass
            self._record_bus = None

        if self._record_pipeline:
            try:
                self._record_pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
            self._record_pipeline = None

    def stop_recording(self):
        """Stop microphone recording, finalize audio file, and return recording metadata."""
        if not self._is_recording or not self._record_pipeline:
            return None

        if self._record_timer_id:
            GLib.source_remove(self._record_timer_id)
            self._record_timer_id = None

        self._is_recording = False
        rec_path = self._record_path
        elapsed_ms = max(200, int((time.time() - self._record_start_time) * 1000))

        # Send EOS to cleanly finalize mp4/m4a/ogg headers
        try:
            self._record_pipeline.send_event(Gst.Event.new_eos())
            bus = self._record_pipeline.get_bus()
            bus.timed_pop_filtered(500 * Gst.MSECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        except Exception:
            pass

        self._cleanup_record_pipeline()
        self._record_path = None

        if not rec_path or not os.path.exists(rec_path) or os.path.getsize(rec_path) == 0:
            return None

        duration_ms = self._detect_duration_ms(rec_path, fallback_ms=elapsed_ms)

        # Build peaks directly from real-time live levels (zero delay!)
        peaks_bytes = self._bin_live_peaks(self._live_peaks, num_bins=64)
        if not peaks_bytes:
            peaks_bytes = self.extract_waveform_peaks(rec_path, num_peaks=64)

        with open(rec_path, "rb") as f:
            data = f.read()

        try:
            os.unlink(rec_path)
        except OSError:
            pass

        ext = os.path.splitext(rec_path)[1].lower()
        if not ext:
            ext = ".m4a"

        return {
            "data": data,
            "ext": ext,
            "duration_ms": duration_ms,
            "peaks": base64.b64encode(peaks_bytes).decode("ascii") if peaks_bytes else ""
        }

    def _bin_live_peaks(self, live_peaks, num_bins=64):
        """Compress live captured levels into standard num_bins waveform bytes."""
        if not live_peaks:
            return None
        n = len(live_peaks)
        if n == 0:
            return None

        out = bytearray()
        chunk_size = max(1, n / float(num_bins))
        for i in range(num_bins):
            start = int(i * chunk_size)
            end = max(start + 1, int((i + 1) * chunk_size))
            chunk = live_peaks[start:min(n, end)]
            if chunk:
                # Use peak (max) within bin for dynamic waveform visibility
                out.append(max(chunk))
            else:
                out.append(20)
        return bytes(out)

    def cancel_recording(self):
        """Cancel and discard the active recording."""
        if not self._is_recording:
            return
        if self._record_timer_id:
            GLib.source_remove(self._record_timer_id)
            self._record_timer_id = None
        self._is_recording = False
        self._cleanup_record_pipeline()
        if self._record_path and os.path.exists(self._record_path):
            try:
                os.unlink(self._record_path)
            except OSError:
                pass
        self._record_path = None
        self._live_peaks.clear()

    # =========================================================================
    # Audio Playback
    # =========================================================================
    def is_playing_item(self, audio_item):
        return self._active_audio_item is audio_item and audio_item.get("playing", False)

    def get_active_playhead(self):
        """Returns (rid, playhead_ms) for ink fading if audio playback is currently active."""
        if self._active_audio_item is not None:
            return self._active_audio_item.get("rid"), self._active_audio_item.get("playhead", -1)
        return None, -1

    def toggle_play(self, audio_item, doc, on_tick=None, on_finished=None):
        """Toggle play/pause on an AudioItem."""
        if self.is_playing_item(audio_item):
            self.pause_playback(audio_item)
            return False
        else:
            return self.play_item(audio_item, doc, on_tick=on_tick, on_finished=on_finished)

    def play_item(self, audio_item, doc, on_tick=None, on_finished=None):
        """Start or resume playback of an AudioItem."""
        if self._active_audio_item and self._active_audio_item is not audio_item:
            self.stop_playback()

        file_path = self._ensure_audio_file_on_disk(audio_item, doc)
        if not file_path or not os.path.exists(file_path):
            return False

        if self._player is None or self._active_audio_item is not audio_item:
            if self._player is not None:
                self._player.set_state(Gst.State.NULL)

            self._player = Gst.ElementFactory.make("playbin", "audio_player")
            if not self._player:
                return False

            uri = Gst.filename_to_uri(file_path)
            self._player.set_property("uri", uri)
            self._play_bus = self._player.get_bus()
            self._play_bus.add_signal_watch()
            self._play_bus.connect("message", self._on_bus_message)

        self._active_audio_item = audio_item
        self._active_doc = doc
        self._play_tick_cb = on_tick
        self._play_finished_cb = on_finished

        playhead = audio_item.get("playhead", -1)
        duration_ms = audio_item.get("duration_ms", 0)
        if playhead >= duration_ms and duration_ms > 0:
            playhead = 0
            audio_item["playhead"] = 0

        self._player.set_state(Gst.State.PLAYING)
        audio_item["playing"] = True

        if playhead > 0:
            target_ns = int(playhead * Gst.MSECOND)
            self._player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, target_ns)

        if self._play_timer_id is None:
            self._play_timer_id = GLib.timeout_add(50, self._on_play_tick)

        if self._play_tick_cb:
            self._play_tick_cb(audio_item)
        return True

    def pause_playback(self, audio_item=None):
        """Pause audio playback."""
        if audio_item is None:
            audio_item = self._active_audio_item

        if self._player and audio_item and self._active_audio_item is audio_item:
            self._player.set_state(Gst.State.PAUSED)
            audio_item["playing"] = False
            if self._play_timer_id:
                GLib.source_remove(self._play_timer_id)
                self._play_timer_id = None
            if self._play_tick_cb:
                self._play_tick_cb(audio_item)

    def seek_playback(self, audio_item, target_ms, doc=None):
        """Seek audio playback to target_ms."""
        target_ms = max(0, min(target_ms, audio_item.get("duration_ms", 0)))
        audio_item["playhead"] = target_ms

        if self._active_audio_item is audio_item and self._player is not None:
            target_ns = int(target_ms * Gst.MSECOND)
            self._player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, target_ns)

        if self._play_tick_cb:
            self._play_tick_cb(audio_item)

    def stop_playback(self):
        """Stop audio playback and reset player."""
        if self._play_timer_id:
            GLib.source_remove(self._play_timer_id)
            self._play_timer_id = None

        if self._active_audio_item:
            self._active_audio_item["playing"] = False
            self._active_audio_item["playhead"] = -1
            if self._play_tick_cb:
                self._play_tick_cb(self._active_audio_item)

        if self._player:
            self._player.set_state(Gst.State.NULL)
            self._player = None

        self._active_audio_item = None
        self._active_doc = None
        self._play_tick_cb = None
        self._play_finished_cb = None

    def _on_play_tick(self):
        if not self._active_audio_item or not self._player:
            return False

        success, pos = self._player.query_position(Gst.Format.TIME)
        if success:
            pos_ms = int(pos / Gst.MSECOND)
            self._active_audio_item["playhead"] = pos_ms
            duration_ms = self._active_audio_item.get("duration_ms", 0)
            if duration_ms > 0 and pos_ms >= duration_ms:
                self._active_audio_item["playing"] = False
                self._active_audio_item["playhead"] = duration_ms
                if self._play_finished_cb:
                    self._play_finished_cb(self._active_audio_item)
                return False

            if self._play_tick_cb:
                self._play_tick_cb(self._active_audio_item)
        return True

    def _on_bus_message(self, bus, msg):
        if msg.type == Gst.MessageType.EOS:
            if self._active_audio_item:
                self._active_audio_item["playing"] = False
                self._active_audio_item["playhead"] = 0
                cb = self._play_finished_cb
                item = self._active_audio_item
                self.stop_playback()
                if cb:
                    cb(item)
        elif msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            self.stop_playback()

    # =========================================================================
    # Helpers: File Extraction & Waveform Analysis
    # =========================================================================
    def _ensure_audio_file_on_disk(self, audio_item, doc):
        asset_name = audio_item.get("asset")
        if not asset_name:
            return None

        if asset_name in self._cached_audio_files:
            cached_path = self._cached_audio_files[asset_name]
            if os.path.exists(cached_path):
                return cached_path

        raw_assets = doc.get("_raw_assets", {}) if doc else {}
        raw_bytes = raw_assets.get(asset_name) or raw_assets.get(os.path.basename(asset_name))
        if not raw_bytes:
            return None

        ext = os.path.splitext(asset_name)[1].lower() or ".m4a"
        fd, temp_path = tempfile.mkstemp(suffix=ext, prefix="xnote_audio_")
        os.write(fd, raw_bytes)
        os.close(fd)

        self._cached_audio_files[asset_name] = temp_path
        return temp_path

    def _detect_duration_ms(self, file_path, fallback_ms=0):
        try:
            disc = GstPbutils.Discoverer.new(2 * Gst.SECOND)
            uri = Gst.filename_to_uri(file_path)
            info = disc.discover_uri(uri)
            duration_ns = info.get_duration()
            if duration_ns > 0:
                return int(duration_ns / Gst.MSECOND)
        except Exception:
            pass

        if file_path.lower().endswith(".wav"):
            try:
                with wave.open(file_path, "rb") as wf:
                    frames = wf.getnframes()
                    rate = wf.getframerate()
                    if rate > 0:
                        return int((frames / float(rate)) * 1000)
            except Exception:
                pass

        return fallback_ms

    def extract_waveform_peaks(self, file_path, num_peaks=64):
        """Extract a byte sequence (0..255) representing waveform amplitude peaks."""
        if file_path.lower().endswith(".wav"):
            try:
                with wave.open(file_path, "rb") as wf:
                    nchannels = wf.getnchannels()
                    sampwidth = wf.getsampwidth()
                    nframes = wf.getnframes()
                    if nframes > 0:
                        frames = wf.readframes(min(nframes, 48000 * 30))
                        chunk_size = max(1, len(frames) // (num_peaks * sampwidth * nchannels))
                        fmt = "<h" if sampwidth == 2 else "<b"
                        step = sampwidth * nchannels
                        peaks = bytearray()
                        for i in range(num_peaks):
                            chunk_start = i * chunk_size * step
                            chunk_end = min(len(frames), (i + 1) * chunk_size * step)
                            max_val = 0
                            for j in range(chunk_start, chunk_end, step * 4):
                                if j + sampwidth <= len(frames):
                                    v = abs(struct.unpack_from(fmt, frames, j)[0])
                                    if v > max_val:
                                        max_val = v
                            norm = int(min(255, (max_val / (32768.0 if sampwidth == 2 else 128.0)) * 255.0))
                            peaks.append(max(15, norm))
                        return bytes(peaks)
            except Exception:
                pass

        # Deterministic waveform fallback
        seed = int(hashlib.md5(file_path.encode("utf-8")).hexdigest()[:6], 16)
        peaks = bytearray()
        for i in range(num_peaks):
            val = int(70 + 55 * math.sin(i * 0.35 + seed % 7) + 35 * math.cos(i * 0.7 + seed % 13))
            peaks.append(max(20, min(230, val)))
        return bytes(peaks)

    # =========================================================================
    # Audio Attachment File Import
    # =========================================================================
    def create_audio_item_from_file(self, file_path, doc, center_x=300.0, center_y=300.0):
        """Import an existing audio file, store it in doc raw assets, and return a new AudioItem dict."""
        if not os.path.exists(file_path):
            return None

        with open(file_path, "rb") as f:
            data = f.read()

        ext = os.path.splitext(file_path)[1].lower() or ".m4a"
        digest = hashlib.sha1(data).hexdigest()[:16]
        asset_name = f"assets/audio-{digest}{ext}"

        doc.setdefault("_raw_assets", {})[asset_name] = data
        duration_ms = self._detect_duration_ms(file_path, fallback_ms=30000)
        peaks = self.extract_waveform_peaks(file_path)
        peaks_b64 = base64.b64encode(peaks).decode("ascii") if peaks else ""

        # Determine next rid (recording id)
        all_items = []
        for p in doc.get("pages", []):
            all_items.extend(p.get("items", []))
        all_items.extend(doc.get("items", []))
        rids = [it.get("rid", 0) for it in all_items if it.get("kind") == "audio"]
        next_rid = (max(rids) + 1) if rids else 1

        name = os.path.splitext(os.path.basename(file_path))[0]
        pill_w = 230.0
        pill_h = 48.0
        rect = [round(center_x - pill_w / 2.0, 1), round(center_y - pill_h / 2.0, 1), pill_w, pill_h]

        return {
            "kind": "audio",
            "asset": asset_name,
            "rect": rect,
            "name": name,
            "rid": next_rid,
            "duration_ms": duration_ms,
            "peaks": peaks_b64,
            "playhead": -1,
            "playing": False
        }
