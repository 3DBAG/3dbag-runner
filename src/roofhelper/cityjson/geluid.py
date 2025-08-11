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
    tijdstip_eind_registratie_lv: str
    coverage: float
    dd_id: int
    pand_deel_id: int

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
    Subtract base from value, but return None if either is None
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
        building_lod0.dak_type = attributes["rf_roof_type"]
        building_lod0.h_maaiveld = attributes["rf_h_ground"]
        building_lod0.identificatie = attributes["identificatie"]
        building_lod0.pw_actueel = attributes["rf_pc_year"]
        building_lod0.pw_bron = attributes["rf_pc_source"]
        building_lod0.oorspronkelijk_bouwjaar = attributes["oorspronkelijkBouwjaar"]
        building_lod0.bagpandid = attributes["identificatie"].replace("NL.IMBAG.Pand.", "")
        building_lod0.kwaliteits_klasse = "keep"
        building_lod0.document_nummer = attributes["documentNummer"]
        building_lod0.document_datum = attributes["documentDatum"]
        building_lod0.status = attributes["status"]
        building_lod0.tijdstip_registratie_lv = attributes["tijdstipRegistratieLV"]
        building_lod0.tijdstip_eind_registratie_lv = attributes["tijdstipEindRegistratieLV"]

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
        # ID
        # 'id': 'int',
        'identificatie': 'str',
        'pand_deel_id': 'int',
        'dd_id': 'int',
        'h_maaiveld': 'float',
        'roof_elevation_50p': 'float',
        'roof_elevation_70p': 'float',
        'roof_elevation_min': 'float',
        'roof_elevation_max': 'float',
        # 'dd_data_coverage': 'float',
        'dak_type': 'str',  # Enum conversion
        'pw_datum': 'str',
        # 'pw_actueel': 'int',
        'pw_bron': 'str',
        # 'reconstructie_methode': 'str',
        # 'versie_methode': 'str',
        # 'kas_warenhuis': 'bool',
        # 'ondergronds_type': 'int',
        # 'kwaliteits_klasse': 'str',
        # 'objectid': 'int',
        # 'aanduidingrecordinactief': 'float',
        # 'aanduidingrecordcorrectie': 'float',
        # 'officieel': 'float',
        # 'inonderzoek': 'str',
        'documentnummer': 'str',
        'documentdatum': 'str',
        'pandstatus': 'str',
        'bouwjaar': 'int',
        'begindatumtijdvakgeldigheid': 'str',
        'einddatumtijdvakgeldigheid': 'str',
        'lod': 'str',
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
    if b.tijdstip_eind_registratie_lv is None:
        b.tijdstip_eind_registratie_lv = '2199/12/31 00:00:00'

    return {
        'geometry': mapping(polygon),
        'properties': {
            'identificatie': b.identificatie.replace("NL.IMBAG.Pand.", ""),
            'pand_deel_id': b.pand_deel_id,
            'dd_id': b.dd_id,
            'h_maaiveld': b.h_maaiveld,
            'roof_elevation_50p': b.roof_elevation_50p,
            'roof_elevation_70p': b.roof_elevation_70p,
            'roof_elevation_min': b.roof_elevation_min,
            'roof_elevation_max': b.roof_elevation_max,
            'dak_type': b.dak_type,  # nummer
            'pw_datum': b.pw_actueel,
            # 'pw_actueel': 2,
            'pw_bron': b.pw_bron,
            # 'versie_methode': '0ed1dc74b3146b10d4acb5196fde31348e887b06',
            # 'kas_warenhuis': False,
            # 'kwaliteits_klasse': "keep",
            'documentnummer': b.document_nummer,
            'documentdatum': b.document_datum,
            'pandstatus': b.status,
            'bouwjaar': b.oorspronkelijk_bouwjaar,
            'begindatumtijdvakgeldigheid': b.tijdstip_registratie_lv,
            'einddatumtijdvakgeldigheid': b.tijdstip_eind_registratie_lv,
            'lod': b.lod,
        }
    }
