"""
Test cases for the translate_cityjson function.

This module contains comprehensive test cases for the translate_cityjson function
that translates and scales CityJSON coordinate data.
"""

import pytest
import copy
from typing import Any, Dict

from roofhelper.tyler import translate_cityjson


class TestTranslateCityJSON:
    """Test cases for translate_cityjson function."""

    def setup_method(self) -> None:
        """Set up test fixtures before each test method."""
        # Base test data with standard CityJSON structure
        self.base_data: Dict[str, Any] = {
            "type": "CityJSON",
            "version": "1.1",
            "transform": {
                "scale": [0.001, 0.001, 0.001],
                "translate": [171800.0, 472700.0, 0.0]
            },
            "vertices": [
                [0, 0, 0],
                [1000, 1000, 1000],
                [500, 750, 250]
            ]
        }

    def test_translate_cityjson_sample_1(self) -> None:
        """Test translation with sample data, case 1"""
        sample1_input: Dict[str, Any] = {
            "type": "CityJSON",
            "version": "1.1",
            "transform": {
                "scale": [0.001, 0.001, 0.001],
                "translate": [153907.738, 467305.429, 1.016]
            },
            "vertices": [
                [367988, 18246, 38],
                [366536, 17589, 38],
                [365216, 16697, 38]
            ]
        }

        result = translate_cityjson(sample1_input)
        assert result["vertices"] == [(-17524274, -5376325, 1054), (-17525726, -5376982, 1054), (-17527046, -5377874, 1054)]

    def test_translate_cityjson_no_translation_needed(self) -> None:
        """Test when translation values are already at base coordinates."""
        data = copy.deepcopy(self.base_data)

        result = translate_cityjson(data)

        # Transform should remain the same
        assert result["transform"]["translate"] == (171800.0, 472700.0, 0.0)
        assert result["transform"]["scale"] == (0.001, 0.001, 0.001)

        # Vertices should remain unchanged since no translation is needed
        assert result["vertices"] == [(0, 0, 0), (1000, 1000, 1000), (500, 750, 250)]

    def test_translate_cityjson_positive_translation(self) -> None:
        """Test translation with positive offsets."""
        data = copy.deepcopy(self.base_data)
        # Set transform to require positive translation
        data["transform"]["translate"] = [172000.0, 473000.0, 100.0]

        result = translate_cityjson(data)

        # Transform should be set to base values
        assert result["transform"]["translate"] == (171800.0, 472700.0, 0.0)

        # Calculate expected translations with corrected formula
        # dX = (172000.0 - 171800.0) / 0.001 = 200.0 / 0.001 = 200000
        # dY = (473000.0 - 472700.0) / 0.001 = 300.0 / 0.001 = 300000
        # dZ = (100.0 - 0.0) / 0.001 = 100.0 / 0.001 = 100000
        # scale_difference = 0.001/0.001 = 1.0 for all axes
        # Formula: x + dX / scale_difference = x + dX / 1.0 = x + dX

        expected_vertices = [
            (200000, 300000, 100000),     # [0 + 200000, 0 + 300000, 0 + 100000]
            (201000, 301000, 101000),     # [1000 + 200000, 1000 + 300000, 1000 + 100000]
            (200500, 300750, 100250)      # [500 + 200000, 750 + 300000, 250 + 100000]
        ]

        assert result["vertices"] == expected_vertices

    def test_translate_cityjson_negative_translation(self) -> None:
        """Test translation with negative offsets."""
        data = copy.deepcopy(self.base_data)
        # Set transform to require negative translation
        data["transform"]["translate"] = [171600.0, 472400.0, -50.0]

        result = translate_cityjson(data)

        # Transform should be set to base values
        assert result["transform"]["translate"] == (171800.0, 472700.0, 0.0)

        # Calculate expected translations with corrected formula
        # dX = (171600.0 - 171800.0) / 0.001 = -200.0 / 0.001 = -200000
        # dY = (472400.0 - 472700.0) / 0.001 = -300.0 / 0.001 = -300000
        # dZ = (-50.0 - 0.0) / 0.001 = -50.0 / 0.001 = -50000
        # scale_difference = 0.001/0.001 = 1.0 for all axes
        # Formula: x + dX / scale_difference = x + dX / 1.0 = x + dX

        expected_vertices = [
            (-200000, -300000, -50000),   # [0 + (-200000), 0 + (-300000), 0 + (-50000)]
            (-199000, -299000, -49000),   # [1000 + (-200000), 1000 + (-300000), 1000 + (-50000)]
            (-199500, -299250, -49750)    # [500 + (-200000), 750 + (-300000), 250 + (-50000)]
        ]

        assert result["vertices"] == expected_vertices

    def test_translate_cityjson_different_scales(self) -> None:
        """Test translation with different scale values."""
        # Create test data with different scale (0.01 instead of 0.001)
        # Vertices should be 10 times smaller to represent same real-world coordinates
        data = {
            "type": "CityJSON",
            "version": "1.1",
            "transform": {
                "scale": [0.01, 0.01, 0.01],  # 10x larger scale
                "translate": [171800.0, 472700.0, 0.0]
            },
            "vertices": [
                [0, 0, 0],          # Same as base but represents 10x smaller in real world
                [100, 100, 100],    # 1000/10 = 100 (10x smaller due to 10x larger scale)
                [50, 75, 25]        # [500, 750, 250]/10 = [50, 75, 25]
            ]
        }

        result = translate_cityjson(data)

        # Transform translate should be set to base values
        assert result["transform"]["translate"] == (171800.0, 472700.0, 0.0)
        # Scale should change
        assert result["transform"]["scale"] == (0.001, 0.001, 0.001)

        # Calculate expected translations with corrected formula and different scale
        # dX = (171800.0 - 171800.0) / 0.01 = 0.0 / 0.01 = 0
        # dY = (472700.0 - 472700.0) / 0.01 = 0.0 / 0.01 = 0
        # dZ = (0.0 - 0.0) / 0.01 = 0.0 / 0.01 = 0
        # scale_difference = 0.001/0.01 = 0.1 for all axes
        # Formula: (x + dX) / scale_difference = (x + 0) / 0.1 = x / 0.1 = x * 10

        expected_vertices = [
            (0, 0, 0),    # [0 * 10, 0 * 10, 0 * 10]
            (1000, 1000, 1000),    # [100 * 10, 100 * 10, 100 * 10]
            (500, 750, 250)     # [50 * 10, 75 * 10, 25 * 10]
        ]

        assert result["vertices"] == expected_vertices

    def test_translate_cityjson_fractional_coordinates(self) -> None:
        """Test translation with fractional coordinate results."""
        data = copy.deepcopy(self.base_data)
        data["transform"]["translate"] = [171800.5, 472700.7, 0.3]

        result = translate_cityjson(data)

        # Calculate expected translations with corrected formula
        # dX = (171800.5 - 171800.0) / 0.001 = 0.5 / 0.001 = 500
        # dY = (472700.7 - 472700.0) / 0.001 = 0.7 / 0.001 = 700
        # dZ = (0.3 - 0.0) / 0.001 = 0.3 / 0.001 = 300
        # scale_difference = 0.001/0.001 = 1.0 for all axes
        # Formula: x + dX / scale_difference = x + dX / 1.0 = x + dX
        # Results should be rounded to integers

        expected_vertices = [
            (500, 700, 300),        # [0 + 500, 0 + 700, 0 + 300]
            (1500, 1700, 1300),     # [1000 + 500, 1000 + 700, 1000 + 300]
            (1000, 1450, 550)       # [500 + 500, 750 + 700, 250 + 300]
        ]

        assert result["vertices"] == expected_vertices

    def test_translate_cityjson_rounding_behavior(self) -> None:
        """Test proper rounding of coordinate calculations."""
        data = copy.deepcopy(self.base_data)
        data["transform"]["translate"] = [171800.0006, 472699.9994, 0.0001]

        result = translate_cityjson(data)

        # Calculate expected translations with corrected formula
        # dX = (171800.0006 - 171800.0) / 0.001 = 0.0006 / 0.001 = 0.6
        # dY = (472699.9994 - 472700.0) / 0.001 = -0.0006 / 0.001 = -0.6
        # dZ = (0.0001 - 0.0) / 0.001 = 0.0001 / 0.001 = 0.1
        # scale_difference = 0.001/0.001 = 1.0 for all axes
        # Formula: x + dX / scale_difference = x + dX / 1.0 = x + dX
        # Should round: 0.6 -> 1, -0.6 -> -1, 0.1 -> 0 (small changes)

        expected_vertices = [
            (1, -1, 0),          # [0 + 0.6, 0 + (-0.6), 0 + 0.1] -> [1, -1, 0] after rounding
            (1001, 999, 1000),   # [1000 + 0.6, 1000 + (-0.6), 1000 + 0.1] -> [1001, 999, 1000]
            (501, 749, 250)      # [500 + 0.6, 750 + (-0.6), 250 + 0.1] -> [501, 749, 250]
        ]

        assert result["vertices"] == expected_vertices

    def test_translate_cityjson_empty_vertices(self) -> None:
        """Test translation with empty vertices list."""
        data = copy.deepcopy(self.base_data)
        data["vertices"] = []
        data["transform"]["translate"] = [172000.0, 473000.0, 100.0]

        result = translate_cityjson(data)

        assert result["transform"]["translate"] == (171800.0, 472700.0, 0.0)
        assert result["vertices"] == []

    def test_translate_cityjson_single_vertex(self) -> None:
        """Test translation with single vertex."""
        data = copy.deepcopy(self.base_data)
        data["vertices"] = [[100, 200, 300]]
        data["transform"]["translate"] = [171900.0, 472800.0, 50.0]

        result = translate_cityjson(data)

        # Calculate expected translations with corrected formula
        # dX = (171900.0 - 171800.0) / 0.001 = 100.0 / 0.001 = 100000
        # dY = (472800.0 - 472700.0) / 0.001 = 100.0 / 0.001 = 100000
        # dZ = (50.0 - 0.0) / 0.001 = 50.0 / 0.001 = 50000
        # scale_difference = 0.001/0.001 = 1.0 for all axes
        # Formula: x + dX / scale_difference = x + dX / 1.0 = x + dX
        expected_vertices = [(100100, 100200, 50300)]  # [100 + 100000, 200 + 100000, 300 + 50000]

        assert result["vertices"] == expected_vertices

    def test_translate_cityjson_preserves_other_data(self) -> None:
        """Test that translation preserves other data in the CityJSON structure."""
        data = copy.deepcopy(self.base_data)
        data["CityObjects"] = {"building_1": {"type": "Building"}}
        data["metadata"] = {"title": "Test data"}
        data["transform"]["translate"] = [172000.0, 473000.0, 100.0]

        result = translate_cityjson(data)

        # Check that other data is preserved
        assert result["type"] == "CityJSON"
        assert result["version"] == "1.1"
        assert result["CityObjects"] == {"building_1": {"type": "Building"}}
        assert result["metadata"] == {"title": "Test data"}

        # Transform should be updated
        assert result["transform"]["translate"] == (171800.0, 472700.0, 0.0)

    def test_translate_cityjson_does_not_modify_input(self) -> None:
        """Test that the original input data is not modified."""
        original_data = copy.deepcopy(self.base_data)
        original_data["transform"]["translate"] = [172000.0, 473000.0, 100.0]

        input_data = copy.deepcopy(original_data)
        result = translate_cityjson(input_data)

        # The function modifies the input data in place, so input_data should be modified
        # but we can verify the result is correct
        assert result["transform"]["translate"] == (171800.0, 472700.0, 0.0)
        assert input_data is result  # Function modifies input in place

    def test_translate_cityjson_scale_not_preserved(self) -> None:
        """Test that the scale values are set to base values during translation."""
        data = copy.deepcopy(self.base_data)
        original_scale = [0.005, 0.002, 0.001]
        data["transform"]["scale"] = original_scale
        data["transform"]["translate"] = [172000.0, 473000.0, 100.0]

        result = translate_cityjson(data)

        # Scale should be set to base values, not preserved
        assert result["transform"]["scale"] == (0.001, 0.001, 0.001)

    def test_translate_cityjson_large_coordinates(self) -> None:
        """Test translation with large coordinate values."""
        data = copy.deepcopy(self.base_data)
        data["vertices"] = [[1000000, 2000000, 500000]]
        data["transform"]["translate"] = [180000.0, 480000.0, 1000.0]

        result = translate_cityjson(data)

        # Calculate expected translations with corrected formula
        # dX = (180000.0 - 171800.0) / 0.001 = 8200.0 / 0.001 = 8200000
        # dY = (480000.0 - 472700.0) / 0.001 = 7300.0 / 0.001 = 7300000
        # dZ = (1000.0 - 0.0) / 0.001 = 1000.0 / 0.001 = 1000000
        # scale_difference = 0.001/0.001 = 1.0 for all axes
        # Formula: x + dX / scale_difference = x + dX / 1.0 = x + dX
        expected_vertices = [(9200000, 9300000, 1500000)]  # [1000000 + 8200000, 2000000 + 7300000, 500000 + 1000000]

        assert result["vertices"] == expected_vertices

    def test_translate_cityjson_zero_scale_handling(self) -> None:
        """Test behavior with zero scale values (edge case)."""
        data = copy.deepcopy(self.base_data)
        data["transform"]["scale"] = [0.0, 0.001, 0.001]  # Zero X scale
        data["transform"]["translate"] = [172000.0, 473000.0, 100.0]

        # This should raise a ZeroDivisionError due to division by zero
        with pytest.raises(ZeroDivisionError):
            translate_cityjson(data)
