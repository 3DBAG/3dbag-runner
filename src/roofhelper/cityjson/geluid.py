import copy
import json
from pathlib import Path
from typing import Any, Final, Generator, Optional

from shapely.geometry import Polygon, mapping


class Building:
    footprint: list[list[float]]
    h_maaiveld: float
    identificatie: str
    roof_elevation_50p: float
    roof_elevation_70p: float
    roof_elevation_min: float
    roof_elevation_max: float
    dak_type: str
    kwaliteits_klasse: str
    pw_actueel: int
    pw_bron: str
    pw_date: str
    lod: str
    reconstructie_methode: str
    bagpandid: str
    oorspronkelijk_bouwjaar: str
    fid: int
    document_nummer: str
    document_datum: str
    status: str
    tijdstip_registratie_lv: str
    tijdstip_eind_registratie_lv: Optional[str]
    coverage: float
    dd_id: int
    pand_deel_id: int
    # Additional attributes from HOOGTE_SCHEMA
    begin_geldigheid: Optional[str]
    eind_geldigheid: Optional[str]
    voorkomen_identificatie: int
    rf_success: bool
    rf_h_roof_ridge: Optional[float]
    rf_volume_lod12: float
    rf_volume_lod13: float
    rf_volume_lod22: float
    rf_is_glass_roof: bool
    rf_pc_select: str
    rf_rmse_lod12: float
    rf_rmse_lod13: float
    rf_rmse_lod22: float
    rf_val3dity_lod12: str
    rf_val3dity_lod13: str
    rf_val3dity_lod22: str

    def __str__(self) -> str:
        return (
            f"Building {self.identificatie}:\n"
            f"  BAG ID: {self.bagpandid}\n"
            f"  Bouwjaar: {self.oorspronkelijk_bouwjaar}\n"
            f"  Footprint points: {len(self.footprint)}\n"
            f"  Maaiveld Height: {self.h_maaiveld} m\n"
            f"  Roof Elevation (50%): {self.roof_elevation_50p} m\n"
            f"  Roof Elevation (70%): {self.roof_elevation_70p} m\n"
            f"  Roof Elevation (min): {self.roof_elevation_min} m\n"
            f"  Roof Elevation (max): {self.roof_elevation_max} m\n"
            f"  Roof Type: {self.dak_type}\n"
            f"  Quality Class: {self.kwaliteits_klasse}\n"
            f"  PW Actueel: {self.pw_actueel} (Source: {self.pw_bron})\n"
            f"  LOD: {self.lod}\n"
            f"  Reconstruction Method: {self.reconstructie_methode}"
        )


def safe_subtract(value: Any, base: Optional[float]) -> float:
    """
    Subtract base from value, but return 0.0 if either is None
    or if they’re not numbers.
    """
    try:
        if value is None or base is None:
            return 0.0
        return value - base
    except (TypeError, KeyError):
        return 0.0


def read_height_from_cityjson(cityjsonfile: Path) -> Generator[Building]:
    data = None

    with open(cityjsonfile, "r") as f:
        data = json.load(f)

    scale_x, scale_y, scale_z = data["transform"]["scale"]
    translate_x, translate_y, translate_z = data["transform"]["translate"]

    vertices = data["vertices"]
    cityobjects = data["CityObjects"].items()

    for cityobject in [v for _, v in cityobjects if v["type"] == "Building"]:
        attributes = cityobject["attributes"]
        if attributes["status"] == "Pand ten onrechte opgevoerd":
            continue

        building_lod0 = Building()

        # LOD 0 always consists out of a single surface
        footprint_vertex_idx = cityobject["geometry"][0]["boundaries"][0][0]
        footprint_vertices_raw = [vertices[x] for x in footprint_vertex_idx]
        footprint_vertices_translated = []
        for vertex_raw in footprint_vertices_raw:
            x = vertex_raw[0] * scale_x + translate_x
            y = vertex_raw[1] * scale_y + translate_y
            z = vertex_raw[2] * scale_z + translate_z
            footprint_vertices_translated.append([x, y, z])

        building_lod0.footprint = footprint_vertices_translated
        building_lod0.dak_type = attributes.get("rf_roof_type", "")
        building_lod0.h_maaiveld = attributes.get("rf_h_ground", 0.0)
        building_lod0.identificatie = attributes.get("identificatie", "")
        building_lod0.pw_actueel = attributes.get("rf_pc_year", 0)
        building_lod0.pw_bron = attributes.get("rf_pc_source", "")
        building_lod0.oorspronkelijk_bouwjaar = attributes.get("oorspronkelijkBouwjaar", "")
        building_lod0.bagpandid = attributes.get("identificatie", "").replace("NL.IMBAG.Pand.", "")
        building_lod0.kwaliteits_klasse = "keep"
        building_lod0.document_nummer = attributes.get("documentNummer", "")
        building_lod0.document_datum = attributes.get("documentDatum", "")
        building_lod0.status = attributes.get("status", "")
        building_lod0.tijdstip_registratie_lv = attributes.get("tijdstipRegistratieLV", "")
        building_lod0.tijdstip_eind_registratie_lv = attributes.get("tijdstipEindRegistratieLV")

        # Additional attributes from HOOGTE_SCHEMA
        building_lod0.begin_geldigheid = attributes.get("beginGeldigheid")
        building_lod0.eind_geldigheid = attributes.get("eindGeldigheid")
        building_lod0.voorkomen_identificatie = attributes.get("voorkomenIdentificatie", 0)
        building_lod0.rf_success = attributes.get("rf_success", False)
        building_lod0.rf_h_roof_ridge = attributes.get("rf_h_roof_ridge")
        building_lod0.rf_volume_lod12 = attributes.get("rf_volume_lod12", 0.0)
        building_lod0.rf_volume_lod13 = attributes.get("rf_volume_lod13", 0.0)
        building_lod0.rf_volume_lod22 = attributes.get("rf_volume_lod22", 0.0)
        building_lod0.rf_is_glass_roof = attributes.get("rf_is_glass_roof", False)
        building_lod0.rf_pc_select = attributes.get("rf_pc_select", "")
        building_lod0.rf_rmse_lod12 = attributes.get("rf_rmse_lod12", 0.0)
        building_lod0.rf_rmse_lod13 = attributes.get("rf_rmse_lod13", 0.0)
        building_lod0.rf_rmse_lod22 = attributes.get("rf_rmse_lod22", 0.0)
        building_lod0.rf_val3dity_lod12 = attributes.get("rf_val3dity_lod12", "")
        building_lod0.rf_val3dity_lod13 = attributes.get("rf_val3dity_lod13", "")
        building_lod0.rf_val3dity_lod22 = attributes.get("rf_val3dity_lod22", "")

        # LOD 0 by default
        building_lod0.roof_elevation_50p = safe_subtract(
            attributes.get("rf_roof_elevation_50p"), building_lod0.h_maaiveld
        )
        building_lod0.roof_elevation_70p = safe_subtract(
            attributes.get("rf_roof_elevation_70p"), building_lod0.h_maaiveld
        )
        building_lod0.roof_elevation_min = safe_subtract(
            attributes.get("rf_roof_elevation_min"), building_lod0.h_maaiveld
        )
        building_lod0.roof_elevation_max = safe_subtract(
            attributes.get("rf_roof_elevation_max"), building_lod0.h_maaiveld
        )
        building_lod0.lod = "0"
        building_lod0.reconstructie_methode = "3dgi-lod0"
        building_lod0.dd_id = 0
        building_lod0.pand_deel_id = 0
        has_yielded_lod13 = False
        # Fetch the child for the remaining attributes
        dd_id: int = 0

        if len(cityobject["children"]) > 0:  # lod 1.3 source
            pand_deel_id = 0
            for child_name in cityobject["children"]:
                child_data = data["CityObjects"][child_name]["geometry"]
                for geometry in child_data:
                    if geometry["lod"] == "1.3":
                        boundaries = geometry["boundaries"][0]
                        value_idx = geometry["semantics"]["values"][0]

                        for index, surface in enumerate(geometry["semantics"]["surfaces"]):
                            if surface["type"] == "RoofSurface":  # For each
                                building_lod13 = copy.copy(building_lod0)
                                building_lod13.dd_id = dd_id
                                building_lod13.pand_deel_id = pand_deel_id
                                dd_id += 1
                                surface_footprint_vertex_idx = []
                                for boundary_idx, _ in enumerate(boundaries):
                                    if value_idx[boundary_idx] == index:
                                        surface_footprint_vertex_idx.extend(boundaries[boundary_idx][0])
                                surface_vertices_raw = [vertices[x] for x in surface_footprint_vertex_idx]

                                surface_vertices_translated = []
                                for vertex_raw in surface_vertices_raw:
                                    x = vertex_raw[0] * scale_x + translate_x
                                    y = vertex_raw[1] * scale_y + translate_y
                                    z = vertex_raw[2] * scale_z + translate_z
                                    surface_vertices_translated.append([x, y, z])

                                building_lod13.footprint = surface_vertices_translated
                                if "rf_roof_elevation_50p" in surface:
                                    building_lod13.roof_elevation_50p = safe_subtract(surface["rf_roof_elevation_50p"], building_lod0.h_maaiveld)

                                if "rf_roof_elevation_70p" in surface:
                                    building_lod13.roof_elevation_70p = safe_subtract(surface["rf_roof_elevation_70p"], building_lod0.h_maaiveld)

                                if "rf_roof_elevation_min" in surface:
                                    building_lod13.roof_elevation_min = safe_subtract(surface["rf_roof_elevation_min"], building_lod0.h_maaiveld)

                                if "rf_roof_elevation_max" in surface:
                                    building_lod13.roof_elevation_max = safe_subtract(surface["rf_roof_elevation_max"], building_lod0.h_maaiveld)

                                building_lod13.lod = "1.3"
                                building_lod13.reconstructie_methode = "3dgi-lod13"
                                has_yielded_lod13 = True
                                yield building_lod13
                pand_deel_id += 1

        if not has_yielded_lod13:
            yield building_lod0


# Export to GeoPackage using Fiona
GELUID_SCHEMA: Final = {
    'geometry': 'Polygon',
    'properties': {
        'identificatie': 'str',
        'bagpandid': 'str',
        'h_maaiveld': 'float',
        'roof_elevation_50p': 'float',
        'roof_elevation_70p': 'float',
        'roof_elevation_min': 'float',
        'roof_elevation_max': 'float',
        'dak_type': 'str',
        'kwaliteits_klasse': 'str',
        'pw_actueel': 'int',
        'pw_bron': 'str',
        'lod': 'str',
        'reconstructie_methode': 'str',
        'bouwjaar': 'int'
    }
}


HOOGTE_SCHEMA: Final = {
    'geometry': 'Polygon',
    'properties': {
        'identificatie': 'str',
        'status': 'str',
        'oorspronkelijkbouwjaar': 'int',
        'begingeldigheid': 'str',
        'eindgeldigheid': 'str',
        'voorkomenidentificatie': 'int',
        'rf_success': 'bool',
        'rf_h_ground': 'float',
        'rf_h_roof_min': 'float',
        'rf_h_roof_ridge': 'float',
        'rf_h_roof_50p': 'float',
        'rf_h_roof_70p': 'float',
        'rf_h_roof_max': 'float',
        'rf_volume_lod12': 'float',
        'rf_volume_lod13': 'float',
        'rf_volume_lod22': 'float',
        'rf_roof_type': 'str',
        'rf_is_glass_roof': 'bool',
        'rf_pc_year': 'int',
        'rf_pc_source': 'str',
        'rf_pc_select': 'str',
        'rf_rmse_lod12': 'float',
        'rf_rmse_lod13': 'float',
        'rf_rmse_lod22': 'float',
        'rf_val3dity_lod12': 'str',
        'rf_val3dity_lod13': 'str',
        'rf_val3dity_lod22': 'str',
    }
}


def building_to_gpkg_dict(b: Building) -> dict[Any, Any]:
    polygon = Polygon([(pt[0], pt[1]) for pt in b.footprint])
    return {
        'geometry': mapping(polygon),
        'properties': {
            'identificatie': b.identificatie,
            'bagpandid': b.bagpandid,
            'h_maaiveld': b.h_maaiveld,
            'roof_elevation_50p': b.roof_elevation_50p,
            'roof_elevation_70p': b.roof_elevation_70p,
            'roof_elevation_min': b.roof_elevation_min,
            'roof_elevation_max': b.roof_elevation_max,
            'dak_type': b.dak_type,
            'kwaliteits_klasse': b.kwaliteits_klasse,
            'pw_actueel': b.pw_actueel,
            'pw_bron': b.pw_bron,
            'lod': b.lod,
            'reconstructie_methode': b.reconstructie_methode,
            'bouwjaar': b.oorspronkelijk_bouwjaar
        }
    }


def building_to_hoogte_gpkg_dict(b: Building) -> dict[Any, Any]:
    polygon = Polygon([(pt[0], pt[1]) for pt in b.footprint])

    return {
        'geometry': mapping(polygon),
        'properties': {
            'identificatie': b.identificatie.replace("NL.IMBAG.Pand.", ""),
            'status': b.status,
            'oorspronkelijkbouwjaar': b.oorspronkelijk_bouwjaar,
            'begingeldigheid': b.begin_geldigheid,
            'eindgeldigheid': b.eind_geldigheid,
            'voorkomenidentificatie': b.voorkomen_identificatie,
            'rf_success': b.rf_success,
            'rf_h_ground': b.h_maaiveld,
            'rf_h_roof_min': b.roof_elevation_min,
            'rf_h_roof_ridge': b.rf_h_roof_ridge,
            'rf_h_roof_50p': b.roof_elevation_50p,
            'rf_h_roof_70p': b.roof_elevation_70p,
            'rf_h_roof_max': b.roof_elevation_max,
            'rf_volume_lod12': b.rf_volume_lod12,
            'rf_volume_lod13': b.rf_volume_lod13,
            'rf_volume_lod22': b.rf_volume_lod22,
            'rf_roof_type': b.dak_type,
            'rf_is_glass_roof': b.rf_is_glass_roof,
            'rf_pc_year': b.pw_actueel,
            'rf_pc_source': b.pw_bron,
            'rf_pc_select': b.rf_pc_select,
            'rf_rmse_lod12': b.rf_rmse_lod12,
            'rf_rmse_lod13': b.rf_rmse_lod13,
            'rf_rmse_lod22': b.rf_rmse_lod22,
            'rf_val3dity_lod12': b.rf_val3dity_lod12,
            'rf_val3dity_lod13': b.rf_val3dity_lod13,
            'rf_val3dity_lod22': b.rf_val3dity_lod22,
        }
    }
