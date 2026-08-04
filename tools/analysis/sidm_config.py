"""
# sidm_config.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Central configuration for the SIDM "two Lepton Jets" analysis
(CMS AN-23-107). Everything the reconstruction/selection tools need that is
*policy* rather than *mechanism* lives here: the object-selection thresholds
from Table 12, the Lepton-Jet clustering/categorization/isolation/displacement
criteria (Tables 10, 11), the event-selection requirements (Sec. 5, Tables
13-15), and -- crucially -- the *branch-name map* that says where each of those
quantities lives inside an LLPNanoAOD ROOT file.

Why a single config module:

  * The physics thresholds are quoted verbatim from the note, in one place, so
    they can be audited against Table 12 / Table 11 / Table 7 at a glance.
  * LLPNanoAOD is a moving target -- the exact branch names for the DSA-muon
    collection, per-muon pixel hits, per-photon ID inputs, etc. vary between
    productions. Every branch the tools read is declared in `FieldMap` and can
    be overridden at call time (see `SIDMConfig.from_overrides`). Run
    `SIDMInspectFileTool` on a real file first to learn the true names, then
    pass any corrections through the tool's `field_overrides` argument.

Nothing in this module imports coffea/awkward at import time, so it is safe to
import in a bare environment (e.g. for unit-testing the pure selection helpers).
The coffea schema builder imports coffea lazily inside the function body.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any

# Particle masses used to build constituent 4-vectors for clustering (GeV).
ELECTRON_MASS = 0.000511
MUON_MASS = 0.105658
PHOTON_MASS = 0.0


# ===================================================================== #
# ============== Object selection thresholds (Table 12) =============== #
# ===================================================================== #

@dataclass
class ObjectSelection:
    """pT/|eta|/ID thresholds for the four LJ-constituent collections.

    Values are quoted from Sec. 4.1 and summarised in Table 12 of AN-23-107.
    """
    # GED electrons (Sec. 4.1.1)
    electron_pt_min: float = 10.0
    electron_abseta_max: float = 2.4
    # MVA v2 NoIso loose WP. Stock 2018 NanoAOD boolean branch name below.
    electron_id_branch: str = "Electron_mvaFall17V2noIso_WPL"

    # PF photons (Sec. 4.1.2, Table 7). Modified cut-based loose:
    # sigma_ietaieta and photon isolation requirements are DROPPED.
    photon_pt_min: float = 20.0
    photon_abseta_max: float = 2.4
    # ID mode:
    #   "relaxed_bitmap" -> decode Photon_vidNestedWPBitmap (2 bits x 7 cuts)
    #                       and require the loose cut-based decisions EXCEPT
    #                       sigma_ietaieta (cut 3) and photon iso (cut 6), which
    #                       the analysis drops (Sec. 4.1.2). This reproduces the
    #                       "modified loose" ID from the standard NanoAOD bitmap.
    #   "cutbased_loose" -> Photon_cutBased >= 1 (simplest; but keeps the
    #                       sigma_ietaieta + photon-iso cuts -> less faithful).
    #   "table7"         -> explicit H/E + rho-corrected iso cuts (needs the
    #                       absolute-iso branches, which stock NanoAOD lacks).
    photon_id_mode: str = "relaxed_bitmap"
    # relaxed_bitmap parameters (Fall17V2 photon VID cut order):
    #   0 MinPt, 1 SCEta, 2 H/E, 3 sigmaIEtaIEta, 4 chIso, 5 nhIso, 6 phoIso
    photon_vid_bitmap_branch: str = "Photon_vidNestedWPBitmap"
    photon_vid_bits_per_cut: int = 2
    photon_vid_required_cuts: tuple = (0, 1, 2, 4, 5)  # drop 3 (sieie), 6 (phoIso)
    photon_vid_min_level: int = 1                       # >= loose per required cut
    # H/E and rho-corrected charged/neutral-hadron isolation cut values
    # (Fall17 V2 loose), split by ECAL barrel (|scEta|<=1.479) / endcap.
    photon_hoe_max_eb: float = 0.04596
    photon_hoe_max_ee: float = 0.0590
    photon_chIso_max_eb: float = 1.694
    photon_chIso_max_ee: float = 2.089
    # neutral-hadron iso cut is quadratic in pT: a + b*pT + c*pT^2
    photon_nhIso_eb: tuple = (24.032, 0.01512, 0.00002259)
    photon_nhIso_ee: tuple = (19.722, 0.0117, 0.000023)
    photon_sceta_barrel_max: float = 1.479
    photon_apply_pixel_seed_veto: bool = True   # reject if pixelSeed set
    photon_electron_dr_veto: float = 0.025      # reject photon within dR of an e
    # Table-7 / veto input branches (override to match the file).
    photon_sceta_branch: str = "Photon_superclusterEta"
    photon_hoe_branch: str = "Photon_hoe"
    photon_chiso_branch: str = "Photon_pfChargedIso"
    photon_nhiso_branch: str = "Photon_pfNeutralHadIso"
    photon_pixelseed_branch: str = "Photon_pixelSeed"
    photon_cutbased_branch: str = "Photon_cutBased"

    # PF muons (Sec. 4.1.3)
    pfmuon_pt_min: float = 5.0
    pfmuon_abseta_max: float = 2.4
    pfmuon_id_branch: str = "Muon_looseId"

    # DSA muons (Sec. 4.1.4, Table 8)
    dsamuon_pt_min: float = 10.0
    dsamuon_abseta_max: float = 2.4
    # displacedID flag in the LLPNanoAOD DSA collection: require > 0.
    dsamuon_displacedid_branch: str = "DSAMuon_displacedID"
    dsamuon_displacedid_min: float = 0.0        # strict > applied in code
    # Explicit Table-8 fallbacks, used only if displacedID branch is absent:
    dsamuon_min_muon_hits: int = 12             # CSC+DT valid hits > 12
    dsamuon_dt_only_hits: int = 18              # if no CSC hit: > 18 DT hits
    dsamuon_norm_chi2_max: float = 2.5
    dsamuon_pt_rel_err_max: float = 1.0         # sigma(pT)/pT < 1


# ===================================================================== #
# ============ PF-DSA muon cross-cleaning (Table 9) =================== #
# ===================================================================== #

@dataclass
class PFDSACrossClean:
    """Remove a DSA muon if it overlaps a selected PF muon (Sec. 4.1.4).

    Full definition uses muon-segment matching (Table 9). If the segment
    branches are unavailable in the file, the tools fall back to an angular
    match on the DSA outer-track direction (`fallback_dr`).
    """
    # "dr" (default, robust): remove a DSA within `fallback_dr` of a selected
    # PF muon. "segment": full Table-9 shared-segment logic (needs the segment
    # branches; the tool auto-falls-back to "dr" if they are absent).
    method: str = "dr"                           # "dr" | "segment"
    min_shared_segments: int = 1                 # Nmatch >= 1
    delta_r_outer_max: float = 0.1               # Overlap Cond. 1
    shared_frac_min: float = 0.34                # Overlap Cond. 1 lower edge
    # Overlap Cond. 2: shared fraction >= 1 -> always remove (any dR).
    fallback_dr: float = 0.1                     # used when method == "dr"
    # branch names for segment matching
    n_matched_segments_branch: str = "DSAMuon_nMatchedSegments"
    n_dsa_segments_branch: str = "DSAMuon_nSegments"
    dsa_outer_eta_branch: str = "DSAMuon_outerEta"
    dsa_outer_phi_branch: str = "DSAMuon_outerPhi"


# ===================================================================== #
# ============ Lepton-Jet clustering & selection (Tables 10, 11) ====== #
# ===================================================================== #

@dataclass
class LeptonJetSelection:
    """anti-kT clustering + categorization + isolation + displacement."""
    # Clustering (Sec. 4.2)
    jet_radius: float = 0.4                      # anti-kT Delta R cone
    lj_pt_min: float = 30.0
    lj_abseta_max: float = 2.4
    # Clustering engine: "fastjet" (columnar, batched over all events via the
    # fastjet+awkward ClusterSequence -- the coffea-native path) or "builtin"
    # (the pure-Python reference anti-kT, used as a validated fallback and in
    # tests). Both give identical jets.
    cluster_backend: str = "fastjet"
    # mu-type multiplicity requirement: N_mu^LJ >= 2 (Sec. 4.2)
    mu_type_nmu_min: int = 2

    # Isolation (Sec. 4.3): IsoLJ = (E_MJ/E_LJ)*(1 - f_lepton),
    # matched jet = closest PF jet within dR < matched_jet_dr; iso=0 if none.
    matched_jet_dr: float = 0.4
    iso_max: float = 0.2                          # Table 11
    # PF-jet energy-fraction branches summed into f_lepton = chEM+neEM+mu
    jet_ch_em_frac_branch: str = "Jet_chEmEF"
    jet_ne_em_frac_branch: str = "Jet_neEmEF"
    jet_mu_frac_branch: str = "Jet_muEF"

    # Displacement (Sec. 4.4, Table 11)
    # eg-type: require every electron to have lostHits >= this (min over e's)
    electron_lost_hits_min: int = 1
    electron_lost_hits_branch: str = "Electron_lostHits"
    # mu-type: require every PF muon to have pixelHits <= this (max over mu's)
    pfmuon_pixel_hits_max: int = 2
    pfmuon_pixel_hits_branch: str = "Muon_trkNumPixelHits"


# ===================================================================== #
# ==================== Event selection (Sec. 5) ====================== #
# ===================================================================== #

@dataclass
class EventSelection:
    """Triggers, primary-vertex filter, cosmic veto, channel definitions."""
    # Table 13: analysis triggers (logical OR)
    trigger_paths: List[str] = field(default_factory=lambda: [
        "HLT_DoubleL2Mu23NoVtx_2Cha",
        "HLT_DoubleL2Mu23NoVtx_2Cha_CosmicSeed",
        "HLT_DoubleL2Mu25NoVtx_2Cha_Eta2p4",
        "HLT_DoubleL2Mu25NoVtx_2Cha_CosmicSeed_Eta2p4",
    ])

    # Primary-vertex filter (Table 15). CMS-standard "goodVertices" values are
    # used as defaults (ndof>4, |z|<24 cm, rho<2 cm). NOTE: the note prints
    # "24 mm"/"2 mm"; that appears to be a units typo for the standard cm cuts.
    # Prefer the precomputed Flag if present, else compute from PV_*.
    pv_flag_branch: str = "Flag_goodVertices"
    pv_ndof_min: float = 4.0
    pv_absz_max: float = 24.0     # cm
    pv_rho_max: float = 2.0       # cm
    pv_ndof_branch: str = "PV_ndof"
    pv_z_branch: str = "PV_z"
    pv_x_branch: str = "PV_x"
    pv_y_branch: str = "PV_y"

    # Cosmic-ray muon veto (Sec. 5.2.2): veto events containing a back-to-back
    # muon pair (3D opening angle near pi). Simple, configurable proxy.
    cosmic_veto_enabled: bool = True
    cosmic_veto_3d_angle: float = 2.9   # rad; veto if opening angle > this

    # Channel definition (Sec. 5.3), by the two leading selected LJs:
    #   both mu-type            -> "4mu"
    #   one mu-type + one eg    -> "2mu2e"
    #   (no 4e channel: two muons are required by the trigger)


# ===================================================================== #
# ================= Branch-name map for LLPNanoAOD =================== #
# ===================================================================== #

@dataclass
class FieldMap:
    """Names of the collections/branches read from the ROOT file.

    Collection names follow the NanoAOD convention: a counter branch
    `n<Collection>` plus per-object `<Collection>_<field>` branches, which
    coffea's NanoAODSchema groups into a single record array.
    """
    tree_name: str = "Events"
    electron_coll: str = "Electron"
    photon_coll: str = "Photon"
    pfmuon_coll: str = "Muon"
    dsamuon_coll: str = "DSAMuon"
    jet_coll: str = "Jet"
    genpart_coll: str = "GenPart"

    # gen-level PDG ids (Sec. 3 kinematics). In the CMS SIDM LLPNanoAOD samples
    # (BsTo2DpTo...) the dark photon "Dp" is stored with pdgId 32.
    dark_photon_pdgid: int = 32         # Z_D / "Dp" (dark photon)
    electron_pdgid: int = 11
    muon_pdgid: int = 13
    # GenPart member fields. Mother index is standard NanoAOD; the production-
    # vertex fields (needed for dark-photon Lxy, Sec. 3) are LLPNanoAOD
    # additions and vary by production -- override to match the file. If
    # absent, Lxy is skipped with a warning.
    genpart_mother_field: str = "genPartIdxMother"
    genpart_vx_field: str = "vx"
    genpart_vy_field: str = "vy"


# ===================================================================== #
# ========================= Top-level config ========================= #
# ===================================================================== #

@dataclass
class SIDMConfig:
    objects: ObjectSelection = field(default_factory=ObjectSelection)
    crossclean: PFDSACrossClean = field(default_factory=PFDSACrossClean)
    leptonjets: LeptonJetSelection = field(default_factory=LeptonJetSelection)
    events: EventSelection = field(default_factory=EventSelection)
    fields: FieldMap = field(default_factory=FieldMap)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_overrides(cls, overrides: Optional[Dict[str, Any]] = None) -> "SIDMConfig":
        """Build a config, deep-merging a nested override dict onto the
        defaults. Example::

            SIDMConfig.from_overrides({
                "fields": {"dsamuon_coll": "DisplacedStandAloneMuon"},
                "objects": {"pfmuon_pt_min": 3.0},
                "leptonjets": {"iso_max": 0.15},
            })
        """
        cfg = cls()
        if not overrides:
            return cfg
        for section, vals in overrides.items():
            if not hasattr(cfg, section):
                raise ValueError(
                    f"Unknown config section '{section}'. Valid sections: "
                    f"objects, crossclean, leptonjets, events, fields."
                )
            sub = getattr(cfg, section)
            for k, v in (vals or {}).items():
                if not hasattr(sub, k):
                    raise ValueError(
                        f"Unknown key '{k}' in config section '{section}'."
                    )
                setattr(sub, k, v)
        return cfg


# ===================================================================== #
# ===================== Pure geometry helpers ======================== #
# ===================================================================== #
# Small, dependency-free helpers shared by the tools. Vectorised awkward
# versions live in the tool modules; these scalar forms are used in tests and
# in the (rare) python-loop code paths.

def delta_phi(phi1: float, phi2: float) -> float:
    """Signed azimuthal difference wrapped to (-pi, pi]."""
    d = phi1 - phi2
    while d > math.pi:
        d -= 2.0 * math.pi
    while d <= -math.pi:
        d += 2.0 * math.pi
    return d


def delta_r(eta1: float, phi1: float, eta2: float, phi2: float) -> float:
    """DeltaR = sqrt(Deta^2 + Dphi^2)."""
    deta = eta1 - eta2
    dphi = delta_phi(phi1, phi2)
    return math.sqrt(deta * deta + dphi * dphi)


def photon_nh_iso_cut(pt: float, params: tuple) -> float:
    """Quadratic neutral-hadron isolation cut a + b*pT + c*pT^2 (Table 7)."""
    a, b, c = params
    return a + b * pt + c * pt * pt


# ===================================================================== #
# ==================== coffea schema (lazy import) =================== #
# ===================================================================== #

def build_llpnano_schema(extra_mixins: Optional[Dict[str, str]] = None):
    """Return a NanoAODSchema subclass that understands LLPNanoAOD's extra
    collections (DSA / displaced-global muons) as Lorentz-vector-bearing
    records. Imports coffea lazily so this module stays import-light.

    coffea's NanoAODSchema auto-groups any `n<Name>` + `<Name>_<field>` branch
    family into a record automatically, so the LLP-only collections (DSAMuon,
    DGLMuon, ...) are already accessible as `events.DSAMuon` etc. with their raw
    `pt/eta/phi/...` fields -- which is all these tools need (4-vectors are built
    internally with the muon mass).

    We deliberately do NOT attach the `Muon` (PtEtaPhiM Lorentz-vector) behaviour
    to the DSA collections: DSA-muon records have no `mass` branch, so coffea
    would raise "missing temporal coordinate" when the behaviour tries to form a
    4-vector. They stay generic collections. Pass `extra_mixins` if you really
    want to attach a vector behaviour (and your file has the needed fields).
    """
    from coffea.nanoevents import NanoAODSchema

    class LLPNanoAODSchema(NanoAODSchema):
        # Silence cross-reference warnings for LLP-only collections that have
        # no genPart/associated-object index branches.
        warn_missing_crossrefs = False
        # Don't hard-fail if run/luminosityBlock/event are absent (they are
        # present in real CMS files, but skimmed/minimal files may drop them).
        error_missing_event_ids = False

    mixins = dict(LLPNanoAODSchema.mixins)
    # LLPNanoAOD stores GenPart with BOTH px/py/pz and pt/eta/phi. coffea's
    # GenParticle Lorentz-vector behaviour rejects that ("conflicting azimuthal
    # coordinate representations"). We read GenPart fields raw and build our own
    # 4-vectors, so drop the vector behaviour and keep GenPart a generic
    # collection (its raw fields, incl. genPartIdxMother/vx/vy, stay accessible).
    mixins.pop("GenPart", None)
    if extra_mixins:
        mixins.update(extra_mixins)
    LLPNanoAODSchema.mixins = mixins
    return LLPNanoAODSchema
