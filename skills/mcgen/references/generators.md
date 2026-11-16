# Writing the generator

`PrepareMCProduction` takes the process in one of four ways. The CMS common settings,
the CP5 tune and the 13.6 TeV beam energy are added to `pythia8_settings` for you;
a complete `fragment` brings its own.

## Pythia 8 settings

One setting per string, as in a Pythia 8 command file. A process is switched on,
its phase space cut, and the decays of the particles of interest restricted. The
numbers below show the syntax; the values are the analysis's choice.

| Process | Settings |
|---------|----------|
| QCD dijets | `HardQCD:all = on`, `PhaseSpace:pTHatMin = 50.` |
| b and c pairs | `HardQCD:hardbbbar = on` (or `hardccbar`), `PhaseSpace:pTHatMin = 10.` |
| Minimum bias | `SoftQCD:inelastic = on` (or `SoftQCD:nonDiffractive = on`) |
| Prompt charmonium | `Charmonium:all = on`, then decays of 443 (J/psi) or 100443 (psi(2S)) |
| Prompt bottomonium | `Bottomonium:all = on`, then decays of 553, 100553, 200553 (Upsilon 1S-3S) |
| Drell-Yan Z/gamma* | `WeakSingleBoson:ffbar2gmZ = on`, `PhaseSpace:mHatMin = 50.`, decays of 23 |
| W | `WeakSingleBoson:ffbar2W = on`, decays of 24 |
| ttbar | `Top:gg2ttbar = on`, `Top:qqbar2ttbar = on` |
| SM Higgs | `HiggsSM:all = on` (or one mode, `HiggsSM:gg2H = on`), decays of 25 |
| Sequential Z' | `NewGaugeBoson:ffbar2gmZZprime = on`, `Zprime:gmZmode = 3`, `32:m0 = <mass>`, decays of 32 |

Decays: `<id>:onMode = off` switches every channel off; then `<id>:onIfMatch = 13 -13`
switches on the channels whose daughters are exactly those (here mu+ mu-), and
`<id>:onIfAny = 11 13` those with any of the listed particles among the daughters.
Masses and widths: `<id>:m0 = 125.`, `<id>:mWidth = 0.004`. A pTHat or mHat cut
makes generation efficient but shapes the spectrum below it: put it well under the
analysis's own threshold, and say that it is there.

## Generator filters

A filter keeps only events with what the analysis needs, so that most generated
events are not thrown away later. Define the filter after `generator` in `filters`,
and end with the sequence that runs them:

```python
mumufilter = cms.EDFilter("MCParticlePairFilter",
    Status = cms.untracked.vint32(1, 1),
    MinPt = cms.untracked.vdouble(2.0, 2.0),
    MinP = cms.untracked.vdouble(0.0, 0.0),
    MinEta = cms.untracked.vdouble(-2.5, -2.5),
    MaxEta = cms.untracked.vdouble(2.5, 2.5),
    ParticleID1 = cms.untracked.vint32(13),
    ParticleID2 = cms.untracked.vint32(13),
    ParticleCharge = cms.untracked.int32(-1),
)
ProductionFilterSequence = cms.Sequence(generator * mumufilter)
```

| Filter | Keeps events with | Parameters (all untracked) |
|--------|-------------------|----------------------------|
| `MCParticlePairFilter` | a pair of particles | `ParticleID1`, `ParticleID2` (lists), `Status`, `MinPt`, `MinP`, `MinEta`, `MaxEta` (exactly two values each, one per particle, or it warns and fills in defaults), `ParticleCharge` (-1 opposite, +1 same, 0 any), `MinInvMass`, `MaxInvMass`, `MinDeltaR`, `MaxDeltaR`, `MinDeltaPhi`, `MaxDeltaPhi` |
| `MCSingleParticleFilter` | one particle of a kind | `ParticleID`, `Status`, `MinPt`, `MinEta`, `MaxEta` (one value per listed particle) |
| `MCMultiParticleFilter` | at least `NumRequired` particles | `ParticleID`, `PtMin`, `EtaMax`, `Status` (lists), `NumRequired`, `AcceptMore`, `MotherID` |
| `PythiaDauVFilter` | a particle decaying to given daughters | `ParticleID`, `DaughterIDs`, `NumberDaughters`, `MinPt`, `MinEta`, `MaxEta` (per daughter), `MotherID`, `ChargeConjugation` |

`Status` 1 is a final-state particle; a list has one entry per particle the filter
looks at. Several filters are combined in the sequence:
`cms.Sequence(generator * filter1 * filter2)`.

## A complete fragment

Anything a CMS fragment can configure: give it as `fragment` (text) or
`fragment_file` (a file in the working directory, or one of CMSSW's own under
`/cvmfs/cms.cern.ch/<arch>/cms/cmssw/<release>/src/Configuration/Generator/python/`).
It must define `generator`, and `ProductionFilterSequence` if it filters.

- **EvtGen decays** (B physics): a `Pythia8ConcurrentGeneratorFilter` with an
  `ExternalDecays` block (`EvtGen130`); the official B-physics fragments are the
  model, and `mcm_prepid` takes one as it is.
- **Particle guns**: `cms.EDFilter("Pythia8PtGun", PGunParameters = cms.PSet(...))`.
- **MadGraph or Powheg gridpacks**: an `ExternalLHEProducer` (the gridpack's path,
  `nEvents`, `numberOfParameters`, `outputFile`, and the `scriptName`
  `GeneratorInterface/LHEInterface/data/run_generic_tarball_cvmfs.sh`), followed by a
  `Pythia8ConcurrentHadronizerFilter` with the matching or merging settings the
  gridpack was made for. Those settings must agree with the gridpack: start from the
  fragment of an official sample made with the same gridpack (`mcm_prepid`), and change
  only what has to change. The chain then begins with LHE,GEN,SIM in the campaign's LHE
  release, and every job gets its own seed for the gridpack.

## An official sample

`mcm_prepid` takes the fragment of an official McM request as it is, to make more of
that sample (more events, another run period, no pileup, another tier). Request ids
look like `BPH-Run3Summer23GS-00002`; the GEN-SIM (or wmLHEGS) request of a sample's
chain is the one with the fragment.
