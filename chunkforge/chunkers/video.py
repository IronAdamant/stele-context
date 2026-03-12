"""
Video chunker for ChunkForge.

Splits video files into key frames and audio segments.
Requires opencv-python for video processing.

Install: pip install chunkforge[video]
"""

import io
from typing import Any, Dict, List, Optional

from chunkforge.chunkers.base import BaseChunker, Chunk

# Check for opencv
try:
    import cv2
    import numpy as np
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False
    cv2 = None  # type: ignore
    np = None  # type: ignore


class VideoChunker(BaseChunker):
    """
    Chunker for video files.
    
    Supports:
    - Key frame extraction
    - Time-based segmentation
    - Frame hashing for similarity
    
    Requires: opencv-python (pip install chunkforge[video])
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
                "Install with: pip install chunkforge[video]"
            )
        
        self.segment_duration = segment_duration
        self.keyframe_interval = keyframe_interval
        self.max_dimension = max_dimension
    
    def supported_extensions(self) -> List[str]:
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
    ) -> List[Chunk]:
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
            
            # Compute frames per segment
            frames_per_segment = int(self.segment_duration * fps)
            keyframe_interval_frames = int(self.keyframe_interval * fps)
            
            # Create chunks
            chunks: List[Chunk] = []
            chunk_index = 0
            
            for start_frame in range(0, frame_count, frames_per_segment):
                end_frame = min(start_frame + frames_per_segment, frame_count)
                
                # Extract key frames
                keyframes = []
                for frame_num in range(start_frame, end_frame, keyframe_interval_frames):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                    ret, frame = cap.read()
                    
                    if ret:
                        # Resize if needed
                        frame = self._resize_frame(frame)
                        
                        # Convert to bytes
                        _, buffer = cv2.imencode('.jpg', frame)
                        frame_bytes = buffer.tobytes()
                        
                        # Compute hash
                        frame_hash = self._frame_hash(frame)
                        
                        keyframes.append({
                            "frame_num": frame_num,
                            "time": frame_num / fps,
                            "data": frame_bytes,
                            "hash": frame_hash,
                        })
                
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
                chunks.append(Chunk(
                    content=b"",
                    modality="video",
                    start_pos=0,
                    end_pos=0,
                    document_path=document_path,
                    chunk_index=0,
                    metadata={
                        "fps": fps,
                        "width": width,
                        "height": height,
                        "duration": duration,
                    },
                ))
            
            return chunks
        finally:
            cap.release()
            if cleanup_path:
                import os
                os.unlink(cleanup_path)
    
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
        import hashlib
        
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


class VideoChunk(Chunk):
    """Video-specific chunk with enhanced features."""
    
    def _compute_semantic_signature(self, signature_dim: int = 128) -> List[float]:
        """
        Compute semantic signature for video.
        
        Uses keyframe hashes and temporal features.
        """
        signature = [0.0] * signature_dim
        
        # Keyframe hashes (first 64 dimensions)
        hashes = self.metadata.get("keyframe_hashes", [])
        for i, h in enumerate(hashes[:4]):
            # Convert hash to features
            try:
                hash_int = int(h, 16)
                hash_bits = bin(hash_int)[2:].zfill(16)
                for j, bit in enumerate(hash_bits[:16]):
                    idx = i * 16 + j
                    if idx < 64:
                        signature[idx] = float(bit)
            except ValueError:
                pass
        
        # Temporal features (next 32 dimensions)
        duration = self.metadata.get("duration", 0.0)
        signature[64] = duration / 60.0  # Normalize by minute
        
        fps = self.metadata.get("fps", 0.0)
        signature[65] = fps / 60.0
        
        keyframe_count = self.metadata.get("keyframe_count", 0)
        signature[66] = keyframe_count / 100.0
        
        # Resolution features
        width = self.metadata.get("width", 0)
        height = self.metadata.get("height", 0)
        signature[67] = width / 1920.0
        signature[68] = height / 1080.0
        
        # Normalize
        norm = sum(x * x for x in signature) ** 0.5
        if norm > 0:
            signature = [x / norm for x in signature]
        
        return signature
    
    def _estimate_token_count(self) -> int:
        """Estimate token count for video (1 token per second)."""
        duration = self.metadata.get("duration", 0.0)
        return max(1, int(duration))
