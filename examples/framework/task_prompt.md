# Analysis framework task prompts

Prompts to start a session from. Replace the physics with your own: the numbers
below are illustrations, not recommendations. The agent is told not to invent cut
values, cross sections or trigger paths, so anything you leave out it will ask for
or leave as a marked placeholder.

---

## Start a framework

Set up an analysis framework called `zmumu` for the NanoAOD file in `data/`.
The analysis selects Z → μμ events: two opposite-charge muons passing the tight
ID with pT > 25 GeV and |η| < 2.4, relative isolation below 0.15, triggered by
`HLT_IsoMu24`, with the dimuon mass between 76 and 106 GeV. Add a control region
with the isolation requirement inverted. I want histograms of the muon
kinematics and of the dimuon mass and pT. Run the check on the file and show me
the cutflow of each channel.

## Start from files with custom collections

Set up an analysis framework called `displaced_dimuon` for the file in `data/`.
It is a private NanoAOD production with extra collections: I need the displaced
standalone muons (`DSAMuon`) as an object next to the standard muons. Tell me
which other collections in the file have no vector behaviour, and what you
assumed for them. Use the trigger paths `HLT_DoubleL2Mu23NoVtx_2Cha` and
`HLT_DoubleL2Mu23NoVtx_2Cha_CosmicSeed`.

## Extend an existing framework

- Add an event cut that vetoes events with a b-tagged jet. The working point is
  `btagDeepFlavB > 0.2783`. Use it in a new channel built on the signal region.
- Add the 2018 data sample in `data/SingleMuon/` with the golden JSON
  `data/Cert_2018.json` and a luminosity of 59830 /pb.
- Add lepton jets built from muons, displaced muons, electrons and photons with
  a radius of 0.4, and a channel requiring two of them.
- Set the framework up to run on HTCondor at the LPC.
- Add a regression report that runs on every pull request, using 200 events of
  the signal file as the fixture.
