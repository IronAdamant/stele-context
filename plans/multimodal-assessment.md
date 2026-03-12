# ChunkForge Multi-Modal Support Assessment

## Current State: Text/Code Only

ChunkForge v0.1.0 is **text-only**. Here's what that means:

### What Works Now
- ✅ Plain text files (.txt, .md, .rst)
- ✅ Code files (.py, .js, .ts, .java, etc.)
- ✅ Structured text (.json, .yaml, .xml, .csv)
- ✅ Documentation (.md, .rst, .adoc)

### What Doesn't Work
- ❌ Images (.png, .jpg, .gif, .webp)
- ❌ Audio (.mp3, .wav, .ogg)
- ❌ Video (.mp4, .webm)
- ❌ PDFs (binary format)
- ❌ Office documents (.docx, .xlsx, .pptx)
- ❌ Binary data

---

## Why Text-Only in v0.1.0?

1. **Scope control** - Text is the primary use case for coding agents
2. **Simplicity** - Text chunking is well-understood
3. **Zero dependencies** - No need for image/audio processing libraries
4. **MVP approach** - Ship working text support first, expand later

---

## Multi-Modal Architecture Requirements

### 1. Modality Detection

```python
# Need to detect file type
def detect_modality(file_path: str) -> str:
    """Detect file modality: text, image, audio, video, binary"""
    ext = Path(file_path).suffix.lower()
    
    TEXT_EXTENSIONS = {'.txt', '.md', '.py', '.js', ...}
    IMAGE_EXTENSIONS = {'.png', '.jpg', '.gif', '.webp', ...}
    AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', ...}
    VIDEO_EXTENSIONS = {'.mp4', '.webm', ...}
    
    if ext in TEXT_EXTENSIONS:
        return "text"
    elif ext in IMAGE_EXTENSIONS:
        return "image"
    # ... etc
```

### 2. Modality-Specific Chunkers

Each modality needs different chunking strategies:

| Modality | Chunking Strategy | Chunk Size |
|----------|-------------------|------------|
| **Text** | Paragraph/sentence boundaries | ~256 tokens |
| **Code** | Function/class boundaries (AST) | ~256 tokens |
| **Image** | Whole image or grid tiles | 1 image or NxN tiles |
| **Audio** | Time-based segments | 30-60 seconds |
| **Video** | Frame extraction + audio | Key frames + audio segments |
| **PDF** | Page-based or section-based | 1 page or section |

### 3. Modality-Specific Semantic Signatures

| Modality | Signature Approach | Dependencies |
|----------|-------------------|--------------|
| **Text** | TF-style features (current) | None |
| **Code** | AST features + TF | ast (stdlib) |
| **Image** | Perceptual hash + color histogram | PIL/Pillow |
| **Audio** | MFCC features + spectral analysis | librosa or scipy |
| **Video** | Frame hashes + audio features | opencv + librosa |
| **PDF** | Text extraction + layout features | pymupdf or pdfplumber |

### 4. Storage Considerations

Current storage is optimized for text:
- SQLite for metadata
- Small KV-cache files

Multi-modal needs:
- **Binary blob storage** - Images, audio, video are large
- **Thumbnail generation** - For quick previews
- **Metadata extraction** - EXIF, ID3 tags, etc.
- **Compression** - Critical for media files

---

## Dependency Analysis

### Current: Zero Dependencies ✅

### Text + Code Enhancement: Still Zero ✅
- AST parsing (stdlib)
- Better chunking (regex, stdlib)

### Image Support: +1 Dependency ⚠️
- **Pillow** (PIL) - Image processing
- Offline: ✅ Yes
- Supply chain risk: 🟡 Low (mature, widely used)

### Audio Support: +1-2 Dependencies ⚠️
- **librosa** or **scipy** - Audio analysis
- Offline: ✅ Yes
- Supply chain risk: 🟡 Low-Medium

### Video Support: +2-3 Dependencies ⚠️
- **opencv-python** - Video frame extraction
- **librosa** - Audio track analysis
- Offline: ✅ Yes
- Supply chain risk: 🟡 Medium

### PDF Support: +1 Dependency ⚠️
- **pymupdf** or **pdfplumber** - PDF parsing
- Offline: ✅ Yes
- Supply chain risk: 🟡 Low

---

## Recommended Approach

### Phase 1: Text/Code Enhancement (v0.1.x) - Zero Dependencies
- ✅ Code-aware chunking (Python AST)
- ✅ Better text chunking (sentence boundaries)
- ✅ Markdown structure awareness
- ✅ JSON/YAML structure awareness

### Phase 2: Image Support (v0.2.0) - +1 Optional Dependency
- Add Pillow as optional dependency
- Image perceptual hashing
- Image tile-based chunking
- Color histogram signatures
- Thumbnail generation

### Phase 3: PDF Support (v0.2.x) - +1 Optional Dependency
- Add pymupdf as optional dependency
- Page-based chunking
- Text extraction from PDFs
- Layout-aware chunking

### Phase 4: Audio/Video (v0.3.0) - +2-3 Optional Dependencies
- Add librosa, opencv as optional dependencies
- Time-based chunking
- MFCC/spectral signatures
- Key frame extraction

---

## Implementation Strategy

### Keep Zero Dependencies for Core

```python
# Core: zero dependencies
pip install chunkforge

# Image support
pip install chunkforge[image]

# Audio support  
pip install chunkforge[audio]

# Video support
pip install chunkforge[video]

# PDF support
pip install chunkforge[pdf]

# Everything
pip install chunkforge[all]
```

### Modular Chunker Architecture

```python
# chunkforge/chunkers/
├── __init__.py
├── base.py          # Abstract base chunker
├── text.py          # Text chunker (current)
├── code.py          # Code-aware chunker
├── image.py         # Image chunker (requires Pillow)
├── audio.py         # Audio chunker (requires librosa)
├── video.py         # Video chunker (requires opencv)
└── pdf.py           # PDF chunker (requires pymupdf)
```

### Lazy Loading

```python
# Only import if available
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

class ImageChunker:
    def __init__(self):
        if not HAS_PIL:
            raise ImportError("Pillow required for image support. Install: pip install chunkforge[image]")
```

---

## Security Considerations

### Supply Chain Risk by Dependency

| Package | Risk Level | Mitigation |
|---------|------------|------------|
| Pillow | 🟡 Low | Pin version, audit regularly |
| librosa | 🟡 Low-Medium | Pin version, audit regularly |
| opencv-python | 🟡 Medium | Pin version, consider headless only |
| pymupdf | 🟡 Low | Pin version, audit regularly |

### Recommendations

1. **Keep core zero-dependency** - Always works without any packages
2. **Optional extras only** - Users opt-in to dependencies
3. **Pin versions** - Prevent supply chain attacks via version pinning
4. **Audit regularly** - Check for vulnerabilities in optional deps
5. **Provide alternatives** - Pure Python fallbacks where possible

---

## Answer to Your Question

**Does v0.1.0 account for multi-modal?**

**No, v0.1.0 is text/code only.** But the architecture is designed to be extensible:

1. ✅ **Modular design** - Easy to add new chunkers
2. ✅ **Optional dependencies** - Can add deps without breaking core
3. ✅ **Fallback pattern** - Already proven with numpy/msgspec
4. ✅ **Storage is generic** - Can store any binary blob
5. ✅ **MCP protocol is modality-agnostic** - Tools work for any content

**For v0.2.0+, multi-modal is feasible** with:
- 1-3 optional dependencies per modality
- All dependencies are offline-safe
- Core remains zero-dependency
- Users opt-in to what they need

---

## Recommendation

**For v0.2.0, prioritize:**

1. **Code-aware chunking** (zero deps, high value for coding agents)
2. **Image support** (1 dep, high value for documentation)
3. **PDF support** (1 dep, high value for knowledge bases)

**Defer to v0.3.0:**
- Audio/video (more complex, lower priority for coding agents)

This keeps the supply chain minimal while adding the most valuable multi-modal features first.
