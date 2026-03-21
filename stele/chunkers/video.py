"""
Video chunker for Stele.

Splits video files into key frames and audio segments.
Requires opencv-python for video processing.

Install: pip install stele[video]
"""

from __future__ import annotations

from typing import Any

from stele.chunkers.base import BaseChunker, Chunk

# Check for opencv
try:
    import cv2

    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False
    cv2 = None  # type: ignore


class VideoChunker(BaseChunker):
    """
    Chunker for video files.

    Supports:
    - Key frame extraction
    - Time-based segmentation
    - Frame hashing for similarity

    Requires: opencv-python (pip install stele[video])
    """

    def __init__(
        self,
        segment_duration: float = 10.0,  # seconds
        keyframe_interval: float = 1.0,  # seconds
        max_dimension: int = 640,
    ):
        """
        Initialize video chunker.

        Args:
            segment_duration: Duration of each segment in seconds
            keyframe_interval: Interval between key frames in seconds
            max_dimension: Maximum dimension for frame processing
        """
        if not HAS_OPENCV:
            raise ImportError(
                "opencv-python is required for video support. "
                "Install with: pip install stele[video]"
            )

        self.segment_duration = segment_duration
        self.keyframe_interval = keyframe_interval
        self.max_dimension = max_dimension

    def supported_extensions(self) -> list[str]:
        """Return supported video file extensions."""
        return [
            ".mp4",
            ".avi",
            ".mov",
            ".mkv",
            ".webm",
            ".flv",
            ".wmv",
        ]

    def chunk(
        self,
        content: Any,
        document_path: str,
        **kwargs: Any,
    ) -> list[Chunk]:
        """
        Split video into chunks.

        Args:
            content: Video content (bytes or file path)
            document_path: Path to source document
            **kwargs: Additional options

        Returns:
            List of Chunk objects
        """
        # Open video
        if isinstance(content, bytes):
            # Save to temp file for opencv
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                f.write(content)
                temp_path = f.name

            cap = cv2.VideoCapture(temp_path)
            cleanup_path = temp_path
        elif isinstance(content, str):
            cap = cv2.VideoCapture(content)
            cleanup_path = None
        else:
            raise ValueError(f"Unsupported content type: {type(content)}")

        try:
            if not cap.isOpened():
                raise ValueError("Could not open video file")

            # Get video properties
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = frame_count / fps if fps > 0 else 0

            # Guard: cannot segment with zero/negative fps
            if fps <= 0 or frame_count <= 0:
                return self._empty_video_chunk(document_path, width, height, duration)

            # Compute frames per segment
            frames_per_segment = max(1, int(self.segment_duration * fps))
            keyframe_interval_frames = max(1, int(self.keyframe_interval * fps))

            # Create chunks
            chunks: list[Chunk] = []
            chunk_index = 0

            for start_frame in range(0, frame_count, frames_per_segment):
                end_frame = min(start_frame + frames_per_segment, frame_count)

                # Extract key frames
                keyframes = []
                for frame_num in range(
                    start_frame, end_frame, keyframe_interval_frames
                ):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                    ret, frame = cap.read()

                    if ret:
                        # Resize if needed
                        frame = self._resize_frame(frame)

                        # Convert to bytes
                        _, buffer = cv2.imencode(".jpg", frame)
                        frame_bytes = buffer.tobytes()

                        # Compute hash
                        frame_hash = self._frame_hash(frame)

                        keyframes.append(
                            {
                                "frame_num": frame_num,
                                "time": frame_num / fps,
                                "data": frame_bytes,
                                "hash": frame_hash,
                            }
                        )

                if not keyframes:
                    continue

                # Time range
                start_time = start_frame / fps
                end_time = end_frame / fps

                # Combine keyframe data
                combined_data = b"".join(kf["data"] for kf in keyframes)

                chunk = Chunk(
                    content=combined_data,
                    modality="video",
                    start_pos=int(start_time * 1000),  # milliseconds
                    end_pos=int(end_time * 1000),
                    document_path=document_path,
                    chunk_index=chunk_index,
                    metadata={
                        "start_time": start_time,
                        "end_time": end_time,
                        "duration": end_time - start_time,
                        "fps": fps,
                        "width": width,
                        "height": height,
                        "keyframe_count": len(keyframes),
                        "keyframe_hashes": [kf["hash"] for kf in keyframes],
                    },
                )
                chunks.append(chunk)
                chunk_index += 1

            # Handle empty video
            if not chunks:
                chunks.extend(
                    self._empty_video_chunk(document_path, width, height, duration)
                )

            return chunks
        finally:
            cap.release()
            if cleanup_path:
                import os

                os.unlink(cleanup_path)

    @staticmethod
    def _empty_video_chunk(
        document_path: str, width: int = 0, height: int = 0, duration: float = 0
    ) -> list[Chunk]:
        """Create an empty fallback chunk for unreadable/empty videos."""
        return [
            Chunk(
                content=b"",
                modality="video",
                start_pos=0,
                end_pos=0,
                document_path=document_path,
                chunk_index=0,
                metadata={"width": width, "height": height, "duration": duration},
            )
        ]

    def _resize_frame(self, frame: Any) -> Any:
        """Resize frame if it exceeds max_dimension."""
        height, width = frame.shape[:2]

        if width <= self.max_dimension and height <= self.max_dimension:
            return frame

        # Calculate new size
        ratio = min(self.max_dimension / width, self.max_dimension / height)
        new_size = (int(width * ratio), int(height * ratio))

        return cv2.resize(frame, new_size)

    def _frame_hash(self, frame: Any) -> str:
        """Compute perceptual hash of frame."""

        # Convert to grayscale
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        # Resize to 8x8
        small = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)

        # Compute average
        avg = small.mean()

        # Create hash bits
        bits = "".join("1" if p > avg else "0" for p in small.flatten())

        # Convert to hex
        return hex(int(bits, 2))[2:].zfill(16)
