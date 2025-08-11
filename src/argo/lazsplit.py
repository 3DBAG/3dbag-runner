from hera.workflows import DAG, WorkflowTemplate, Parameter, Artifact, Script
from argo.argodefaults import default_worker


@default_worker(outputs=Artifact(name="queue", path="/workflow/queue.json"))
def workerfunc(source: str, destination: str, gridsize: int) -> None:
    from main import pointcloudsplit
    from pathlib import Path
    pointcloudsplit(source, destination, gridsize, Path("/workflow"))


def generate_workflow() -> None:
    with WorkflowTemplate(name="lazsplit",
                          generate_name="lazsplit-",
                          entrypoint="maindag",
                          namespace="argo",
                          service_account_name="workflow-runner",
                          image_pull_secrets="acrdddprodman",
                          arguments=[
                              Parameter(name="source", default="https://storageaccount.blob.core.windows.net/container/pointcloud?<sas>"),
                              Parameter(name="destination", default="https://storageaccount.blob.core.windows.net/container/output?<sas>"),
                              Parameter(name="grid-size", default="250"),
                          ]) as w:
        with DAG(name="maindag"):
            worker: Script = workerfunc(arguments={"source": w.get_parameter("source"),  # type: ignore  # noqa: F841
                                                   "destination": w.get_parameter("destination"),
                                                   "gridsize": w.get_parameter("grid-size")})  # type: ignore

        with open("generated/lazsplit.yaml", "w") as f:
            w.to_yaml(f)


if __name__ == "__main__":
    generate_workflow()
