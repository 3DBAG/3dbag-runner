from dataclasses import dataclass
from datetime import datetime
from typing import Any
import copy

PDOK_DELIVERY_BASE_SCHEMA: dict[str, Any] = {
    'geometry': 'Polygon',
    'properties': {
        'bladnr': 'str',
        'jaargang_luchtfoto': 'int',
        'download_size_bytes': 'int',
        'download_link': 'str',
        'startdatum': 'datetime',
        'einddatum': 'datetime',
    }
}


def createBaseSchema(extend: dict[str, str]) -> dict[Any, Any]:
    result = copy.deepcopy(PDOK_DELIVERY_BASE_SCHEMA)
    result["properties"].update(extend)
    return result


@dataclass
class PdokDeliveryProperties:
    """Properties for PDOK delivery features."""
    bladnr: str
    download_size_bytes: int
    download_link: str
    startdatum: datetime
    einddatum: datetime
