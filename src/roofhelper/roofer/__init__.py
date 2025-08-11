from dataclasses import asdict
from typing import Any
import toml

from .RooferConfig import RooferConfig
from .PointCloudConfig import PointcloudConfig


def convert_keys_to_kebab_case(data_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Python does not allow for kebab casing in toml files, but it is allowed in roofer toml configuration files,
    This function temporary fixes the problem by converting specific dictionary values to kebab casing.
    """
    new_dict: dict[str, Any] = {}
    for key, value in data_dict.items():
        new_key = key.replace('_', '-')
        if isinstance(value, dict):
            if new_key != "output-attributes":
                new_dict[new_key] = convert_keys_to_kebab_case(value)
            else:
                new_dict[new_key] = value
        elif isinstance(value, list):
            new_list = []
            for item in value:
                if isinstance(item, dict):
                    new_list.append(convert_keys_to_kebab_case(item))
                else:
                    new_list.append(item)
            new_dict[new_key] = new_list
        else:
            if new_key != "force-lod11" and new_key != "select-only-for-date":
                new_dict[new_key] = value
            else:
                new_dict[key] = value

    return new_dict


def roofer_config_generate(footprint_source: str, pointclouds: list[PointcloudConfig], bbox: list[float], yoc_column: str, id_attribute: str, output_directory: str, ) -> str:
    """ Create a config file for Roofer, please use this function instead of the RooferConfig dataclass as it's not immediately compatible with Roofer """
    configuration: RooferConfig = RooferConfig(polygon_source=footprint_source,
                                               yoc_attribute=yoc_column,
                                               id_attribute=id_attribute,
                                               pointclouds=pointclouds,
                                               box=bbox,
                                               output_directory=output_directory,
                                               force_lod11_attribute="force_low_lod")

    data_as_dict = asdict(configuration)
    transformed_dict = convert_keys_to_kebab_case(data_as_dict)
    toml_string: str = toml.dumps(transformed_dict)
    return toml_string
