# HEPTAPOD Tools

This directory contains the physics tools used by HEPTAPOD for event generation, hadronization, data conversion, and analysis.

## Data Format

### evtjsonl-1.0 Schema

All event data uses the `evtjsonl-1.0` schema (JSON Lines format):

**Particle Events:**
```json
{"event_id": 1, "data": {"particles": [
  {"i": 0, "id": 11, "status": 1, "px": 45.2, "py": -12.3, "pz": 156.7, "E": 165.4, "m": 0.000511},
  {"i": 1, "id": -11, "status": 1, "px": -38.1, "py": 52.4, "pz": -89.2, "E": 107.3, "m": 0.000511}
]}}
{"event_id": 2, "data": {"particles": [...]}}
```

**Jet Events:**
```json
{"event_id": 1, "data": {"jets": [
  {"i": 0, "px": 123.4, "py": 56.7, "pz": 234.5, "E": 278.9},
  {"i": 1, "px": 89.2, "py": -45.3, "pz": 167.8, "E": 198.2}
]}}
```

**Field Definitions:**
- `i`: Particle/jet index within event
- `id`: PDG particle ID (particles only)
- `status`: Status code (1 = final state, particles only)
- `px`, `py`, `pz`: Momentum components (GeV)
- `E`: Energy (GeV)
- `m`: Mass (GeV, particles only)

**Schema Validation:**
- All tools verify `"schema_version": "evtjsonl-1.0"`
- Forward/backward compatibility through version checking
- Pydantic models ensure type safety

### LLM-Compatible Data Representation

The JSONL (JSON Lines) format serves dual purposes in HEPTAPOD: efficient storage for large event datasets and native compatibility with Large Language Model contexts. Unlike binary formats (ROOT, HDF5), JSONL provides:

**Key Advantages:**
- **Human-readable**: Direct inspection without specialized tools
- **LLM-native**: Events can be directly included in prompts for analysis and debugging
- **Streaming-friendly**: Line-by-line processing minimizes memory overhead
- **Schema-extensible**: JSON structure supports arbitrary metadata without breaking compatibility
- **Cross-platform**: Pure text format eliminates endianness and architecture dependencies

**Design Rationale:**

The choice of JSONL over traditional HEP formats reflects the integration of AI-assisted workflows. LLMs can directly reason about event structure, identify anomalies, and suggest analyses when event data is represented in JSON. This enables natural language queries like "show me events with high-pT leptons" to be answered by the LLM directly examining JSONL records.

**Format Interoperability:**

While JSONL is the internal working format, HEPTAPOD provides conversion tools for standard HEP formats:

- **LHEToJSONLTool**: Import Les Houches Event files from MadGraph/other generators
- **EventJSONLToNumpyTool**: Export to zero-padded NumPy arrays for ML pipelines
- **JetsJSONLToNumpyTool**: Convert jet collections to vectorized arrays

This architecture allows seamless integration with existing analysis frameworks (ROOT, Pandas, PyTorch/TensorFlow) while maintaining LLM transparency throughout the workflow.

---

## Tool Suite Overview

### Event Generation Tools

#### FeynRulesToUFOTool

**Purpose**: Generate Universal FeynRules Output (UFO) from FeynRules model files

**Input Parameters:**
- `model_path` (str): Path to FeynRules `.fr` model file
- `output_dir` (str): Directory for generated UFO output
- `feynrules_path` (str, optional): FeynRules installation path
- `wolframscript_path` (str, optional): WolframScript executable path

**Output:**
```json
{
  "status": "ok",
  "ufo_directory": "path/to/S1_LQ_RR_UFO",
  "files": ["couplings.py", "particles.py", "vertices.py", ...]
}
```

**Example:**
```python
from feynrules.feynrules_tools import FeynRulesToUFOTool

tool = FeynRulesToUFOTool(base_directory="./workspace")
result = tool.run(
    model_path="models/S1_LQ_RR.fr",
    output_dir="ufo_outputs"
)
```

**Requirements**: Mathematica ≤ v13.3, valid license

---

#### MadGraphFromRunCardTool

**Purpose**: Generate parton-level events using MadGraph5_aMC@NLO

**Input Parameters:**
- `command_card` (str): Path to MadGraph command card (`.mg5` file)
- `data_dir` (str): Output directory for generated events
- `ufo_path` (str, optional): Path to UFO model directory
- `output_name` (str, optional): Name for output process directory
- `nevents` (int, optional): Number of events to generate
- `seed` (int, optional): Random seed for reproducibility

**Output:**
```json
{
  "status": "ok",
  "lhe_path": "path/to/events.lhe",
  "n_events": 10000,
  "process_dir": "path/to/PROC_NAME",
  "scan_runs": ["run_01", "run_02", ...]  // If parameter scan detected
}
```

**Key Features:**
- Automatic template variable substitution in command cards
- Detection of parameter scans (auto-discovers `run_01`, `run_02`, etc.)
- Process-level provenance tracking

**Example Command Card** (`cards/signal.mg5`):
```
import model {UFO_PATH}
generate p p > s1 s1~, (s1 > l+ j, s1~ > l- j)
output {OUTPUT_NAME}
launch {OUTPUT_NAME}
  set nevents {N_EVENTS}
  set iseed {SEED}
  set ebeam1 7000
  set ebeam2 7000
done
```

**Example Usage:**
```python
from mg5.mg5_tools import MadGraphFromRunCardTool

tool = MadGraphFromRunCardTool(
    base_directory="./workspace",
    mg5_path="/path/to/MG5_aMC_v3.6.6"
)

result = tool.run(
    command_card="cards/signal.mg5",
    data_dir="mg5_output",
    ufo_path="ufo/S1_LQ_RR_UFO",
    output_name="lq_pair_production",
    nevents=10000,
    seed=42
)
```

---

#### PythiaFromRunCardTool

**Purpose**: Hadronize and shower parton-level events using Pythia8

**Input Parameters:**
- `cmnd_path` (str): Path to Pythia command card (`.cmnd` file)
- `data_dir` (str): Output directory
- `n_events` (int): Number of events to generate
- `seed` (int, optional): Random seed
- `finals_only` (bool): Keep only final-state particles (default: True)
- `full_history` (bool): Include mother/daughter indices (default: False)
- `shower_lhe` (bool): Shower pre-generated LHE events (default: False)
- `lhe_path` (str, optional): Path to input LHE file (required if `shower_lhe=True`)

**Modes:**

1. **Standalone Generation** (`shower_lhe=False`):
   ```python
   result = tool.run(
       cmnd_path="cards/pythia_standalone.cmnd",
       data_dir="output",
       n_events=10000,
       seed=42
   )
   ```

2. **LHE Showering** (`shower_lhe=True`):
   ```python
   result = tool.run(
       cmnd_path="cards/pythia_shower.cmnd",
       data_dir="output",
       n_events=10000,
       shower_lhe=True,
       lhe_path="mg5_output/events.lhe"
   )
   ```

**Output:**
```json
{
  "status": "ok",
  "events_jsonl": "path/to/events.jsonl",
  "n_events": 10000,
  "schema_version": "evtjsonl-1.0"
}
```

**Example Command Card** (`pythia_shower.cmnd`):
```
! Beams
Beams:frameType = 4           ! LHEF input
Beams:LHEF = {LHE_FILE_PATH}  ! Will be substituted by tool

! Settings
Random:setSeed = on
Random:seed = {SEED}
PartonLevel:ISR = on
PartonLevel:FSR = on
HadronLevel:all = on

! Output
Next:numberShowEvent = 0
```

---

#### JetClusterSlowJetTool

**Purpose**: Perform anti-kt jet clustering on hadronized events

**Input Parameters:**
- `events_jsonl` (str): Path to hadronized events in JSONL format
- `R` (float): Jet clustering radius (typical: 0.4 or 0.8)
- `pT_min` (float): Minimum jet transverse momentum in GeV

**Output:**
```json
{
  "status": "ok",
  "jets_jsonl": "path/to/jets.jsonl",
  "n_events": 10000,
  "total_jets": 45678
}
```

**Example:**
```python
from pythia.pythia_tools import JetClusterSlowJetTool

tool = JetClusterSlowJetTool(base_directory="./workspace")
result = tool.run(
    events_jsonl="pythia_output/events.jsonl",
    R=0.4,
    pT_min=20.0
)
```

**Note**: Uses Pythia's SlowJet algorithm (exact but slower than FastJet).

---

### Data Conversion Tools

#### LHEToJSONLTool

**Purpose**: Convert Les Houches Event (LHE) files to JSONL format

**Input Parameters:**
- `lhe_path` (str): Path to `.lhe` file
- `output_dir` (str): Output directory
- `finals_only` (bool): Keep only final-state particles (default: True)
- `full_history` (bool): Include mother indices (default: False)

**Example:**
```python
from utils.data_conversion_tools import LHEToJSONLTool

tool = LHEToJSONLTool(base_directory="./workspace")
result = tool.run(
    lhe_path="mg5_output/events.lhe",
    output_dir="converted",
    finals_only=True
)
```

---

#### EventJSONLToNumpyTool

**Purpose**: Convert event JSONL to zero-padded NumPy arrays

**Input Parameters:**
- `events_jsonl` (str): Path to JSONL event file
- `output_dir` (str): Output directory

**Output:**
- NumPy array with shape `(N_events, N_max_particles, 5)`
- Columns: `[px, py, pz, E, pid]`
- Zero-padded to match longest event

**Example:**
```python
from utils.data_conversion_tools import EventJSONLToNumpyTool

tool = EventJSONLToNumpyTool(base_directory="./workspace")
result = tool.run(
    events_jsonl="pythia_output/events.jsonl",
    output_dir="numpy_arrays"
)
# Output: workspace/numpy_arrays/events.npy
```

---

#### JetsJSONLToNumpyTool

**Purpose**: Convert jet JSONL to NumPy arrays with flexible extraction modes

**Input Parameters:**
- `jets_jsonl` (str): Path to jets JSONL file
- `output_dir` (str): Output directory
- `extraction_mode` (str): How to extract jets - `"by_pt"`, `"by_index"`, or `"n_hardest"`
- `n_jets` (int): Number of jets to extract
- `jet_indices` (list, optional): Specific jet indices (for `by_index` mode)

**Extraction Modes:**

1. **`by_pt`**: Extract N highest-pT jets per event
   ```python
   result = tool.run(
       jets_jsonl="jets.jsonl",
       output_dir="numpy",
       extraction_mode="by_pt",
       n_jets=4
   )
   ```

2. **`n_hardest`**: Same as `by_pt` (alias)

3. **`by_index`**: Extract specific jet indices
   ```python
   result = tool.run(
       jets_jsonl="jets.jsonl",
       output_dir="numpy",
       extraction_mode="by_index",
       jet_indices=[0, 1]  # First two jets
   )
   ```

**Output:** NumPy array with shape `(N_events, N_jets, 5)`, columns: `[px, py, pz, E, jet_id]`

---

### Analysis Tools

All analysis tools support both **file-based** and **legacy direct-input** modes. File-based mode is recommended for production workflows.

#### CalculateInvariantMassTool

**Purpose**: Calculate invariant mass M² = (ΣE)² - (Σp)²

**File-based Mode:**
```python
from utils.analysis_tools import CalculateInvariantMassTool

tool = CalculateInvariantMassTool(base_directory="./workspace")
result = tool.run(
    particle_array_paths=["leptons.jsonl", "jets.jsonl"],
    output_dir="results"
)
# Output: workspace/results/invariant_masses.npy
```

**Direct Mode (Legacy):**
```python
import numpy as np
particles = np.load("events.npy")  # Shape: (N_events, N_particles, 5)
result = tool.run(particle_arrays=[particles])
masses = result["invariant_masses"]  # Shape: (N_events,)
```

---

#### CalculateTransverseMomentumTool

**Purpose**: Calculate pT = √(px² + py²)

**Example:**
```python
from utils.analysis_tools import CalculateTransverseMomentumTool

tool = CalculateTransverseMomentumTool(base_directory="./workspace")
result = tool.run(
    particle_array_paths=["particles.jsonl"],
    output_dir="results"
)
# Output: workspace/results/pt.npy (shape: N_events × N_particles)
```

---

#### CalculateDeltaRTool

**Purpose**: Calculate ΔR = √(Δη² + Δφ²) between particle pairs

**Parameters:**
- `particle_array_paths`: List of particle files
- `index_pairs`: List of `[i, j]` pairs specifying which particles to compare
- `output_dir`: Output directory

**Example:**
```python
from utils.analysis_tools import CalculateDeltaRTool

tool = CalculateDeltaRTool(base_directory="./workspace")
result = tool.run(
    particle_array_paths=["particles.jsonl"],
    index_pairs=[[0, 1], [0, 2]],  # ΔR(particle0, particle1) and ΔR(particle0, particle2)
    output_dir="results"
)
# Output: workspace/results/delta_r_0_1.npy, delta_r_0_2.npy
```

---

#### ApplyCutsTool

**Purpose**: Apply physics cuts (pT, η, PDG filters)

**Parameters:**
- `particle_array_paths`: Input particles
- `pt_min`, `pt_max`: Transverse momentum range (GeV)
- `eta_min`, `eta_max`: Pseudorapidity range
- `pdg_ids`: List of allowed PDG codes
- `output_dir`: Output directory

**Example:**
```python
from utils.analysis_tools import ApplyCutsTool

tool = ApplyCutsTool(base_directory="./workspace")
result = tool.run(
    particle_array_paths=["events.jsonl"],
    pt_min=20.0,
    eta_max=2.5,
    pdg_ids=[11, -11, 13, -13],  # electrons and muons
    output_dir="selected"
)
```

---

#### GetHardestNTool / GetHardestNJetsTool

**Purpose**: Select N highest-pT particles or jets per event

**Example:**
```python
from utils.analysis_tools import GetHardestNTool

tool = GetHardestNTool(base_directory="./workspace")
result = tool.run(
    particle_array_paths=["leptons.jsonl"],
    n=2,  # Select 2 hardest leptons
    output_dir="hardest"
)
# Output: workspace/hardest/hardest_2_particles.jsonl
```

---

#### FilterByPDGIDTool

**Purpose**: Filter particles by PDG ID

**Example:**
```python
from utils.analysis_tools import FilterByPDGIDTool

tool = FilterByPDGIDTool(base_directory="./workspace")
result = tool.run(
    particle_array_paths=["events.jsonl"],
    pdg_ids=[11, -11],  # Keep only electrons
    output_dir="electrons"
)
```

---

#### SortByPtTool

**Purpose**: Sort particles by transverse momentum (descending)

---

#### FilterByDeltaRTool

**Purpose**: Remove overlapping objects with ΔR below threshold

**Parameters:**
- `particle_array_paths`: Input particles
- `min_delta_r`: Minimum allowed ΔR separation
- `output_dir`: Output directory

**Example:**
```python
from utils.analysis_tools import FilterByDeltaRTool

tool = FilterByDeltaRTool(base_directory="./workspace")
result = tool.run(
    particle_array_paths=["jets.jsonl"],
    min_delta_r=0.4,  # Remove jets closer than ΔR = 0.4
    output_dir="isolated_jets"
)
```

---

### Resonance Reconstruction

#### ResonanceReconstructionTool

**Purpose**: Template-based reconstruction of physics resonances from pre-selected objects

**Templates:**
- `"two_body_symmetric"`: Pair-produced resonances (e.g., leptoquark pairs)
- `"n_body_all_pairs"`: All possible combinations

**Input Parameters:**
- `particle_arrays` (list): Paths to pre-selected object files (e.g., `["hardest_2_leptons.jsonl", "hardest_2_jets.jsonl"]`)
- `template` (str): Analysis template
- `max_k` (int): Maximum k-body multiplicity (default: 2)
- `min_delta_r` (float, optional): Cross-array ΔR constraint

**Physics-Aware Design:**
- ΔR constraints apply **only between different arrays** (e.g., leptons vs jets)
- Objects within same array are **not** constrained (e.g., two leptons can overlap)

**Example: Leptoquark Pair Production Analysis**

```python
from utils.resonance_reconstruction_tool import ResonanceReconstructionTool

tool = ResonanceReconstructionTool(base_directory="./workspace")

# Assume we have pre-selected objects:
# - hardest_2_leptons.jsonl (2 hardest leptons per event)
# - hardest_2_jets.jsonl (2 hardest jets per event)

result = tool.run(
    particle_arrays=[
        "selected/hardest_2_leptons.jsonl",
        "selected/hardest_2_jets.jsonl"
    ],
    template="two_body_symmetric",
    max_k=2,
    min_delta_r=0.4  # Leptons and jets must be separated by ΔR > 0.4
)
```

**Output:**
```json
{
  "status": "ok",
  "template": "two_body_symmetric",
  "n_arrays": 2,
  "n_total_objects": 4,
  "n_events_analyzed": 10000,
  "n_events_successful": 9876,
  "n_events_failed": 124,
  "observables": [
    {"name": "m1", "kind": "per_event"},
    {"name": "m2", "kind": "per_event"},
    {"name": "Delta_m", "kind": "per_event"},
    {"name": "m_avg", "kind": "per_event"}
  ],
  "histograms": [
    {
      "observable": "m1",
      "bins": [0, 100, 200, ...],
      "counts": [45, 123, 234, ...]
    }
  ],
  "data_paths": {
    "m1": "workspace/results/m1.npy",
    "m2": "workspace/results/m2.npy",
    "Delta_m": "workspace/results/Delta_m.npy"
  }
}
```

**Generated Observables:**
- `m1`, `m2`: Individual resonance masses
- `Delta_m`: Mass difference |m1 - m2|
- `m_avg`: Average mass (m1 + m2) / 2

---

### NDA & Diagram Enumeration Tools

#### EstimateDecayWidthNDATool

**Purpose**: Estimate decay widths using Naive Dimensional Analysis (arXiv:1402.1178)

**Input Parameters:**
- `diagram` (dict): Diagram specification with `initial`, `final`, `vertices`, and optionally `propagators`, `couplings`, `color_factor`
- `process_label` (str, optional): Display label (e.g., `"H → bb̄"`)
- `reference_width` (float, optional): Reference width in GeV for comparison
- `include_summary` (bool): Include formatted summary (default: True)

**Output:**
```json
{
  "status": "ok",
  "width_gev": 0.00437,
  "width_gev_nda_raw": 0.01749,
  "method": "improved (arXiv:1402.1178)",
  "formula": "\\Gamma \\sim \\frac{y^2 M}{16\\pi}"
}
```

**Example:**
```python
from tools.nda import EstimateDecayWidthNDATool

tool = EstimateDecayWidthNDATool(
    diagram={
        "initial": [{"label": "H", "spin": 0, "mass": 125.1}],
        "final": [
            {"label": "b", "spin": 0.5, "mass": 4.18},
            {"label": "bbar", "spin": 0.5, "mass": 4.18}
        ],
        "vertices": [{"type": "yukawa", "coupling": "yb"}],
        "couplings": {"yb": 0.0242},
        "color_factor": 3.0
    },
    base_directory="./workspace"
)
```

**Note — back-to-back kinematic correction**: For 2-body decays with
fermionic final states, the improved estimate applies a factor-of-2
correction to account for back-to-back kinematics. In 2-body decays
$\vec{p}_1 = -\vec{p}_2$, so $p_1 \cdot p_2 = 2 E_1 E_2$ (massless)
rather than the $\sim E_1 E_2$ assumed by the NDA polarization factors.
This correction is vertex-type-dependent: it applies to unprojected trace
structures (`yukawa`, `vector`, `va`, etc.) but not to chiral-projected
vertices (`left-handed`, `right-handed`, `chiral`), where the $P_L$
projector halves the trace and exactly cancels the kinematic enhancement.
No correction is applied for $n \geq 3$ body decays.

---

#### EstimateBranchingRatioNDATool

**Purpose**: Estimate branching ratios across multiple diagram classes

**Input Parameters:**
- `diagram_classes` (list): List of `{"diagram": {...}, "n_diagrams": N}` dicts (or `"diagram_path"` by reference)
- `reference_width` (float): Total width in GeV for BR denominator
- `process_label` (str, optional): Display label
- `reference_label` (str): Label for the reference width (default: `"Total width"`)

**Output:**
```json
{
  "status": "ok",
  "n_classes": 2,
  "n_diagrams_total": 126,
  "partial_width_gev": 1.23e-20,
  "branching_ratio": 4.56e-10,
  "class_results": [...]
}
```

---

#### EnumerateDiagramsTool

**Purpose**: Enumerate all Feynman diagrams for a process using FeynGraph

**Input Parameters:**
- `initial` (list): Initial-state particle labels, e.g., `["H"]`
- `final` (list): Final-state particle labels, e.g., `["b", "bbar"]`
- `max_loops` (int): Maximum loop order (default: 0 = tree level)
- `metadata_only` (bool): Return only class counts and representative diagrams (default: False)
- `save_diagrams` (bool): Save JSON + SVG files to disk (default: True)

**Output:**
```json
{
  "status": "ok",
  "n_diagrams": 3,
  "process": "H -> b bbar",
  "diagrams": [...],
  "br_classes": [...]
}
```

The `metadata_only` output includes a `br_classes` array that can be passed
directly to `EstimateBranchingRatioNDA` as `diagram_classes`.

**Example:**
```python
from tools.feyngraph import EnumerateDiagramsTool

tool = EnumerateDiagramsTool(
    initial=["mu-"],
    final=["e-", "nu_ebar", "nu_mu"],
    metadata_only=True,
    base_directory="./workspace"
)
```

---

### NanoAOD & Analysis Framework Tools

These tools work on NanoAOD-like ROOT files and on coffea analysis frameworks
rather than on `evtjsonl` events. `InspectFileTool` is in the `coffea` and
`framework` bundles; the other three are in `framework`. See
[tools/framework/README.md](framework/README.md) for the design and the
verification status, and the `framework` and `coffea` skills for how an agent is
expected to use them.

#### InspectFileTool

**Purpose**: Report what a NanoAOD-like ROOT file contains and what will need care when coffea reads it

**Input Parameters:**
- `root_path` (str): ROOT file, relative to `base_directory`
- `tree_name` (str): TTree to inspect (default: `"Events"`)
- `collection` (str, optional): Return every field of this collection
- `branch_pattern` (str, optional): Shell-style pattern of branch names to list, e.g. `"HLT_*Mu*"`
- `require` (list, optional): Branch or collection names that must exist
- `max_fields` (int): Fields listed per collection in the overview (default: 12; 0 for all)
- `list_all_branches` (bool): Also return every branch name (default: False)

**Output:**
```json
{
  "status": "ok",
  "trees": {"Events": 4733, "Runs": 1},
  "n_entries": 4733,
  "collections": {"Muon": {"kind": "jagged", "n_fields": 103, "counter": "nMuon", "fields": ["..."]}},
  "per_event_branches": ["run", "luminosityBlock", "event", "genWeight"],
  "schema_notes": [
    {"collection": "DSAMuon", "issue": "no_vector_behaviour", "detail": "..."},
    {"collection": "GenPart", "issue": "duplicate_momenta", "detail": "..."}
  ]
}
```

`schema_notes` lists what changes how the file must be read, by `issue`:
`no_vector_behaviour` (a collection with `pt`/`eta`/`phi` that coffea's
`NanoAODSchema` does not know, read without vector behaviour),
`duplicate_momenta` and `conflicting_coordinates` (a collection that stores its
momentum twice, or has other fields coffea reads as a second set of
coordinates), `missing_fields` (a collection coffea knows that lacks a field its
behaviour needs) and `shadowed_branches` (a branch named exactly like a
collection, which hides the collection). Each note says what to do about it.

The events must be in a TTree. An RNTuple is reported as such (`Not A TTree`)
and not described further.

---

#### ScaffoldAnalysisFrameworkTool

**Purpose**: Write a coffea analysis framework for a new analysis. It writes files and reads at most the branch names of `sample_file`; whether the result runs is what `CheckAnalysisFrameworkTool` reports.

**Input Parameters:**
- `project_dir` (str): Directory to create, relative to `base_directory`
- `sample_file` (str, optional): Representative ROOT file; objects, cuts and histograms are matched to it. It also becomes the first sample, unless `samples` is given
- `tree_name` (str): TTree holding the events (default: `"Events"`); recorded in the package
- `objects` (dict, optional): `{object name: collection}` to add to or override in the default objects, e.g. `{"dsaMuons": "DSAMuon"}`
- `include_default_objects` (bool): Start from the standard objects found in the file (default: True)
- `triggers` (list, optional): HLT paths ORed into a `"pass triggers"` event cut
- `year` (optional): Run period of the samples
- `lumi`, `golden_json` (optional): Luminosity of that period in /pb and its golden JSON; both need `year`
- `samples` (list, optional): `[{"name", "files", "is_data", "xsec", "year", "skim_factor"}]`; `files` are complete paths, `root://` urls, or relative to `base_directory`
- `components` (list, optional): Components to add right away (see `AddFrameworkComponentTool`)
- `package_name`, `project_title`, `project_description`, `author`, `experiment`
- `overwrite` (bool): Allow a directory that already has files in it; files the framework writes replace existing ones of the same name (default: False). A directory that already holds a framework is refused whatever this says

**Output:**
```json
{
  "status": "ok",
  "project": "my_analysis",
  "package": "my_analysis",
  "tree": "Events",
  "n_files_written": 34,
  "files_replaced": [],
  "objects": [{"name": "muons", "collection": "Muon", "kind": "jagged", "object_cuts": 8,
               "wrapped_as_lorentz": false, "optional": false}],
  "channels": ["all", "baseline", "baseline_2muons"],
  "hist_collections": ["muon_base", "base"],
  "components": [],
  "components_failed": [],
  "notes": ["..."],
  "next_steps": ["..."]
}
```

The engine written to `<package>/tools/` is the same for every analysis. What
is generated per analysis is `definitions/` (objects, cuts, histograms), `configs/`
(selections, histogram collections, samples) and a test notebook. The cuts,
thresholds and selections it writes are placeholders so that something runs,
not recommendations. A requested component that cannot be added is listed in
`components_failed`; the framework is written all the same.

The tool is called once per analysis. If it fails, whatever it had written is
removed again. `notes` says, among other things, which standard collections
were left out of the default objects because the file lacks a field coffea's
behaviour for them needs.

**Example:**
```python
from tools.framework.scaffold import ScaffoldAnalysisFrameworkTool

tool = ScaffoldAnalysisFrameworkTool(
    project_dir="my_analysis",
    sample_file="data/signal.root",
    objects={"dsaMuons": "DSAMuon"},
    triggers=["HLT_IsoMu24"],
    base_directory="./workspace"
)
```

---

#### AddFrameworkComponentTool

**Purpose**: Add an optional component to a scaffolded framework

**Input Parameters:**
- `project_dir` (str): The framework's directory
- `component` (str): `lepton_jets`, `scaleout`, `schema` or `chain_report`
- `options` (dict, optional): Component options
  - `lepton_jets`: `name`, `sources`, `radius`, `carry`, `isolation_jets`, `invmass_max`
  - `schema`: `mixins`, `cross_references`, `nested_items`, `hidden_branches`, `constant_fields`, `hide_duplicate_momenta`, `reset`
- `overwrite` (bool): Regenerate the component's files and blocks, discarding edits made inside them (default: False)

**Output:**
```json
{
  "status": "ok",
  "component": "lepton_jets",
  "files_written": ["my_analysis/tools/lepton_jets.py"],
  "files_kept": [],
  "blocks": {"my_analysis/definitions/objects.py": "added"},
  "options": {"name": "ljs", "radius": 0.4, "...": "..."},
  "notes": ["..."],
  "next_steps": ["..."]
}
```

A component copies its files and appends marked blocks
(`# >>> component: NAME >>>` ... `# <<< component: NAME <<<`) to the definitions
and configs. A call either completes or leaves the project as it was. Running
it again with the same options changes nothing. `lepton_jets` with different
options is refused unless `overwrite` is set, so that the tool does not leave
kept blocks next to a record of other options; `options` in the answer are the
ones the files and blocks were written from. Edits made by hand inside a block
are not tracked.

For `schema`, the tables at the top of the generated `tools/schema.py` are the
record: they can be edited by hand, later calls add to them, and `reset` starts
them afresh. If the file was edited elsewhere it is kept, and the answer says
`"applied": false`, unless `overwrite` is set.

---

#### CheckAnalysisFrameworkTool

**Purpose**: Run a scaffolded framework's self-check

**Input Parameters:**
- `project_dir` (str): The framework's directory
- `sample` (str, optional): Configured sample to run on; static checks only if omitted
- `tag`, `sample_config` (str, optional): Group of the sample, and name of the file under `configs/samples/` that defines it, when not the defaults
- `sample_file` (str, optional): Or a ROOT file to run on, with `is_data` and `year`
- `channels`, `hist_collections` (list, optional): What to run (default: all)
- `max_events` (int): Roughly how many events to process (default: 2000)
- `strict` (bool): Stop at the first failure (default: True)
- `venv` (str, optional): Virtual environment to run with
- `timeout_s` (int): Timeout in seconds (default: 50)

**Output:**
```json
{
  "status": "ok",
  "ok": true,
  "python": "/path/to/python",
  "versions": {"coffea": "...", "awkward": "..."},
  "static": {"errors": [], "warnings": [], "channels": ["all", "baseline"], "optional_objects": ["gens"]},
  "run": {"ok": true, "datasets": {"Signal": {
    "n_events": 2000,
    "is_data": [false],
    "year": ["2018"],
    "scaled_sum_weights": 2000.0,
    "lumixs_weight": null,
    "cutflow": {"baseline": [["None", 2000, 2000.0], ["pass triggers", 1500, 1500.0]]},
    "counters": {"baseline": {"Selected muons": 2710.0}},
    "empty_hists": [],
    "unavailable_objects": [],
    "warnings": []
  }}}
}
```

`status` says that the check ran; `ok` says whether it found errors (warnings do
not change it). When a run stops, `run.error` holds the chain of errors and
`run.traceback` the end of the traceback. Weighted yields are scaled to
lumi * cross section when `lumixs_weight` is a number, and are sums of
generator weights when it is null. The check runs
`python -m <package>.tools.check` in a subprocess, with the interpreter of
`venv`, else the `analysis_python` config key
(`tb config set --user heptapod analysis_python /path/to/python`), else the
toolkit's own.

The default timeout is below the 60 s toolbase gives a tool call by default.
Longer checks need both a larger `timeout_s` and a server started with
`tb serve --call-timeout SECONDS`; `examples/framework/launch.py` sets that up.
