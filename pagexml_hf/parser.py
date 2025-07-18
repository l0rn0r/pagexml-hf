"""
Parser for Transkribus ZIP files and PAGE XML format.
"""

import os
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple, Callable

import chardet


@dataclass
class TextLine:
    """Represents a text line in the PAGE XML."""

    id: str
    text: Optional[str]
    coords: List[Tuple[int, int]]
    baseline: Optional[List[Tuple[int, int]]]
    reading_order: int
    region_id: str


@dataclass
class TextRegion:
    """Represents a text region in the PAGE XML."""

    id: str
    type: str
    coords: List[Tuple[int, int]]
    text_lines: List[TextLine]
    reading_order: int
    full_text: Optional[str]


@dataclass
class PageData:
    """Represents a complete page with metadata and content."""

    image_filename: str
    image_width: int
    image_height: int
    image_url: str
    regions: List[TextRegion]
    xml_content: str
    project_name: str


class XmlParser:
    """
    Parser for Transkribus ZIP files containing PAGE XML format or
    Page XML files in a folder structure with images or image-URL in the XML.
    """

    def __init__(self, namespace: Optional[str] = None):
        if namespace:
            self.namespace = {
                "pc": namespace
            }
        else:
            # Default namespace for PAGE XML
            # This can be overridden if needed
            self.namespace = {
                "pc": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"
            }

    def parse_zip(self, zip_path: str) -> List[PageData]:
        """
        Parse a Transkribus ZIP file and extract all page data.

        Args:
            zip_path: Path to the ZIP file

        Returns:
            List of PageData objects
        """
        pages = []

        with zipfile.ZipFile(zip_path) as zip_file:
            # Get all files in the ZIP
            file_list = zip_file.namelist()

            # Group files by project (top-level directory)
            projects = self._auto_group_files(file_list)

            for project_name, project_files in projects.items():
                print(f"Processing project: {project_name} ({len(project_files)} files)")
                xml_files = [
                    f
                    for f in project_files
                    if f.endswith(".xml")
                       and not self._is_metadata_file(f)
                       and not self._is_macos_metadata_file(f)
                ]

                def file_loader(f: str) -> Optional[str]:
                    """
                    Load XML file content with automatic encoding detection.
                    """
                    return self._read_xml_with_encoding(zip_file, f)

                pages.extend(self._parse_files(xml_files, file_loader, project_name))

        return pages

    def parse_folder(self, folder_path: str) -> List[PageData]:
        """
        Parse PAGE XML files from a folder path mimicking Transkribus structure.

        Args:
            folder_path: Path to the folder

        Returns:
            List of PageData objects
        """
        pages = []
        folder = Path(folder_path)
        xml_files = [str(p) for p in folder.rglob("*.xml") if p.is_file()]
        projects = self._auto_group_files(xml_files)

        for project_name, project_files in projects.items():
            print(f"Processing project: {project_name} ({len(project_files)} files)")
            xml_files = [
                f
                for f in project_files
                if f.endswith(".xml")
                   and not self._is_metadata_file(f)
                   and not self._is_macos_metadata_file(f)
            ]

            def file_loader(file_path: str) -> Optional[str]:
                """
                Load XML file content with automatic encoding detection.
                """
                try:
                    with open(file_path, "rb") as f:
                        raw_content = f.read()
                    return self._decode_bytes(raw_content, file_path)
                except Exception as e:
                    print(f"Error reading {file_path}: {e}")
                    return None

            pages.extend(self._parse_files(xml_files, file_loader, project_name))

        return pages

    def _parse_files(
            self,
            file_paths: List[str],
            loader: Callable[[str], Optional[str]],
            project_name: str,
    ) -> List[PageData]:
        pages = []
        for file_path in file_paths:
            try:
                xml_content = loader(file_path)
                if xml_content is None:
                    print(f"Skipping {file_path} due to read error")
                    continue

                page_data = self._parse_page_xml(xml_content, project_name)
                if page_data:
                    pages.append(page_data)
            except Exception as e:
                print(f"Error parsing {file_path}: {e}")
        return pages

    def _read_xml_with_encoding(
            self, zip_file: zipfile.ZipFile, xml_file: str
    ) -> Optional[str]:
        """Read XML content with automatic encoding detection and fallback."""
        try:
            raw_content = zip_file.read(xml_file)
            return self._decode_bytes(raw_content, xml_file)
        except Exception as e:
            print(f"Error reading {xml_file}: {e}")
            return None

    @staticmethod
    def _decode_bytes(raw_content: bytes, source_name: str = "") -> Optional[str]:
        try:
            return raw_content.decode("utf-8")
        except UnicodeDecodeError:
            pass

        detected = chardet.detect(raw_content)
        if detected and detected["confidence"] > 0.7:
            encoding = detected["encoding"]
            try:
                return raw_content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                pass

        for enc in ["latin-1", "cp1252", "iso-8859-1"]:
            try:
                return raw_content.decode(enc)
            except UnicodeDecodeError:
                continue

        print(f"Could not decode {source_name} with any supported encoding")
        return None

    @staticmethod
    def _is_macos_metadata_file(file_path: str) -> bool:
        """Check if a file is a macOS metadata file that should be skipped."""
        # Skip __MACOSX directory and ._ prefixed files
        if "__MACOSX" in file_path or file_path.startswith("._"):
            return True

        # Skip other common macOS metadata patterns
        if "/." in file_path and not file_path.endswith(".xml"):
            return True

        return False

    @staticmethod
    def _is_metadata_file(file_path: str) -> bool:
        """Check if a file is a metadata file that should be skipped."""
        base = os.path.basename(file_path)
        return base.lower() in ['mets.xml', 'metadata.xml']

    def _parse_page_xml(
            self, xml_content: str, project_name: str
    ) -> Optional[PageData]:
        """Parse a single PAGE XML file."""
        try:
            root = ET.fromstring(xml_content)

            # Get page element
            page_elem = root.find("pc:Page", self.namespace)
            if page_elem is None:
                return None

            # Extract page metadata
            image_filename = page_elem.get("imageFilename", "")
            image_width = int(page_elem.get("imageWidth", 0))
            image_height = int(page_elem.get("imageHeight", 0))

            # if available, extract image URL
            image_url = self._parse_imgurl(root)

            # Parse reading order
            reading_order = self._parse_reading_order(root)

            # Parse text regions
            regions = self._parse_text_regions(root, reading_order)

            return PageData(
                image_filename=image_filename,
                image_width=image_width,
                image_height=image_height,
                image_url=image_url,
                regions=regions,
                xml_content=xml_content,
                project_name=project_name,
            )

        except ET.ParseError as e:
            print(f"XML parsing error: {e}")
            return None

    def _auto_group_files(self, file_list: List[str]) -> Dict[str, List[str]]:
        """
        Automatically group files by their top-level directory (project).
        This is a helper function to ensure files are grouped correctly.
        """
        xmls = [f for f in file_list if f.endswith(".xml") and not self._is_metadata_file(f)]
        project_names = {}
        for f in xmls:
            project_name = self._get_logical_project_parent(f)
            if project_name not in project_names:
                project_names[project_name] = []
            project_names[project_name].append(f)

        return project_names

    def _parse_reading_order(self, root: ET.Element) -> Dict[str, int]:
        """Parse the reading order from the XML."""
        reading_order = {}

        reading_order_elem = root.find(".//pc:ReadingOrder", self.namespace)
        if reading_order_elem is not None:
            for region_ref in reading_order_elem.findall(
                    ".//pc:RegionRefIndexed", self.namespace
            ):
                region_id = region_ref.get("regionRef", "")
                index = int(region_ref.get("index", 0))
                reading_order[region_id] = index

        return reading_order

    def _parse_text_regions(
            self, root: ET.Element, reading_order: Dict[str, int]
    ) -> List[TextRegion]:
        """Parse all text regions from the XML."""
        regions = []

        for region_elem in root.findall(".//pc:TextRegion", self.namespace):
            region_id = region_elem.get("id", "")
            region_type = region_elem.get("type", "paragraph")

            # Parse coordinates
            coords = self._parse_coords(region_elem.find("pc:Coords", self.namespace))

            # Parse text lines
            text_lines = self._parse_text_lines(region_elem, region_id)

            # Get full text from TextEquiv
            full_text = self._get_text_equiv(region_elem)

            # Get reading order
            region_reading_order = reading_order.get(region_id, 0)

            region = TextRegion(
                id=region_id,
                type=region_type,
                coords=coords,
                text_lines=text_lines,
                reading_order=region_reading_order,
                full_text=full_text,
            )

            regions.append(region)

        # Sort regions by reading order
        regions.sort(key=lambda r: r.reading_order)

        return regions

    def _parse_text_lines(
            self, region_elem: ET.Element, region_id: str
    ) -> List[TextLine]:
        """Parse text lines within a region."""
        lines = []

        for line_elem in region_elem.findall("pc:TextLine", self.namespace):
            line_id = line_elem.get("id", "")

            # Parse coordinates
            coords = self._parse_coords(line_elem.find("pc:Coords", self.namespace))

            # Parse baseline
            baseline_elem = line_elem.find("pc:Baseline", self.namespace)
            baseline = (
                self._parse_coords(baseline_elem) if baseline_elem is not None else None
            )

            # Get text content
            text = self._get_text_equiv(line_elem)

            # Extract reading order from custom attribute
            reading_order = self._extract_reading_order_from_custom(line_elem)

            line = TextLine(
                id=line_id,
                text=text,
                coords=coords,
                baseline=baseline,
                reading_order=reading_order,
                region_id=region_id,
            )

            lines.append(line)

        # Sort lines by reading order
        lines.sort(key=lambda ln: ln.reading_order)

        return lines

    def _parse_imgurl(self, root: ET.Element) -> Optional[str]:
        """Parse image URL from the PAGE XML."""
        img_url_elem = root.find(".//pc:TranskribusMetadata", self.namespace)
        # If from Transkribus, the image URL might be in the TranskribusMetadata element
        image_url = img_url_elem.get("imgUrl") if img_url_elem is not None else None
        if not image_url:
            page_elem = root.find("pc:Page", self.namespace)
            image_url = page_elem.get("imageURL")
        return image_url

    @staticmethod
    def _get_logical_project_parent(f: str) -> str:
        path = PurePosixPath(f)
        parts = path.parts
        if "page" in parts:
            idx = parts.index("page")
            if idx > 0:
                return parts[idx - 1]
        if len(parts) >= 2:
            return parts[-2]
        return parts[0]

    @staticmethod
    def _parse_coords(coords_elem: Optional[ET.Element]) -> List[Tuple[int, int]]:
        """Parse coordinates from a Coords element."""
        if coords_elem is None:
            return []

        points_str = coords_elem.get("points", "")
        if not points_str:
            return []

        coords = []
        for point in points_str.split():
            if "," in point:
                x, y = point.split(",")
                coords.append((int(x), int(y)))

        return coords

    def _get_text_equiv(self, element: ET.Element) -> Optional[str]:
        """Extract text from TextEquiv/Unicode element."""
        text_equiv = element.find("pc:TextEquiv/pc:Unicode", self.namespace)
        if text_equiv is not None and text_equiv.text:
            return text_equiv.text
        return None

    @staticmethod
    def _extract_reading_order_from_custom(element: ET.Element) -> int:
        """Extract reading order from custom attribute."""
        custom = element.get("custom", "")
        if "readingOrder" in custom:
            match = re.search(r"readingOrder\s*\{\s*index\s*:\s*(\d+)", custom)
            if match:
                return int(match.group(1))
        return 0
