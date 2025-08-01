"""
Exporters for converting parsed Transkribus data to different HuggingFace dataset formats.
"""

import io
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Tuple, Union
import requests

from PIL import Image, ImageFile
import numpy as np
import cv2
from datasets import Dataset, Features, Value, Image as DatasetImage

from .parser import PageData, TextLine

# Allow loading of truncated images
ImageFile.LOAD_TRUNCATED_IMAGES = True


class BaseExporter(ABC):
    """Base class for all exporters."""

    def __init__(
            self,
            zip_path: Optional[str] = None,
            folder_path: Optional[str] = None
    ):
        self.zip_path = zip_path
        self.folder_path = Path(folder_path) if folder_path else None
        self.failed_images = []
        self.processed_count = 0
        self.skipped_count = 0

    @abstractmethod
    def export(self, pages: List[PageData]) -> Dataset:
        """Export pages to a HuggingFace dataset."""
        pass

    def _load_image(self, image_source: Union[str, Path, io.BytesIO]) -> Optional[Image.Image]:
        """Load an image either from ZIP file or folder with robust error handling."""
        try:
            image_data = None
            if isinstance(image_source, str):
                if self.zip_path:
                    with zipfile.ZipFile(self.zip_path, "r") as zip_file:
                        image_data = zip_file.read(image_source)
                elif self.folder_path:
                    full_path = self.folder_path / image_source
                    image_data = full_path.read_bytes()
                buffer = io.BytesIO(image_data)
            elif isinstance(image_source, io.BytesIO):
                buffer = image_source
            else:
                raise ValueError(f"Unsupported image source: {type(image_source)}")

            image = Image.open(buffer)
            image.verify()
            buffer.seek(0)
            image = Image.open(buffer)

            if image.mode != "RGB":
                image = image.convert("RGB")

            return self._correct_orientation(image)

        except Exception as e:
            error_msg = f"Error loading image {image_source}: {e}"
            print(f"Warning: {error_msg}")
            self.failed_images.append((image_source, str(e)))
            self.skipped_count += 1
            return None

    def _find_image(self, page: PageData) -> Optional[Union[str, io.BytesIO]]:
        """Find the image path in the ZIP or folder for a given page."""
        possible_paths = [
            f"{page.project_name}/{page.image_filename}",
            f"{page.project_name}/images/{page.image_filename}",
            page.image_filename,
        ]

        if self.zip_path:
            with zipfile.ZipFile(self.zip_path, "r") as zip_file:
                file_list = zip_file.namelist()
                for path in possible_paths:
                    if path in file_list:
                        return path
                for file_path in file_list:
                    if file_path.endswith(page.image_filename):
                        return file_path
        elif self.folder_path:
            for path in possible_paths:
                full_path = self.folder_path / path
                if full_path.is_file():
                    return str(path)
        if page.image_url:
            try:
                response = requests.get(page.image_url, timeout=20)
                response.raise_for_status()
                return io.BytesIO(response.content)
            except requests.exceptions.Timeout:
                print(f'Image download of {page.image_filename} timed out')
                return None
            except requests.exceptions.RequestException as e:
                print(f'Image download from {page.image_url} failed: {e}')
                return None
        return None

    def _crop_region(
            self,
            image: Image.Image,
            coords: List[Tuple[int, int]],
            mask: bool = False,
            min_width: Optional[int] = None,
    ) -> Optional[Image.Image]:
        """Crop a region from an image based on coordinates, optimized by pre-cropping to bounding box."""
        if not coords:
            print("Warning: No coordinates provided for cropping.")
            self.skipped_count += 1
            return None

        try:
            # Calculate bounding box for the coordinates
            x_coords = [pt[0] for pt in coords]
            y_coords = [pt[1] for pt in coords]
            min_x, max_x = max(0, min(x_coords)), min(image.width, max(x_coords))
            min_y, max_y = max(0, min(y_coords)), min(image.height, max(y_coords))

            if min_x >= max_x or min_y >= max_y:
                print(f"Warning: Invalid crop coordinates: ({min_x}, {min_y}, {max_x}, {max_y})")
                self.skipped_count += 1
                return None

            if min_width and int(max_x - min_x) < min_width:
                self.skipped_count += 1
                return None

            # Bild und Koordinaten auf Bounding Box beschränken
            image_cropped = image.crop((min_x, min_y, max_x, max_y))
            shifted_coords = [(x - min_x, y - min_y) for (x, y) in coords]
            img_array = cv2.cvtColor(np.array(image_cropped), cv2.COLOR_RGB2BGR)

            if mask:
                mask_img = np.zeros(img_array.shape[:2], dtype=np.uint8)
                pts = np.array([shifted_coords], dtype=np.int32)
                cv2.fillPoly(mask_img, pts, 255)
                white_bg = np.ones_like(img_array, dtype=np.uint8) * 255
                result = np.where(mask_img[:, :, None] == 255, img_array, white_bg)
            else:
                result = img_array

            result_rgb = cv2.cvtColor(result, cv2.COLOR_BGR2RGB)
            return Image.fromarray(result_rgb)

        except Exception as e:
            print(f"Warning: Error cropping region: {e}")
            return None

    @staticmethod
    def _calculate_bounding_box(
            coords_list: List[List[Tuple[int, int]]],
    ) -> List[Tuple[int, int]]:
        """Calculate the bounding box that encompasses multiple coordinate sets."""
        if not coords_list:
            return []

        all_coords = []
        for coords in coords_list:
            all_coords.extend(coords)

        if not all_coords:
            return []

        x_coords = [coord[0] for coord in all_coords]
        y_coords = [coord[1] for coord in all_coords]

        min_x, max_x = min(x_coords), max(x_coords)
        min_y, max_y = min(y_coords), max(y_coords)

        # Return as rectangle coordinates
        return [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]

    def _print_summary(self, dataset: Optional[Dataset] = None) -> None:
        """Print processing summary."""
        print("#" * 60)
        if not dataset:
            print("No dataset created.")
            return
        elif self.processed_count == 0 and self.skipped_count == 0:
            print("⚠️ No new items processed — dataset likely loaded from cache.")
        else:
            print("\nProcessing Summary:")
            print(f"  Successfully processed: {self.processed_count}")
            print(f"  Skipped due to errors: {self.skipped_count}")
            if self.failed_images:
                print("  Failed images:")
                for image_path, error in self.failed_images[:5]:  # Show first 5 errors
                    print(f"    {image_path}: {error}")
                if len(self.failed_images) > 5:
                    print(f"    ... and {len(self.failed_images) - 5} more")

    @staticmethod
    def _correct_orientation(image: Image.Image) -> Image.Image:
        try:
            exif = image.getexif()

            if exif:
                # key 274 = orientation, returns 1 if not existing
                orientation = exif.get(274, 1)

                if orientation == 3:
                    image = image.rotate(180, expand=True)
                elif orientation == 6:
                    image = image.rotate(270, expand=True)
                elif orientation == 8:
                    image = image.rotate(90, expand=True)
        except (AttributeError, KeyError, IndexError):
            pass

        return image


class RawXMLExporter(BaseExporter):
    """Export raw images with their corresponding XML content."""

    def export(self, pages: List[PageData]) -> Union[Dataset, None]:
        """Export pages as image + raw XML pairs."""
        print(f"Exporting raw XML content with images... (Processed: {len(pages)})")

        def generate_examples():
            """Generate examples from pages with images and XML content."""
            for page in pages:
                image_path = self._find_image(page)
                if not image_path:
                    print(f"Warning: No image found for {page.image_filename} in project {page.project_name}")
                    continue
                if image_path:
                    image = self._load_image(image_path)
                    if image:
                        self.processed_count += 1
                        yield {
                            "image": image,
                            "xml": page.xml_content,
                            "filename": page.image_filename,
                            "project": page.project_name,
                        }

        features = Features(
            {
                "image": DatasetImage(),
                "xml": Value("string"),
                "filename": Value("string"),
                "project": Value("string"),
            }
        )

        try:
            dataset = Dataset.from_generator(
                generate_examples, features=features, cache_dir=None
            )
        except Exception as e:
            print(f"Error creating dataset: {e}")
            dataset = None

        self._print_summary(dataset)
        return dataset


class TextExporter(BaseExporter):
    """Export images with concatenated text content."""

    def export(self, pages: List[PageData]) -> Dataset:
        """Export pages as image + full text pairs."""

        def generate_examples():
            for page in pages:
                image_path = self._find_image(page)
                if image_path:
                    image = self._load_image(image_path)
                    if image:
                        # Concatenate all text from regions in reading order
                        full_text = "\n".join(
                            [
                                region.full_text
                                for region in page.regions
                                if region.full_text
                            ]
                        )
                        self.processed_count += 1
                        yield {
                            "image": image,
                            "text": full_text,
                            "filename": page.image_filename,
                            "project": page.project_name,
                        }

        features = Features(
            {
                "image": DatasetImage(),
                "text": Value("string"),
                "filename": Value("string"),
                "project": Value("string"),
            }
        )

        try:
            dataset = Dataset.from_generator(
                generate_examples, features=features, cache_dir=None
            )
        except Exception as e:
            print(f"Error creating dataset: {e}")
            dataset = None

        self._print_summary(dataset)
        return dataset


class RegionExporter(BaseExporter):
    """Export individual regions as separate images with metadata."""

    def export(
            self,
            pages: List[PageData],
            mask: bool = False,
            min_width: Optional[int] = None,
            allow_empty: bool = False,
    ) -> Dataset:
        """Export each region as a separate dataset entry."""

        def generate_examples():
            for page in pages:
                image_path = self._find_image(page)
                if image_path:
                    full_image = self._load_image(image_path)
                    if full_image:
                        for region in page.regions:
                            if region.full_text or allow_empty:
                                region_image = self._crop_region(
                                    full_image,
                                    region.coords,
                                    mask=mask,
                                    min_width=min_width,
                                )
                                if region_image:
                                    self.processed_count += 1
                                    yield {
                                        "image": region_image,
                                        "text": region.full_text,
                                        "region_type": region.type,
                                        "region_id": region.id,
                                        "reading_order": region.reading_order,
                                        "filename": page.image_filename,
                                        "project": page.project_name,
                                    }

        features = Features(
            {
                "image": DatasetImage(),
                "text": Value("string"),
                "region_type": Value("string"),
                "region_id": Value("string"),
                "reading_order": Value("int32"),
                "filename": Value("string"),
                "project": Value("string"),
            }
        )

        try:
            dataset = Dataset.from_generator(
                generate_examples, features=features, cache_dir=None
            )
        except Exception as e:
            print(f"Error creating dataset: {e}")
            dataset = None

        self._print_summary(dataset)
        return dataset


class LineExporter(BaseExporter):
    """Export individual text lines as separate images with metadata."""

    def export(
            self,
            pages: List[PageData],
            mask: bool = False,
            min_width: Optional[int] = None,
            allow_empty: bool = False,
    ) -> Dataset:
        """Export each text line as a separate dataset entry."""

        def generate_examples():
            for page in pages:
                image_path = self._find_image(page)
                if image_path:
                    full_image = self._load_image(image_path)
                    if full_image:
                        for region in page.regions:
                            for line in region.text_lines:
                                if line.text or allow_empty:
                                    line_image = self._crop_region(
                                        full_image,
                                        line.coords,
                                        mask=mask,
                                        min_width=min_width,
                                    )
                                    if line_image:
                                        self.processed_count += 1
                                        yield {
                                            "image": line_image,
                                            "text": line.text if line.text else "",
                                            "line_id": line.id,
                                            "line_reading_order": line.reading_order,
                                            "region_id": line.region_id,
                                            "region_reading_order": region.reading_order,
                                            "region_type": region.type,
                                            "filename": page.image_filename,
                                            "project": page.project_name,
                                        }

        features = Features(
            {
                "image": DatasetImage(),
                "text": Value("string"),
                "line_id": Value("string"),
                "line_reading_order": Value("int32"),
                "region_id": Value("string"),
                "region_reading_order": Value("int32"),
                "region_type": Value("string"),
                "filename": Value("string"),
                "project": Value("string"),
            }
        )

        try:
            dataset = Dataset.from_generator(
                generate_examples, features=features, cache_dir=None
            )
        except Exception as e:
            print(f"Error creating dataset: {e}")
            dataset = None

        self._print_summary(dataset)
        return dataset


class WindowExporter(BaseExporter):
    """Export sliding windows of text lines with configurable window size and overlap."""

    def __init__(
            self,
            zip_path: Optional[str] = None,
            folder_path: Optional[str] = None,
            window_size: int = 2,
            overlap: int = 0,
    ):
        """
        Initialize the window exporter.

        Args:
            zip_path: Path to the ZIP file
            folder_path: Path to the folder containing images
            window_size: Number of lines per window (1, 2, 3, etc.)
            overlap: Number of lines to overlap between windows
        """
        super().__init__(zip_path=zip_path, folder_path=folder_path)
        self.window_size = window_size
        self.overlap = overlap

        if overlap >= window_size:
            raise ValueError("Overlap must be less than window size")

    def export(self, pages: List[PageData], mask: bool = False) -> Dataset:
        """Export sliding windows of lines as separate dataset entries."""

        def generate_examples():
            for page in pages:
                image_path = self._find_image(page)
                if image_path:
                    full_image = self._load_image(image_path)
                    if full_image:
                        for region in page.regions:
                            # Generate sliding windows for this region
                            windows = self._create_windows(region.text_lines)
                            for window_idx, window_lines in enumerate(windows):
                                # Calculate bounding box for all lines in this window
                                line_coords = [
                                    line.coords for line in window_lines if line.coords
                                ]
                                if line_coords:
                                    window_coords = self._calculate_bounding_box(
                                        line_coords
                                    )
                                    window_image = self._crop_region(
                                        full_image, window_coords, mask
                                    )
                                    if window_image:
                                        # Combine text from all lines in window
                                        window_text = "\n".join(
                                            [
                                                line.text
                                                for line in window_lines
                                                if line.text
                                            ]
                                        )
                                        # Create line info for metadata
                                        line_ids = [line.id for line in window_lines]
                                        line_orders = [
                                            line.reading_order for line in window_lines
                                        ]
                                        self.processed_count += 1
                                        yield {
                                            "image": window_image,
                                            "text": window_text,
                                            "window_size": len(window_lines),
                                            "window_index": window_idx,
                                            "line_ids": ", ".join(line_ids),
                                            "line_reading_orders": ", ".join(
                                                map(str, line_orders)
                                            ),
                                            "region_id": region.id,
                                            "region_reading_order": region.reading_order,
                                            "region_type": region.type,
                                            "filename": page.image_filename,
                                            "project": page.project_name,
                                        }

        # Create dataset using generator to avoid memory issues
        features = Features(
            {
                "image": DatasetImage(),
                "text": Value("string"),
                "window_size": Value("int32"),
                "window_index": Value("int32"),
                "line_ids": Value("string"),
                "line_reading_orders": Value("string"),
                "region_id": Value("string"),
                "region_reading_order": Value("int32"),
                "region_type": Value("string"),
                "filename": Value("string"),
                "project": Value("string"),
            }
        )

        try:
            dataset = Dataset.from_generator(
                generate_examples, features=features, cache_dir=None
            )
        except Exception as e:
            print(f"Error creating dataset: {e}")
            dataset = None

        self._print_summary(dataset)
        return dataset

    def _create_windows(self, lines: List[TextLine]) -> List[List[TextLine]]:
        """Create sliding windows of lines with specified size and overlap."""
        if not lines:
            return []

        windows = []
        step = self.window_size - self.overlap

        for i in range(0, len(lines), step):
            window = lines[i: i + self.window_size]
            if (
                    len(window) > 0
            ):  # Always include windows, even if smaller than window_size
                windows.append(window)

            # Stop if we've reached the end and the last window would be too small
            # (unless we want to include partial windows)
            if i + self.window_size >= len(lines):
                break

        return windows
