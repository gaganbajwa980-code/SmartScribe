import os
import time
import wave
from pathlib import Path

import av
from config import RECORDINGS_DIR, DATA_DIR

THUMBNAILS_DIR = DATA_DIR / "thumbnails"
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)


class VideoEngine:
    """
    High-performance video and media processing engine powered by PyAV.
    Handles audio extraction, format probing, video thumbnail generation,
    and online media ingestion without requiring external ffmpeg binaries.
    """

    @staticmethod
    def probe(media_path):
        """
        Probes media file and returns structural metadata.
        """
        path = Path(media_path)
        if not path.exists():
            raise FileNotFoundError(f"Media file not found: {media_path}")

        info = {
            "file_name": path.name,
            "file_size": path.stat().st_size,
            "has_video": False,
            "has_audio": False,
            "duration": 0.0,
            "width": 0,
            "height": 0,
            "format": "",
            "video_codec": None,
            "audio_codec": None,
            "audio_sample_rate": 0,
            "audio_channels": 0
        }

        try:
            with av.open(str(path)) as container:
                info["format"] = container.format.name
                if container.duration:
                    info["duration"] = float(container.duration) / av.time_base

                video_stream = next((s for s in container.streams if s.type == "video"), None)
                if video_stream:
                    info["has_video"] = True
                    info["width"] = video_stream.width
                    info["height"] = video_stream.height
                    info["video_codec"] = video_stream.codec_context.name
                    if not info["duration"] and video_stream.duration:
                        info["duration"] = float(video_stream.duration * video_stream.time_base)

                audio_stream = next((s for s in container.streams if s.type == "audio"), None)
                if audio_stream:
                    info["has_audio"] = True
                    info["audio_codec"] = audio_stream.codec_context.name
                    info["audio_sample_rate"] = audio_stream.codec_context.sample_rate
                    info["audio_channels"] = audio_stream.codec_context.channels
                    if not info["duration"] and audio_stream.duration:
                        info["duration"] = float(audio_stream.duration * audio_stream.time_base)

        except Exception as err:
            print(f"Error probing {media_path}: {err}")

        return info

    @staticmethod
    def extract_audio(
        media_path,
        output_wav_path=None,
        target_sample_rate=16000,
        cancel_event=None,
        progress_callback=None
    ):
        """
        Extracts and resamples audio stream to 16kHz mono 16-bit PCM WAV.
        Returns the path of the saved WAV file.
        """
        in_path = Path(media_path)
        if not in_path.exists():
            raise FileNotFoundError(f"Media file not found: {media_path}")

        if output_wav_path is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
            output_wav_path = RECORDINGS_DIR / f"extracted_{ts}.wav"
        else:
            output_wav_path = Path(output_wav_path)

        output_wav_path.parent.mkdir(parents=True, exist_ok=True)

        with av.open(str(in_path)) as container:
            audio_stream = next((s for s in container.streams if s.type == "audio"), None)
            if not audio_stream:
                raise ValueError(f"No audio track found in {in_path.name}")

            total_duration = 0.0
            if container.duration:
                total_duration = float(container.duration) / av.time_base

            # 16kHz mono 16-bit PCM is standard input for Faster-Whisper
            resampler = av.AudioResampler(
                format="s16",
                layout="mono",
                rate=target_sample_rate
            )

            # Open wave file for direct streaming writes
            wav_out = wave.open(str(output_wav_path), "wb")
            wav_out.setnchannels(1)
            wav_out.setsampwidth(2)  # 16-bit = 2 bytes
            wav_out.setframerate(target_sample_rate)

            try:
                processed_time = 0.0
                for frame in container.decode(audio_stream):
                    if cancel_event and cancel_event.is_set():
                        print("Audio extraction cancelled by user.")
                        wav_out.close()
                        if output_wav_path.exists():
                            output_wav_path.unlink()
                        return None

                    resampled = resampler.resample(frame)
                    if resampled:
                        for r_frame in resampled:
                            raw_bytes = r_frame.to_ndarray().tobytes()
                            wav_out.writeframes(raw_bytes)

                    if frame.pts and audio_stream.time_base:
                        processed_time = float(frame.pts * audio_stream.time_base)
                        if progress_callback and total_duration > 0:
                            pct = min(100, int((processed_time / total_duration) * 100))
                            try:
                                progress_callback(pct, processed_time, total_duration)
                            except Exception:
                                pass

                # Flush any remaining audio in the resampler buffer
                final_resampled = resampler.resample(None)
                if final_resampled:
                    for r_frame in final_resampled:
                        raw_bytes = r_frame.to_ndarray().tobytes()
                        wav_out.writeframes(raw_bytes)

            finally:
                wav_out.close()

        if cancel_event and cancel_event.is_set():
            if output_wav_path.exists():
                output_wav_path.unlink()
            return None

        return str(output_wav_path)

    @staticmethod
    def extract_thumbnail(video_path, output_jpg_path=None, target_time_sec=None):
        """
        Grabs a representative video frame and saves it as a JPEG thumbnail.
        Returns the path of the saved thumbnail or None if no video stream exists.
        """
        in_path = Path(video_path)
        if not in_path.exists():
            return None

        if output_jpg_path is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
            output_jpg_path = THUMBNAILS_DIR / f"thumb_{ts}.jpg"
        else:
            output_jpg_path = Path(output_jpg_path)

        output_jpg_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with av.open(str(in_path)) as container:
                video_stream = next((s for s in container.streams if s.type == "video"), None)
                if not video_stream:
                    return None

                duration = float(container.duration or 0) / av.time_base if container.duration else 0.0
                seek_sec = target_time_sec if target_time_sec is not None else min(5.0, duration * 0.1)

                if seek_sec > 0:
                    pts = int(seek_sec / video_stream.time_base)
                    container.seek(pts, any_frame=False, backward=True, stream=video_stream)

                for frame in container.decode(video_stream):
                    img = frame.to_image()
                    # Resize thumbnail to max 480x270 for crisp preview cards
                    img.thumbnail((480, 270))
                    img.save(str(output_jpg_path), "JPEG", quality=85)
                    return str(output_jpg_path)

        except Exception as err:
            print(f"Thumbnail extraction failed for {video_path}: {err}")

        return None

    @staticmethod
    def download_online_media(url, output_dir=None, cancel_event=None, progress_callback=None):
        """
        Fetches public online video or audio using yt-dlp.
        Downloads a single progressive MP4 or best audio stream without requiring ffmpeg.
        """
        out_dir = Path(output_dir) if output_dir else RECORDINGS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            import yt_dlp
            ts = time.strftime("%Y%m%d_%H%M%S")
            out_template = str(out_dir / f"online_{ts}.%(ext)s")

            ydl_opts = {
                # Prefer combined video+audio in a single file, or best audio
                "format": "best[ext=mp4]/best[height<=720]/bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": out_template,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info)

                # If file with predicted extension does not exist, find the actual file on disk
                if not Path(downloaded_file).exists():
                    matches = list(out_dir.glob(f"online_{ts}.*"))
                    if matches:
                        downloaded_file = str(matches[0])
                    else:
                        raise FileNotFoundError("Downloaded media file was not found on disk.")

                return {
                    "file_path": str(downloaded_file),
                    "title": info.get("title") or "Online Video",
                    "duration": float(info.get("duration", 0.0) or 0.0),
                    "thumbnail": info.get("thumbnail")
                }

        except Exception as err:
            err_msg = str(err)
            if "Sign in to confirm" in err_msg or "bot" in err_msg.lower():
                raise RuntimeError(
                    "YouTube bot verification restricted direct downloading for this specific video. "
                    "Please download the video or audio file to your laptop and use 'Upload Media File'."
                )
            elif "Video unavailable" in err_msg or "Private video" in err_msg:
                raise RuntimeError("This video is private, age-restricted, or unavailable.")
            raise RuntimeError(f"Could not import online video: {err_msg}")
