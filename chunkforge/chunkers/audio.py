"""
Audio chunker for ChunkForge.

Splits audio files into time-based segments with MFCC features.
Requires librosa for audio processing.

Install: pip install chunkforge[audio]
"""

from typing import Any, Dict, List

from chunkforge.chunkers.base import BaseChunker, Chunk

# Check for librosa
try:
    import librosa
    import numpy as np

    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False
    librosa = None  # type: ignore
    np = None  # type: ignore


class AudioChunker(BaseChunker):
    """
    Chunker for audio files.

    Supports:
    - Time-based segmentation
    - MFCC feature extraction
    - Spectral features

    Requires: librosa (pip install chunkforge[audio])
    """

    def __init__(
        self,
        segment_duration: float = 30.0,  # seconds
        sample_rate: int = 22050,
        n_mfcc: int = 13,
    ):
        """
        Initialize audio chunker.

        Args:
            segment_duration: Duration of each segment in seconds
            sample_rate: Sample rate for processing
            n_mfcc: Number of MFCC coefficients
        """
        if not HAS_LIBROSA:
            raise ImportError(
                "librosa is required for audio support. "
                "Install with: pip install chunkforge[audio]"
            )

        self.segment_duration = segment_duration
        self.sample_rate = sample_rate
        self.n_mfcc = n_mfcc

    def supported_extensions(self) -> List[str]:
        """Return supported audio file extensions."""
        return [
            ".mp3",
            ".wav",
            ".ogg",
            ".flac",
            ".m4a",
            ".aac",
            ".wma",
        ]

    def chunk(
        self,
        content: Any,
        document_path: str,
        **kwargs: Any,
    ) -> List[Chunk]:
        """
        Split audio into chunks.

        Args:
            content: Audio content (bytes or file path)
            document_path: Path to source document
            **kwargs: Additional options

        Returns:
            List of Chunk objects
        """
        # Load audio
        if isinstance(content, bytes):
            # Save to temp file for librosa
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                y, sr = librosa.load(temp_path, sr=self.sample_rate)
            finally:
                import os

                os.unlink(temp_path)
        elif isinstance(content, str):
            y, sr = librosa.load(content, sr=self.sample_rate)
        else:
            raise ValueError(f"Unsupported content type: {type(content)}")

        # Compute segment samples
        segment_samples = int(self.segment_duration * sr)

        # Create chunks
        chunks: List[Chunk] = []
        chunk_index = 0

        for start_sample in range(0, len(y), segment_samples):
            end_sample = min(start_sample + segment_samples, len(y))
            segment = y[start_sample:end_sample]

            # Skip very short segments
            if len(segment) < sr * 0.1:  # Less than 0.1 seconds
                continue

            # Compute features
            mfcc = self._compute_mfcc(segment, sr)
            spectral_features = self._compute_spectral_features(segment, sr)

            # Time range
            start_time = start_sample / sr
            end_time = end_sample / sr

            chunk = Chunk(
                content=segment.tobytes(),
                modality="audio",
                start_pos=int(start_time * 1000),  # milliseconds
                end_pos=int(end_time * 1000),
                document_path=document_path,
                chunk_index=chunk_index,
                metadata={
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration": end_time - start_time,
                    "sample_rate": sr,
                    "mfcc_mean": mfcc.tolist(),
                    "spectral_features": spectral_features,
                },
            )
            chunks.append(chunk)
            chunk_index += 1

        # Handle empty audio
        if not chunks:
            chunks.append(
                Chunk(
                    content=b"",
                    modality="audio",
                    start_pos=0,
                    end_pos=0,
                    document_path=document_path,
                    chunk_index=0,
                    metadata={"sample_rate": sr},
                )
            )

        return chunks

    def _compute_mfcc(self, y: Any, sr: int) -> Any:
        """Compute MFCC features."""
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=self.n_mfcc)
        # Return mean across time
        return mfcc.mean(axis=1)

    def _compute_spectral_features(self, y: Any, sr: int) -> Dict[str, float]:
        """Compute spectral features."""
        # Spectral centroid
        spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr).mean()

        # Spectral bandwidth
        spectral_bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr).mean()

        # Spectral rolloff
        spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr).mean()

        # Zero crossing rate
        zero_crossing_rate = librosa.feature.zero_crossing_rate(y).mean()

        # RMS energy
        rms = librosa.feature.rms(y=y).mean()

        return {
            "spectral_centroid": float(spectral_centroid),
            "spectral_bandwidth": float(spectral_bandwidth),
            "spectral_rolloff": float(spectral_rolloff),
            "zero_crossing_rate": float(zero_crossing_rate),
            "rms": float(rms),
        }
