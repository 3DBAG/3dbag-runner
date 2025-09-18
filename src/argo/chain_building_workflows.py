from hera.workflows import Step, Steps, Parameter
from hera.workflows.models import TemplateRef, ValueFrom
from argo.argodefaults import get_workflow_template, argo_worker
from typing import Any  # added for mypy suppression


@argo_worker(outputs=[
    Parameter(name="footprints", value_from=ValueFrom(path="/workflow/params/footprints")),
    Parameter(name="cityjson_destination", value_from=ValueFrom(path="/workflow/params/cityjson_destination")),
    Parameter(name="validation_input", value_from=ValueFrom(path="/workflow/params/validation_input")),
    Parameter(name="validation_output", value_from=ValueFrom(path="/workflow/params/validation_output")),
    Parameter(name="height_source", value_from=ValueFrom(path="/workflow/params/height_source")),
    Parameter(name="height_destination", value_from=ValueFrom(path="/workflow/params/height_destination")),
    Parameter(name="geluid_source", value_from=ValueFrom(path="/workflow/params/geluid_source")),
    Parameter(name="geluid_destination", value_from=ValueFrom(path="/workflow/params/geluid_destination")),
    Parameter(name="year_geluid", value_from=ValueFrom(path="/workflow/params/year_geluid")),
    Parameter(name="tyler_source", value_from=ValueFrom(path="/workflow/params/tyler_source")),
    Parameter(name="tyler_intermediate", value_from=ValueFrom(path="/workflow/params/tyler_intermediate")),
    Parameter(name="tyler_destination", value_from=ValueFrom(path="/workflow/params/tyler_destination")),
    Parameter(name="height_split_destination", value_from=ValueFrom(path="/workflow/params/height_split_destination")),
    Parameter(name="geluid_split_destination", value_from=ValueFrom(path="/workflow/params/geluid_split_destination")),
    Parameter(name="cityjson_zipped_destination", value_from=ValueFrom(path="/workflow/params/cityjson_zipped_destination")),
])
def generate_parameters(folder: str, year: str) -> None:
    import json
    import logging
    import os
    from roofhelper.io import SchemeFileHandler
    from roofhelper.defaultlogging import setup_logging

    logger = setup_logging(logging.INFO)

    # Parse the folder URI to determine the scheme
    handler = SchemeFileHandler()

    year_geluid = int(year) + 1  # Don't ask, they've decided in the past it's based on peildatum, which is the first day of next year.

    # Generate all the paths using the appropriate navigate function
    parameters = {
        "footprints": handler.navigate(folder, f"{year}/bag{year}.gpkg"),
        "cityjson_destination": handler.navigate(folder, f"{year}/cityjsonraw"),
        "validation_input": handler.navigate(folder, f"{year}/cityjsonraw"),
        "validation_output": handler.navigate(folder, f"{year}/cityjson"),
        "height_source": handler.navigate(folder, f"{year}/cityjson"),
        "height_destination": handler.navigate(folder, f"{year}/hoogte/{year}_3d_hoogtestatistieken_gebouwen.zip"),
        "geluid_source": handler.navigate(folder, f"{year}/cityjson"),
        "geluid_destination": handler.navigate(folder, f"{year}/geluid/{year_geluid}_NL_3d_geluid_gebouwen.zip"),
        "year_geluid": str(year_geluid),
        "tyler_source": handler.navigate(folder, f"{year}/cityjson"),
        "tyler_intermediate": handler.navigate(folder, f"{year}/intermediate"),
        "tyler_destination": handler.navigate(folder, f"{year}/tyler"),
        "height_split_destination": handler.navigate(folder, f"{year}/hoogte"),
        "geluid_split_destination": handler.navigate(folder, f"{year}/geluid"),
        "cityjson_zipped_destination": handler.navigate(folder, f"{year}/cityjson-zipped")
    }

    logger.info(f"Generated parameters: {json.dumps(parameters, indent=2)}")

    # Write outputs to files for Argo output parameters
    os.makedirs("/workflow/params", exist_ok=True)
    for k, v in parameters.items():
        with open(f"/workflow/params/{k}", "w") as f:
            f.write(str(v))


def generate_workflow() -> None:
    """
    Create a chained workflow that executes workflows in the following order:
    1. Parallel: ingest_createbagdb, prepare_indexlazfiles
    2. transform_roofer
    3. validate_fixcityjson
    4. Parallel: transform_height, transform_geluid, transform_tyler
    5. Parallel: transform_splitgpkg for height and geluid outputs, transform_zip_cityjson
    """
    with get_workflow_template(__name__.split('.')[-1],
                               entrypoint="chain-steps",
                               arguments=[
                                   # Base parameters
                                   Parameter(name="folder", default="azure://<sas>"),
                                   Parameter(name="year", default="2021"),

                                   # Point cloud data sources
                                   Parameter(name="dsm", default="azure://<sas>"),
                                   Parameter(name="ahn3", default="azure://<sas>"),
                                   Parameter(name="ahn4", default="azure://<sas>")
    ]) as w:

        with Steps(name="chain-steps") as s:
            params_step: Any = generate_parameters(arguments={  # type: ignore
                "folder": w.get_parameter("folder"),
                "year": w.get_parameter("year")
            })

            # Step 1: Run ingest_createbagdb and prepare_indexlazfiles (3x) in parallel
            with s.parallel():
                Step(
                    name="ingest-createbagdb",
                    template_ref=TemplateRef(
                        name="ingest-createbagdb",
                        template="createbagdbdag",
                        cluster_scope=False
                    ),
                    arguments={
                        "destination": "{{steps.%s.outputs.parameters.footprints}}" % params_step.name,
                        "year": w.get_parameter("year")
                    }
                )

                Step(
                    name="prepare-indexlazfiles-dsm",
                    template_ref=TemplateRef(
                        name="prepare-indexlazfiles",
                        template="pointclouddbdag",
                        cluster_scope=False
                    ),
                    arguments={
                        "destination": w.get_parameter("dsm")
                    }
                )

                Step(
                    name="prepare-indexlazfiles-ahn3",
                    template_ref=TemplateRef(
                        name="prepare-indexlazfiles",
                        template="pointclouddbdag",
                        cluster_scope=False
                    ),
                    arguments={
                        "destination": w.get_parameter("ahn3")
                    }
                )

                Step(
                    name="prepare-indexlazfiles-ahn4",
                    template_ref=TemplateRef(
                        name="prepare-indexlazfiles",
                        template="pointclouddbdag",
                        cluster_scope=False
                    ),
                    arguments={
                        "destination": w.get_parameter("ahn4")
                    }
                )

            # Step 2: Run transform_roofer after the parallel tasks complete
            Step(
                name="transform-roofer",
                template_ref=TemplateRef(
                    name="transform-roofer",
                    template="rooferdag",
                    cluster_scope=False
                ),
                arguments={
                    "footprints": "{{steps.%s.outputs.parameters.footprints}}" % params_step.name,
                    "year": w.get_parameter("year"),
                    "dsm": w.get_parameter("dsm"),
                    "ahn4": w.get_parameter("ahn4"),
                    "ahn3": w.get_parameter("ahn3"),
                    "destination": "{{steps.%s.outputs.parameters.cityjson_destination}}" % params_step.name,
                    "workercount": "5"
                }
            )

            # Step 3: Run validate_fixcityjson after transform_roofer
            Step(
                name="validate-fixcityjson",
                template_ref=TemplateRef(
                    name="validate-fixcityjson",
                    template="maindag",
                    cluster_scope=False
                ),
                arguments={
                    "input": "{{steps.%s.outputs.parameters.validation_input}}" % params_step.name,
                    "output": "{{steps.%s.outputs.parameters.validation_output}}" % params_step.name
                }
            )

            # Step 4: Run transform_height, transform_geluid, and transform_tyler in parallel
            with s.parallel():
                Step(
                    name="transform-height",
                    template_ref=TemplateRef(
                        name="transform-height",
                        template="heightdag",
                        cluster_scope=False
                    ),
                    arguments={
                        "source": "{{steps.%s.outputs.parameters.height_source}}" % params_step.name,
                        "destination": "{{steps.%s.outputs.parameters.height_destination}}" % params_step.name,
                        "year": w.get_parameter("year")
                    }
                )

                Step(
                    name="transform-geluid",
                    template_ref=TemplateRef(
                        name="transform-geluid",
                        template="geluiddag",
                        cluster_scope=False
                    ),
                    arguments={
                        "source": "{{steps.%s.outputs.parameters.geluid_source}}" % params_step.name,
                        "destination": "{{steps.%s.outputs.parameters.geluid_destination}}" % params_step.name,
                        "year": w.get_parameter("year")
                    }
                )

                Step(
                    name="transform-tyler",
                    template_ref=TemplateRef(
                        name="transform-tyler",
                        template="tylerdag",
                        cluster_scope=False
                    ),
                    arguments={
                        "source": "{{steps.%s.outputs.parameters.tyler_source}}" % params_step.name,
                        "intermediate": "{{steps.%s.outputs.parameters.tyler_intermediate}}" % params_step.name,
                        "destination": "{{steps.%s.outputs.parameters.tyler_destination}}" % params_step.name,
                        "mode": "buildings",
                        "workercount": "5"
                    }
                )

            # Step 5: Split the GPKG files and zip CityJSON files in parallel
            with s.parallel():
                Step(
                    name="transform-splitgpkg-height",
                    template_ref=TemplateRef(
                        name="transform-splitgpkg",
                        template="splitgpkgdag",
                        cluster_scope=False
                    ),
                    arguments={
                        "source": "{{steps.%s.outputs.parameters.height_destination}}" % params_step.name,
                        "destination": "{{steps.%s.outputs.parameters.height_split_destination}}" % params_step.name,
                        "year": w.get_parameter("year"),
                        "postfix": "hoogtestatistieken_gebouwen"
                    }
                )

                Step(
                    name="transform-splitgpkg-geluid",
                    template_ref=TemplateRef(
                        name="transform-splitgpkg",
                        template="splitgpkgdag",
                        cluster_scope=False
                    ),
                    arguments={
                        "source": "{{steps.%s.outputs.parameters.geluid_destination}}" % params_step.name,
                        "destination": "{{steps.%s.outputs.parameters.geluid_split_destination}}" % params_step.name,
                        # Use year_geluid (year + 1) for geluid split step
                        "year": "{{steps.%s.outputs.parameters.year_geluid}}" % params_step.name,
                        "postfix": "3dgeluid_gebouwen"
                    }
                )

                Step(
                    name="transform-zip-cityjson",
                    template_ref=TemplateRef(
                        name="transform-zip-cityjson",
                        template="zipdag",
                        cluster_scope=False
                    ),
                    arguments={
                        "source": "{{steps.%s.outputs.parameters.validation_output}}" % params_step.name,
                        "destination": "{{steps.%s.outputs.parameters.cityjson_zipped_destination}}" % params_step.name
                    }
                )

        with open(f"generated/{w.name}.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()
