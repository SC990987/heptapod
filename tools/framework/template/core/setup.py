"""Packaging, so that the analysis can be installed with ``pip install -e .``"""

import setuptools

setuptools.setup(
    name="analysis_pkg",
    version="0.1.0",
    description="__PROJECT_TITLE__",
    author="__AUTHOR__",
    packages=setuptools.find_packages(include=["analysis_pkg", "analysis_pkg.*"]),
    package_data={
        "analysis_pkg": [
            "configs/*.yaml",
            "configs/samples/*.yaml",
            "data/*",
        ],
    },
    include_package_data=True,
    python_requires=">=3.9",
)
