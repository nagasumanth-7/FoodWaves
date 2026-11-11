import io
import mimetypes
import os
import posixpath
from pathlib import Path
from urllib.parse import urljoin

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible
from django.utils.encoding import filepath_to_uri


@deconstructible
class DatabaseMediaStorage(Storage):
    """
    Stores uploaded media files inside PostgreSQL (`StoredMedia`) so uploads never fail
    on read-only serverless filesystems (e.g. Vercel) and remain persistent across instances.
    Automatically resizes/compresses large uploaded images with Pillow.
    """

    MAX_IMAGE_DIMENSION = 1000
    COMPRESS_THRESHOLD_BYTES = 120 * 1024

    def _normalize_name(self, name):
        clean = posixpath.normpath((name or "").replace("\\", "/")).lstrip("/")
        if clean.startswith("../") or clean == "..":
            raise ValueError(f"Invalid storage path: {name}")
        return clean

    def _optimize_image(self, raw_bytes, content_type):
        """Compresses/resizes oversized raster images while preserving small files as-is."""
        if not content_type.startswith("image/") or content_type == "image/svg+xml":
            return raw_bytes, content_type

        try:
            from PIL import Image, ImageOps

            with Image.open(io.BytesIO(raw_bytes)) as img:
                needs_resize = max(img.width, img.height) > self.MAX_IMAGE_DIMENSION
                if not needs_resize and len(raw_bytes) <= self.COMPRESS_THRESHOLD_BYTES:
                    return raw_bytes, content_type

                transposed = ImageOps.exif_transpose(img)
                if needs_resize:
                    transposed.thumbnail((self.MAX_IMAGE_DIMENSION, self.MAX_IMAGE_DIMENSION))

                if transposed.mode not in ("RGB", "RGBA"):
                    transposed = transposed.convert("RGBA" if "A" in transposed.mode else "RGB")

                out = io.BytesIO()
                transposed.save(out, format="WEBP", quality=82, method=4)
                optimized = out.getvalue()
                if len(optimized) < len(raw_bytes) or needs_resize:
                    return optimized, "image/webp"
        except Exception:
            pass

        return raw_bytes, content_type

    def get_available_name(self, name, max_length=None):
        """Ensures unique name that also fits within the ImageField max_length."""
        clean = self._normalize_name(name)
        dir_name, file_name = posixpath.split(clean)
        stem, ext = posixpath.splitext(file_name)

        if max_length and len(clean) > max_length:
            # Preserve directory, unique suffix room, and extension
            prefix_len = len(dir_name) + (1 if dir_name else 0)
            allowed_stem = max(1, max_length - prefix_len - len(ext) - 10)
            stem = stem[:allowed_stem]
            clean = posixpath.join(dir_name, f"{stem}{ext}") if dir_name else f"{stem}{ext}"

        candidate = clean
        counter = 1
        while self.exists(candidate) or (max_length and len(candidate) > max_length):
            suffix = f"_{counter}"
            if max_length:
                prefix_len = len(dir_name) + (1 if dir_name else 0)
                allowed_stem = max(1, max_length - prefix_len - len(ext) - len(suffix))
                trimmed_stem = stem[:allowed_stem]
                candidate_file = f"{trimmed_stem}{suffix}{ext}"
            else:
                candidate_file = f"{stem}{suffix}{ext}"
            candidate = posixpath.join(dir_name, candidate_file) if dir_name else candidate_file
            counter += 1

        return candidate

    def _open(self, name, mode="rb"):
        from .models import StoredMedia

        clean = self._normalize_name(name)
        media = StoredMedia.objects.filter(name=clean).first()
        if media:
            return ContentFile(bytes(media.data), name=clean)

        disk_path = Path(settings.MEDIA_ROOT) / clean
        if disk_path.is_file():
            with open(disk_path, "rb") as f:
                return ContentFile(f.read(), name=clean)

        raise FileNotFoundError(f"Media file '{clean}' not found.")

    def _save(self, name, content):
        from .models import StoredMedia

        clean = self._normalize_name(name)
        if hasattr(content, "seek"):
            try:
                content.seek(0)
            except Exception:
                pass
        raw_bytes = content.read()
        guessed_type = getattr(content, "content_type", None) or mimetypes.guess_type(clean)[0] or "application/octet-stream"
        final_bytes, final_type = self._optimize_image(raw_bytes, guessed_type)

        StoredMedia.objects.update_or_create(
            name=clean,
            defaults={
                "content_type": final_type,
                "data": final_bytes,
                "size": len(final_bytes),
            },
        )
        return clean

    def exists(self, name):
        from .models import StoredMedia

        clean = self._normalize_name(name)
        if StoredMedia.objects.filter(name=clean).exists():
            return True
        disk_path = Path(settings.MEDIA_ROOT) / clean
        return disk_path.is_file()

    def delete(self, name):
        from .models import StoredMedia

        if not name:
            return
        clean = self._normalize_name(name)
        StoredMedia.objects.filter(name=clean).delete()
        disk_path = Path(settings.MEDIA_ROOT) / clean
        if disk_path.is_file():
            try:
                os.remove(disk_path)
            except OSError:
                pass

    def size(self, name):
        from .models import StoredMedia

        clean = self._normalize_name(name)
        row = StoredMedia.objects.filter(name=clean).values("size").first()
        if row:
            return row["size"]
        disk_path = Path(settings.MEDIA_ROOT) / clean
        if disk_path.is_file():
            return disk_path.stat().st_size
        raise FileNotFoundError(f"Media file '{clean}' not found.")

    def url(self, name):
        if not name:
            return ""
        clean = self._normalize_name(name)
        return urljoin(settings.MEDIA_URL, filepath_to_uri(clean))
