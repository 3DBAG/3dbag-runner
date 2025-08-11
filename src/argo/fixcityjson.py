from hera.workflows import DAG, WorkflowTemplate, Parameter, Script
from argo.argodefaults import default_worker


@default_worker()
def workerfunc(input: str, output: str) -> None:
    import json
    import logging
    from pathlib import Path

    from io import BytesIO
    from typing import Any, Optional
    from roofhelper.io import SchemeFileHandler
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from roofhelper.defautlogging import setup_logging

    log = setup_logging(logging.INFO)

    ELEV_KEYS = [
        "rf_roof_elevation_50p",
        "rf_roof_elevation_70p",
        "rf_roof_elevation_max",
        "rf_roof_elevation_min",
    ]

    def should_reset(vals: dict[Any, Any], name: str) -> bool:
        """
        Given a dict of float-or-None for each key,
        return True if ANY defined value is >1000 or <150.
        """
        for v in vals.values():
            if v is None:
                continue
            if v > 1000 or v < -150:
                log.info(f"Reset in {name}")
                return True
        return False

    def parse_float(val: Any) -> Optional[float]:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def sanitize_dict(d: dict[Any, Any], name: str) -> None:
        """
        Inspect ELEV_KEYS in dict d; if any value out of [150,1000],
        set all four keys in d to 0.0.
        """
        vals = {k: parse_float(d.get(k)) for k in ELEV_KEYS}
        if should_reset(vals, name):
            for k in ELEV_KEYS:
                d[k] = 0.0

    def sanitize_cityjson(data: dict[Any, Any], name: str) -> None:
        city_objects = data.get("CityObjects", {})
        for obj in city_objects.values():
            # 1) Sanitize top-level attributes
            attrs = obj.get("attributes", {})
            sanitize_dict(attrs, name)

            # 2) Sanitize inside each geometry's semantics.surfaces
            for geom in obj.get("geometry", []):
                sem = geom.get("semantics", {})
                for surf in sem.get("surfaces", []):
                    # Only sanitize if any of the elevation keys appear
                    if any(k in surf for k in ELEV_KEYS):
                        sanitize_dict(surf, name)

    handler = SchemeFileHandler(Path("/workflow"))

    def _worker(name: str, uri: str) -> None:
        city_json = handler.get_bytes(uri).decode()
        log.info(f"Downloading {uri}")
        data = json.loads(city_json)

        # Sanitize elevations
        sanitize_cityjson(data, name)

        stream = BytesIO()
        json_str = json.dumps(data)
        stream.write(json_str.encode('utf-8'))
        stream.seek(0)

        handler.upload_bytes_directory(stream, output, name)
        log.info(f"Uploaded {name}")

    files = (entry for entry in handler.list_entries_shallow(input, regex="(?i)^.*city\\.json$") if entry.is_file)
    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = [pool.submit(_worker, entry.name, entry.full_uri) for entry in files]

        for future in as_completed(futures):
            future.result()


def generate_workflow() -> None:
    with WorkflowTemplate(name="fixcityjson",
                          generate_name="fixcityjson-",
                          entrypoint="maindag",
                          namespace="argo",
                          service_account_name="workflow-runner",
                          image_pull_secrets="acrdddprodman",
                          arguments=[
                              Parameter(name="input", default="azure://<sas>"),
                              Parameter(name="output", default="azure://<sas>"),
                          ]) as w:
        with DAG(name="maindag"):
            worker: Script = workerfunc(arguments={"input": w.get_parameter("input"),  # type: ignore  # noqa: F841
                                                   "output": w.get_parameter("output")})  # type: ignore

        with open("generated/fixcityjson.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()
