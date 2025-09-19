from itertools import islice
import logging
import subprocess
from typing import Generator, Iterable, List, TypeVar, Optional

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
    """Run a subprocess with retries and a timeout.

    Args:
        cmd: Command argv to execute (list style, no shell).
        timeout: Timeout in seconds per attempt.
        max_attempts: Total number of attempts (initial + retries).
        capture_output: If True, captures stdout/stderr (like subprocess.run).
        check: If True, raises on non-zero exit.
        text: If True, decode output as text.

    Returns:
        subprocess.CompletedProcess

    Raises:
        subprocess.CalledProcessError | subprocess.TimeoutExpired after the
        final failed attempt.
    """
    last_exc: Optional[BaseException] = None
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
        except subprocess.TimeoutExpired as exc:
            last_exc = exc
            if attempt < max_attempts:
                log.warning("Attempt %d timed out after %s seconds; retrying...", attempt, timeout)
                continue
            else:
                log.warning("Attempt %d timed out after %s seconds; no attempts left.", attempt, timeout)
        except subprocess.CalledProcessError as exc:
            # Do not retry on non-zero exit; fail fast
            if capture_output and getattr(exc, 'stderr', None):
                log.error("Command failed with exit code %s. Stderr: %s", exc.returncode, exc.stderr)
            else:
                log.error("Command failed with exit code %s.", exc.returncode)
            raise

    log.error("All %d attempts failed.", max_attempts)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("run_with_retries failed without capturing an exception")
