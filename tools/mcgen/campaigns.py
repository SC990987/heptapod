"""
# campaigns.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

ListMCCampaignsTool -- the run periods simulation can be produced for, and with what.
"""
import json
from typing import Optional

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField

from tools.mcgen import _campaigns as campaigns


class ListMCCampaignsTool(BaseTool):
    """
    List the run periods CMS simulation can be produced for, and what each is made with.

    For each run period: the CMS campaign, the centre-of-mass energy, the HLT menu, the
    premixed-pileup library, and for every step of the chain (GEN-SIM; LHE-GEN-SIM when
    the generator reads a gridpack; DIGI to HLT; RECO; MiniAOD; NanoAOD) the CMSSW
    release, the global tag, the era and the beamspot. Every step's settings are those
    of a real McM request of that campaign, named under "mcm", so a production made
    here matches the official samples of the run period.

    Args:
        run_period: Only this run period. Omit it for all.

    Returns (JSON):
        {"status": "ok", "run_periods": [{"run_period", "description", "campaign",
         "com_energy_gev", "hlt_menu", "premix_dataset", "steps": {step: {"release",
         "scram_arch", "step", "conditions", "era", "beamspot", "mcm"}}}],
         "tiers": [...]}
    """

    run_period: Optional[str] = RuntimeField(
        default=None, description="Only this run period (2022, 2022EE, 2023, 2023BPix, 2024)")

    def _run(self) -> str:
        if self.run_period is not None and self.run_period not in campaigns.RUN_PERIODS:
            return self.format_error(
                error="Unknown Run Period", reason=f"{self.run_period!r} is not a run period here",
                suggestion=f"Run periods: {', '.join(campaigns.RUN_PERIODS)}")
        names = [self.run_period] if self.run_period else list(campaigns.RUN_PERIODS)
        return json.dumps({"status": "ok",
                           "run_periods": [campaigns.describe(name) for name in names],
                           "tiers": list(campaigns.TIERS)}, ensure_ascii=False)
