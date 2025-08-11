from itertools import islice
import logging
import subprocess
from typing import Generator, Iterable, List, TypeVar

T = TypeVar('T')  # Generic type variable


def chunked(iterable: Iterable[T], size: int) -> Generator[list[T], None, None]:
    """Yield successive chunks (as lists) from an iterable."""
    iterator = iter(iterable)
    while chunk := list(islice(iterator, size)):
        yield chunk


log = logging.getLogger()


def run_with_retries(
    cmd: List[str],
    timeout: int,
    max_attempts: int = 2,
    capture_output: bool = False,
    check: bool = True,
    text: bool = False
) -> subprocess.CompletedProcess[str]:
    for attempt in range(1, max_attempts + 1):
        try:
            log.info("Attempt %d/%d: %s", attempt, max_attempts, " ".join(cmd))
            result = subprocess.run(
                cmd,
                check=check,
                timeout=timeout,
                text=text,
                capture_output=capture_output,
            )
            log.info("Finished command: %s ", " ".join(cmd))
            return result
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("Attempt %d failed: %s", attempt, exc)

    log.error("All %d attempts failed.", max_attempts)
    raise
