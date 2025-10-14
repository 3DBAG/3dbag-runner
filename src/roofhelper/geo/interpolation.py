from __future__ import annotations

import os
from typing import Optional, Union

import numpy as np
from numpy.typing import NDArray
from osgeo import gdal
from scipy.spatial import KDTree


def _get_nodata_value(band: gdal.Band) -> Optional[float]:
    nodata = band.GetNoDataValue()
    # GDAL may return None if undefined
    return float(nodata) if nodata is not None else None


def image_interpolation_idw(
    input_file: Union[str, "os.PathLike[str]"],
    output_file: Union[str, "os.PathLike[str]"],
    *,
    k: int = 8,
    power: float = 2.0,
    batch_size: int = 500_000,
) -> None:
    """
    Fill nodata in a raster using Inverse Distance Weighting (IDW) over pixel coordinates.

    Parameters
    ----------
    input_file: path-like
        Path to an input GeoTIFF (or GDAL-readable raster) to read from.
    output_file: path-like
        Path to write the filled raster. If equal to input_file, writes in place
        when possible; otherwise, creates a copy and writes there.
    k: int
        Number of nearest neighbors to use.
    power: float
        IDW power parameter; larger values emphasize nearer neighbors.
    batch_size: int
        Process unknown pixels in batches to limit memory usage.
    """

    # Open source dataset and read band array
    src_ds = gdal.Open(str(input_file), gdal.GA_ReadOnly)
    if src_ds is None:
        raise RuntimeError(f"Unable to open input raster: {input_file}")

    band = src_ds.GetRasterBand(1)
    arr = _read_band_float64(band)

    nodata = _get_nodata_value(band)
    if nodata is None:
        mask_known = np.isfinite(arr)
    else:
        mask_known = arr != nodata

    # If nothing to fill, just copy/write through
    if np.all(mask_known):
        _write_output(src_ds, arr, input_file, output_file)
        return

    y, x = np.indices(arr.shape)
    coords_known = np.column_stack((x[mask_known], y[mask_known]))
    values_known = arr[mask_known]

    if coords_known.size == 0:
        # No known values; nothing to interpolate
        _write_output(src_ds, arr, input_file, output_file)
        return

    # Build KD-tree for known pixels
    tree = KDTree(coords_known)
    unknown_mask = ~mask_known
    coords_unknown = np.column_stack((x[unknown_mask], y[unknown_mask]))

    effective_k = max(1, min(k, len(values_known)))

    # Interpolate in batches to control memory
    filled_arr = arr.copy()
    n_unknown = coords_unknown.shape[0]
    eps = 1e-12

    for start in range(0, n_unknown, batch_size):
        end = min(start + batch_size, n_unknown)
        batch = coords_unknown[start:end]
        dist, idx = tree.query(batch, k=effective_k)
        # Ensure numpy arrays for consistent typing/operations
        dist = np.asarray(dist)
        idx = np.asarray(idx)

        # Ensure correct dimensions when k==1
        if effective_k == 1:
            dist = dist[:, np.newaxis]
            idx = idx[:, np.newaxis]

        w = 1.0 / ((dist ** power) + eps)
        vals = values_known[idx]
        num = np.sum(w * vals, axis=1)
        den = np.sum(w, axis=1)
        filled_vals = num / den

        # Assign back into array
        uy, ux = coords_unknown[start:end, 1], coords_unknown[start:end, 0]
        filled_arr[uy, ux] = filled_vals

    _write_output(src_ds, filled_arr, input_file, output_file)


def _write_output(
    src_ds: gdal.Dataset, array: NDArray[np.float64], input_file: str | os.PathLike[str], output_file: str | os.PathLike[str]
) -> None:
    in_path = str(input_file)
    out_path = str(output_file)

    if os_path_eq(in_path, out_path):
        # Write in place
        upd = gdal.Open(out_path, gdal.GA_Update)
        if upd is None:
            # Fall back to copy if in-place update is not possible
            driver = gdal.GetDriverByName(src_ds.GetDriver().ShortName)
            dst_ds = driver.CreateCopy(out_path, src_ds, 0)
            _write_band_from_float64(dst_ds.GetRasterBand(1), array)
            dst_ds = None
        else:
            _write_band_from_float64(upd.GetRasterBand(1), array)
            upd = None
    else:
        driver = gdal.GetDriverByName(src_ds.GetDriver().ShortName)
        dst_ds = driver.CreateCopy(out_path, src_ds, 0)
        _write_band_from_float64(dst_ds.GetRasterBand(1), array)
        dst_ds = None


def os_path_eq(a: str, b: str) -> bool:
    try:
        import os
        return os.path.abspath(a) == os.path.abspath(b)
    except Exception:
        return a == b


def _read_band_float64(band: gdal.Band) -> NDArray[np.float64]:
    """
    Read a GDAL band into a numpy float64 array using ReadRaster to avoid
    dependency on osgeo.gdal_array/_gdal_array.
    """
    xsize = band.XSize
    ysize = band.YSize
    # Read as float64 buffer regardless of source dtype
    buf = band.ReadRaster(0, 0, xsize, ysize, buf_xsize=xsize, buf_ysize=ysize, buf_type=gdal.GDT_Float64)
    if buf is None:
        raise RuntimeError("Failed to read raster band data")
    arr = np.frombuffer(buf, dtype=np.float64)
    if arr.size != xsize * ysize:
        raise RuntimeError("Unexpected raster buffer size")
    return arr.reshape(ysize, xsize)


def _write_band_from_float64(band: gdal.Band, array: NDArray[np.float64]) -> None:
    """
    Write a numpy array to a GDAL band using WriteRaster, providing a float64 buffer
    to avoid osgeo.gdal_array dependency. Array is expected shape (rows, cols).
    """
    if array.ndim != 2:
        raise ValueError("Expected 2D array for single-band raster")
    ysize, xsize = array.shape
    # Ensure float64 buffer
    data = np.asarray(array, dtype=np.float64, order="C").tobytes()
    band.WriteRaster(0, 0, xsize, ysize, data, buf_xsize=xsize, buf_ysize=ysize, buf_type=gdal.GDT_Float64)
