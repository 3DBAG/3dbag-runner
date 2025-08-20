"""
Script to remove buildings from CityJSON files in Azure storage.

This script:
1. Lists all .cityjson files from a given Azure URI
2. Downloads and processes files in parallel
3. Removes all CityObjects with type "Building"
4. Uploads the modified files back to Azure storage
5. All operations are done in memory for efficiency
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Any, Dict

from roofhelper.defaultlogging import setup_logging
from roofhelper.io import SchemeFileHandler, EntryProperties

log = setup_logging(logging.INFO)


def remove_buildings_from_cityjson(cityjson_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove all CityObjects with type "Building" from CityJSON data.

    Args:
        cityjson_data: Parsed CityJSON data as dictionary

    Returns:
        Modified CityJSON data with buildings removed
    """
    if "CityObjects" not in cityjson_data:
        log.warning("No CityObjects found in CityJSON data")
        return cityjson_data

    # Count original objects
    original_count = len(cityjson_data["CityObjects"])

    # Filter out buildings and collect their children
    buildings_to_remove = set()
    children_to_remove = set()

    # First pass: identify buildings and collect their children
    for obj_id, obj_data in cityjson_data["CityObjects"].items():
        type = obj_data["attributes"].get("3df_class")
        if type == "Building":
            buildings_to_remove.add(obj_id)
            # Also collect children of buildings
            if "children" in obj_data:
                children_to_remove.update(obj_data["children"])

    # Second pass: also remove any objects that are children of buildings
    for obj_id, obj_data in cityjson_data["CityObjects"].items():
        if "parents" in obj_data:
            # If any parent is a building, this object should be removed
            if any(parent in buildings_to_remove for parent in obj_data["parents"]):
                children_to_remove.add(obj_id)

    # Remove all identified objects
    all_to_remove = buildings_to_remove.union(children_to_remove)

    for obj_id in all_to_remove:
        cityjson_data["CityObjects"].pop(obj_id, None)

    # Clean up any remaining parent/children references
    for obj_data in cityjson_data["CityObjects"].values():
        if "children" in obj_data:
            obj_data["children"] = [child for child in obj_data["children"] if child not in all_to_remove]
            if not obj_data["children"]:
                del obj_data["children"]

        if "parents" in obj_data:
            obj_data["parents"] = [parent for parent in obj_data["parents"] if parent not in all_to_remove]
            if not obj_data["parents"]:
                del obj_data["parents"]

    final_count = len(cityjson_data["CityObjects"])
    buildings_removed = len(buildings_to_remove)
    children_removed = len(children_to_remove)

    log.info(
        f"Removed {buildings_removed} buildings and {children_removed} related objects. "
        f"Objects: {original_count} -> {final_count}"
    )

    return cityjson_data


def process_cityjson_file(entry: EntryProperties, destination_base: str) -> bool:
    """
    Process a single CityJSON file: download, remove buildings, upload.

    Args:
        entry: EntryProperties object representing the CityJSON file to process
        destination_base: Base destination URI

    Returns:
        True if successful, False otherwise
    """
    try:
        log.info(f"Processing {entry.name}")
        file_handler = SchemeFileHandler()
        # Download file content to memory
        content = file_handler.get_bytes(entry.full_uri)

        # Parse JSON
        try:
            cityjson_data = json.loads(content.decode('utf-8'))
        except json.JSONDecodeError as e:
            log.error(f"Failed to parse JSON in {entry.name}: {e}")
            return False

        # Remove buildings
        modified_data = remove_buildings_from_cityjson(cityjson_data)

        # Convert back to JSON
        modified_content = json.dumps(modified_data, separators=(',', ':')).encode('utf-8')

        # Upload modified content
        file_handler.upload_bytes_directory(BytesIO(modified_content), destination_base, entry.name)

        log.info(f"Successfully processed {entry.name} -> {destination_base}")
        return True

    except Exception as e:
        log.error(f"Error processing {entry.name}: {e}")
        return False


def main() -> None:
    """Main function to orchestrate the building removal process."""
    parser = argparse.ArgumentParser(
        description="Remove buildings from CityJSON files in Azure storage"
    )
    parser.add_argument(
        "--source",
        type=str,
        required=True,
        help="Source Azure URI containing CityJSON files (e.g., azure://https://account.blob.core.windows.net/container/path?sas)"
    )
    parser.add_argument(
        "--destination",
        type=str,
        required=True,
        help="Destination Azure URI for modified files (e.g., azure://https://account.blob.core.windows.net/container/output?sas)"
    )

    args = parser.parse_args()

    # Initialize file handler
    file_handler = SchemeFileHandler()

    try:
        # Find all CityJSON files

        cityjson_files = [x for x in file_handler.list_entries_shallow(args.source, regex=r'.*\.city\.json$') if x.is_file]

        if not cityjson_files:
            log.warning("No CityJSON files found")
            return

        log.info(f"Starting processing of {len(cityjson_files)} files")

        # Process files in parallel
        successful = 0
        failed = 0

        with ThreadPoolExecutor() as executor:
            # Submit all tasks
            future_to_file = {
                executor.submit(process_cityjson_file, file_path, args.destination): file_path
                for file_path in cityjson_files
            }

            # Process completed tasks
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                try:
                    success = future.result()
                    if success:
                        successful += 1
                    else:
                        failed += 1
                except Exception as e:
                    log.error(f"Unexpected error processing {file_path}: {e}")
                    failed += 1

        log.info(f"Processing complete. Successful: {successful}, Failed: {failed}")

        if failed > 0:
            sys.exit(1)

    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
