"""
# __init__.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Analysis framework tools.

Write a coffea analysis framework for a new analysis, extend it with optional
components, and check that it is consistent and runs:

- scaffold.ScaffoldAnalysisFrameworkTool
- components.AddFrameworkComponentTool
- check.CheckAnalysisFrameworkTool

The tool modules are thin adapters. The work is done by the underscore modules next to
them, which import neither orchestral nor coffea, and by the files under template/,
which are what ends up in the user's project.
"""
