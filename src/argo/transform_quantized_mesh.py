from hera.workflows import DAG, Script, Parameter
from argo.argodefaults import argo_worker, get_workflow_template


@argo_worker()
def workerfunc(source: str, destination: str) -> None:
    from main import create_quantized_mesh
    from pathlib import Path

    create_quantized_mesh(
        source=source,
        destination=destination,
        temporary_directory=Path("/workflow"),
        parallel=8
    )


def generate_workflow() -> None:
    with get_workflow_template(__name__.split('.')[-1],
                               entrypoint="dag",
                               arguments=[
                                   Parameter(name="source", default="azure://<sas>"),
                                   Parameter(name="destination", default="azure://<sas>")
    ]) as w:
        with DAG(name="dag", inputs=[Parameter(name="source"), Parameter(name="destination")]):
            queue: Script = workerfunc(arguments={  # type: ignore  # noqa: F841
                "source": "{{inputs.parameters.source}}",
                "destination": "{{inputs.parameters.destination}}",
            })  # type: ignore

        with open(f"generated/{w.name}.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()
