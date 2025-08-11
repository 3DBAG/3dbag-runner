from typing import Callable, Literal, ParamSpec, Sequence, TypeVar, Unpack, cast
from typing_extensions import TypedDict
from pathlib import Path


from hera.workflows import script, EmptyDirVolume, Artifact, SecretVolume
from hera.workflows.models.io.k8s.api.core.v1 import Toleration, ResourceRequirements, Affinity, NodeAffinity, NodeSelector, NodeSelectorTerm, NodeSelectorRequirement
from hera.workflows.models.io.k8s.apimachinery.pkg.api.resource import Quantity
from hera.workflows.models.io.argoproj.workflow.v1alpha1 import RetryStrategy

DEFAULT_TOLERATIONS = Toleration(
    key="kubernetes.azure.com/scalesetpriority",
    operator="Equal",  # or "Exists"
    value="spot",
    effect="NoSchedule"
)

DEFAULT_NODE_SELECTOR = {"agentpool": "argo"}

# Define affinity
DEFAULT_AFFINITY = Affinity(
    node_affinity=NodeAffinity(
        required_during_scheduling_ignored_during_execution=NodeSelector(
            node_selector_terms=[
                NodeSelectorTerm(
                    match_expressions=[
                        NodeSelectorRequirement(
                            key="agentpool",
                            operator="In",
                            values=["argo"]
                        )
                    ]
                )
            ]
        )
    )
)

# Load default image from .default_image file if it exists, otherwise use fallback


def _get_default_image() -> str:
    default_image_file = Path(__file__).parent.parent.parent / ".default_image"
    if default_image_file.exists():
        return default_image_file.read_text().strip()
    return "acrexample.azurecr.io/container:master"


DEFAULT_IMAGE = _get_default_image()
DEFAULT_VOLUMES = [EmptyDirVolume(name="workflow", mount_path="/workflow")]
MEMORY_EMPTY_DIR = [EmptyDirVolume(name="workflow", mount_path="/workflow", medium="Memory")]
DEFAULT_COMMAND = ["/app/.venv/bin/python"]
DEFAULT_RETRY_STRATEGY = RetryStrategy(
    limit=1  # type: ignore
)

# Default sizes
SIZE_D32 = ResourceRequirements(
    requests=cast(dict[str, Quantity], {"cpu": "30", "memory": "110Gi"}),
    limits=cast(dict[str, Quantity], {"cpu": "32", "memory": "128Gi"}),
)

SIZE_D16 = ResourceRequirements(
    requests=cast(dict[str, Quantity], {"cpu": "14", "memory": "50Gi"}),
    limits=cast(dict[str, Quantity], {"cpu": "16", "memory": "64Gi"}),
)

SIZE_D8 = ResourceRequirements(
    requests=cast(dict[str, Quantity], {"cpu": "7", "memory": "28Gi"}),
    limits=cast(dict[str, Quantity], {"cpu": "8", "memory": "32Gi"}),
)

SIZE_D4 = ResourceRequirements(
    requests=cast(dict[str, Quantity], {"cpu": "3", "memory": "12Gi"}),
    limits=cast(dict[str, Quantity], {"cpu": "4", "memory": "16Gi"}),
)

SIZE_D2 = ResourceRequirements(
    requests=cast(dict[str, Quantity], {"cpu": "1", "memory": "4Gi"}),
    limits=cast(dict[str, Quantity], {"cpu": "2", "memory": "8Gi"}),
)


class _ScriptKwargs(TypedDict, total=False):
    image: str
    volumes: Sequence[EmptyDirVolume | SecretVolume]
    command: list[str] | None
    node_selector: dict[str, str]
    tolerations: list[Toleration]
    resources: ResourceRequirements
    affinity: Affinity
    image_pull_policy: Literal["Always", "IfNotPresent", "Never"]
    outputs: list[Artifact] | Artifact
    inputs: list[Artifact] | Artifact
    retry_strategy: RetryStrategy


# Typing
P = ParamSpec("P")
R = TypeVar("R")


def argo_worker(**custom_kwargs: Unpack[_ScriptKwargs]) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    A decorator that applies Hera's @script with defaults,
    allowing overrides via keyword arguments.
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        merged_kwargs: _ScriptKwargs = {
            "image": DEFAULT_IMAGE,
            "volumes": DEFAULT_VOLUMES,
            "command": DEFAULT_COMMAND,
            "node_selector": DEFAULT_NODE_SELECTOR,
            "tolerations": [DEFAULT_TOLERATIONS],
            "resources": SIZE_D32,
            "affinity": DEFAULT_AFFINITY,
            "image_pull_policy": "Always",
            "retry_strategy": DEFAULT_RETRY_STRATEGY,
            **custom_kwargs,
        }
        return script(**merged_kwargs)(func)
    return decorator


def default_worker(**custom_kwargs: Unpack[_ScriptKwargs]) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    A decorator that applies Hera's @script with defaults,
    allowing overrides via keyword arguments.
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        merged_kwargs: _ScriptKwargs = {
            "image": DEFAULT_IMAGE,
            "volumes": DEFAULT_VOLUMES,
            "command": DEFAULT_COMMAND,
            "image_pull_policy": "Always",
            "retry_strategy": DEFAULT_RETRY_STRATEGY,
            **custom_kwargs,
        }
        return script(**merged_kwargs)(func)
    return decorator
