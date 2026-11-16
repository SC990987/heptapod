"""
# __init__.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

CMS simulation tools: produce NanoAOD (or any tier on the way) for a process and a run
period, with the official campaign's releases and conditions, here or on HTCondor.

- campaigns.ListMCCampaignsTool
- prepare.PrepareMCProductionTool
- run.RunMCProductionTool
- status.MCProductionStatusTool

The tool modules are thin adapters. The work is done by the underscore modules next to
them, which import neither orchestral nor CMSSW: CMSSW runs only in the scripts a
production writes, in an el8 container or an EL8 batch slot.
"""
