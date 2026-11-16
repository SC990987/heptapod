"""
# _campaigns.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Run periods and the CMS campaigns that produce their simulation, step by step.

Each step carries the release, the SCRAM_ARCH and the cmsDriver options of a real McM
request of that campaign (named in "mcm"). The table was generated from those
requests' public McM setups on 2026-11-15; only options that belong to one job (file
names, numbers of events, threads, seeds) were left out. `build_steps` turns a run
period into the chain of one production: it chooses the first step (with or without
an LHE step), drops the premixed pileup when asked to, stops at the requested tier, and
makes the NanoAOD flat (NANOAODSIM) where McM's production writes EDM NanoAOD that is
merged later.

Every Run 3 campaign runs its steps in an el8 container in McM's own setups; so does
everything here.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

CONTAINER = "el8"                # the image every step runs in (cmssw-el8, or an EL8 batch slot)
DESIRED_OS = "EL8"               # the same, as the LPC batch system names it
COM_ENERGY_GEV = 13600.0         # every Run 3 period
TUNE_IMPORT = "Configuration.Generator.MCTunesRun3ECM13p6TeV.PythiaCP5Settings_cfi"

# What each run period is, in a line
DESCRIPTIONS = {
    "2022": "2022 before the ECAL endcap water leak (Run2022C-D)",
    "2022EE": "2022 after the ECAL endcap water leak (Run2022E-G)",
    "2023": "2023 before the BPix module failure (Run2023C)",
    "2023BPix": "2023 after the BPix module failure (Run2023D)",
    "2024": "2024 (Run2024)",
}

# The tiers a production can stop at, in the order the chain produces them
TIERS = ("GEN", "GEN-SIM", "AOD", "MINIAOD", "NANOAOD")
# The steps that produce each tier after the first one
TIER_STEPS = {"GEN-SIM": [], "AOD": ["DIGI-HLT", "RECO"], "MINIAOD": ["DIGI-HLT", "RECO", "MINIAOD"],
              "NANOAOD": ["DIGI-HLT", "RECO", "MINIAOD", "NANOAOD"]}
# The tier of each step's output
STEP_TIER = {"GEN": "GEN", "GEN-SIM": "GEN-SIM", "DIGI-HLT": "GEN-SIM-RAW", "RECO": "AOD",
             "MINIAOD": "MINIAOD", "NANOAOD": "NANOAOD"}

RUN_PERIODS = {'2022': {'campaign': 'Run3Summer22',
          'steps': {'GEN-SIM': {'release': 'CMSSW_12_4_11_patch3',
                                'scram_arch': 'el8_amd64_gcc10',
                                'mcm': 'BPH-Run3Summer22GS-00002',
                                'options': {'step': 'GEN,SIM',
                                            'era': 'Run3',
                                            'conditions': '124X_mcRun3_2022_realistic_v12',
                                            'beamspot': 'Realistic25ns13p6TeVEarly2022Collision',
                                            'geometry': 'DB:Extended',
                                            'eventcontent': 'RAWSIM',
                                            'datatier': 'GEN-SIM',
                                            'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'LHE-GEN-SIM': {'release': 'CMSSW_12_4_19',
                                    'scram_arch': 'el8_amd64_gcc10',
                                    'mcm': 'BPH-Run3Summer22wmLHEGS-00001',
                                    'options': {'step': 'LHE,GEN,SIM',
                                                'era': 'Run3',
                                                'conditions': '124X_mcRun3_2022_realistic_v12',
                                                'beamspot': 'Realistic25ns13p6TeVEarly2022Collision',
                                                'geometry': 'DB:Extended',
                                                'eventcontent': 'RAWSIM,LHE',
                                                'datatier': 'GEN-SIM,LHE',
                                                'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'DIGI-HLT': {'release': 'CMSSW_12_4_11_patch3',
                                 'scram_arch': 'el8_amd64_gcc10',
                                 'mcm': 'BPH-Run3Summer22DRPremix-00001',
                                 'options': {'step': 'DIGI,DATAMIX,L1,DIGI2RAW,HLT:2022v12',
                                             'era': 'Run3',
                                             'conditions': '124X_mcRun3_2022_realistic_v12',
                                             'geometry': 'DB:Extended',
                                             'procModifiers': 'premix_stage2,siPixelQualityRawToDigi',
                                             'datamix': 'PreMix',
                                             'pileup_input': 'dbs:/Neutrino_E-10_gun/Run3Summer21PrePremix-Summer22_124X_mcRun3_2022_realistic_v11-v2/PREMIX',
                                             'eventcontent': 'PREMIXRAW',
                                             'datatier': 'GEN-SIM-RAW',
                                             'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'RECO': {'release': 'CMSSW_12_4_11_patch3',
                             'scram_arch': 'el8_amd64_gcc10',
                             'mcm': 'BPH-Run3Summer22DRPremix-00001',
                             'options': {'step': 'RAW2DIGI,L1Reco,RECO,RECOSIM',
                                         'era': 'Run3',
                                         'conditions': '124X_mcRun3_2022_realistic_v12',
                                         'geometry': 'DB:Extended',
                                         'procModifiers': 'siPixelQualityRawToDigi',
                                         'eventcontent': 'AODSIM',
                                         'datatier': 'AODSIM',
                                         'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'MINIAOD': {'release': 'CMSSW_13_0_13',
                                'scram_arch': 'el8_amd64_gcc11',
                                'mcm': 'BPH-Run3Summer22MiniAODv4-00001',
                                'options': {'step': 'PAT',
                                            'era': 'Run3,run3_miniAOD_12X',
                                            'conditions': '130X_mcRun3_2022_realistic_v5',
                                            'geometry': 'DB:Extended',
                                            'eventcontent': 'MINIAODSIM',
                                            'datatier': 'MINIAODSIM',
                                            'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'NANOAOD': {'release': 'CMSSW_13_0_13',
                                'scram_arch': 'el8_amd64_gcc11',
                                'mcm': 'BPH-Run3Summer22NanoAODv12-00001',
                                'options': {'step': 'NANO',
                                            'era': 'Run3',
                                            'conditions': '130X_mcRun3_2022_realistic_v5',
                                            'eventcontent': 'NANOEDMAODSIM',
                                            'datatier': 'NANOAODSIM',
                                            'scenario': 'pp',
                                            'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}}}},
 '2022EE': {'campaign': 'Run3Summer22EE',
            'steps': {'GEN-SIM': {'release': 'CMSSW_12_4_11_patch3',
                                  'scram_arch': 'el8_amd64_gcc10',
                                  'mcm': 'BPH-Run3Summer22EEGS-00001',
                                  'options': {'step': 'GEN,SIM',
                                              'era': 'Run3',
                                              'conditions': '124X_mcRun3_2022_realistic_postEE_v1',
                                              'beamspot': 'Realistic25ns13p6TeVEarly2022Collision',
                                              'geometry': 'DB:Extended',
                                              'eventcontent': 'RAWSIM',
                                              'datatier': 'GEN-SIM',
                                              'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                      'LHE-GEN-SIM': {'release': 'CMSSW_12_4_19',
                                      'scram_arch': 'el8_amd64_gcc10',
                                      'mcm': 'BPH-Run3Summer22EEwmLHEGS-00001',
                                      'options': {'step': 'LHE,GEN,SIM',
                                                  'era': 'Run3',
                                                  'conditions': '124X_mcRun3_2022_realistic_postEE_v1',
                                                  'beamspot': 'Realistic25ns13p6TeVEarly2022Collision',
                                                  'geometry': 'DB:Extended',
                                                  'eventcontent': 'RAWSIM,LHE',
                                                  'datatier': 'GEN-SIM,LHE',
                                                  'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                      'DIGI-HLT': {'release': 'CMSSW_12_4_11_patch3',
                                   'scram_arch': 'el8_amd64_gcc10',
                                   'mcm': 'BPH-Run3Summer22EEDRPremix-00001',
                                   'options': {'step': 'DIGI,DATAMIX,L1,DIGI2RAW,HLT:2022v14',
                                               'era': 'Run3',
                                               'conditions': '124X_mcRun3_2022_realistic_postEE_v1',
                                               'geometry': 'DB:Extended',
                                               'procModifiers': 'premix_stage2,siPixelQualityRawToDigi',
                                               'datamix': 'PreMix',
                                               'pileup_input': 'dbs:/Neutrino_E-10_gun/Run3Summer21PrePremix-Summer22_124X_mcRun3_2022_realistic_v11-v2/PREMIX',
                                               'eventcontent': 'PREMIXRAW',
                                               'datatier': 'GEN-SIM-RAW',
                                               'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                      'RECO': {'release': 'CMSSW_12_4_11_patch3',
                               'scram_arch': 'el8_amd64_gcc10',
                               'mcm': 'BPH-Run3Summer22EEDRPremix-00001',
                               'options': {'step': 'RAW2DIGI,L1Reco,RECO,RECOSIM',
                                           'era': 'Run3',
                                           'conditions': '124X_mcRun3_2022_realistic_postEE_v1',
                                           'geometry': 'DB:Extended',
                                           'procModifiers': 'siPixelQualityRawToDigi',
                                           'eventcontent': 'AODSIM',
                                           'datatier': 'AODSIM',
                                           'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                      'MINIAOD': {'release': 'CMSSW_13_0_13',
                                  'scram_arch': 'el8_amd64_gcc11',
                                  'mcm': 'BPH-Run3Summer22EEMiniAODv4-00001',
                                  'options': {'step': 'PAT',
                                              'era': 'Run3,run3_miniAOD_12X',
                                              'conditions': '130X_mcRun3_2022_realistic_postEE_v6',
                                              'geometry': 'DB:Extended',
                                              'eventcontent': 'MINIAODSIM',
                                              'datatier': 'MINIAODSIM',
                                              'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                      'NANOAOD': {'release': 'CMSSW_13_0_13',
                                  'scram_arch': 'el8_amd64_gcc11',
                                  'mcm': 'BPH-Run3Summer22EENanoAODv12-00001',
                                  'options': {'step': 'NANO',
                                              'era': 'Run3',
                                              'conditions': '130X_mcRun3_2022_realistic_postEE_v6',
                                              'eventcontent': 'NANOEDMAODSIM',
                                              'datatier': 'NANOAODSIM',
                                              'scenario': 'pp',
                                              'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}}}},
 '2023': {'campaign': 'Run3Summer23',
          'steps': {'GEN-SIM': {'release': 'CMSSW_13_0_13',
                                'scram_arch': 'el8_amd64_gcc11',
                                'mcm': 'BPH-Run3Summer23GS-00002',
                                'options': {'step': 'GEN,SIM',
                                            'era': 'Run3_2023',
                                            'conditions': '130X_mcRun3_2023_realistic_v14',
                                            'beamspot': 'Realistic25ns13p6TeVEarly2023Collision',
                                            'geometry': 'DB:Extended',
                                            'eventcontent': 'RAWSIM',
                                            'datatier': 'GEN-SIM',
                                            'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'LHE-GEN-SIM': {'release': 'CMSSW_13_0_17',
                                    'scram_arch': 'el8_amd64_gcc11',
                                    'mcm': 'BPH-Run3Summer23wmLHEGS-00001',
                                    'options': {'step': 'LHE,GEN,SIM',
                                                'era': 'Run3_2023',
                                                'conditions': '130X_mcRun3_2023_realistic_v15',
                                                'beamspot': 'Realistic25ns13p6TeVEarly2023Collision',
                                                'geometry': 'DB:Extended',
                                                'eventcontent': 'RAWSIM,LHE',
                                                'datatier': 'GEN-SIM,LHE',
                                                'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'DIGI-HLT': {'release': 'CMSSW_13_0_14',
                                 'scram_arch': 'el8_amd64_gcc11',
                                 'mcm': 'BPH-Run3Summer23DRPremix-00001',
                                 'options': {'step': 'DIGI,DATAMIX,L1,DIGI2RAW,HLT:2023v12',
                                             'era': 'Run3_2023',
                                             'conditions': '130X_mcRun3_2023_realistic_v14',
                                             'geometry': 'DB:Extended',
                                             'procModifiers': 'premix_stage2',
                                             'datamix': 'PreMix',
                                             'pileup_input': 'dbs:/Neutrino_E-10_gun/Run3Summer21PrePremix-Summer23_130X_mcRun3_2023_realistic_v13-v1/PREMIX',
                                             'eventcontent': 'PREMIXRAW',
                                             'datatier': 'GEN-SIM-RAW',
                                             'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'RECO': {'release': 'CMSSW_13_0_14',
                             'scram_arch': 'el8_amd64_gcc11',
                             'mcm': 'BPH-Run3Summer23DRPremix-00001',
                             'options': {'step': 'RAW2DIGI,L1Reco,RECO,RECOSIM',
                                         'era': 'Run3_2023',
                                         'conditions': '130X_mcRun3_2023_realistic_v14',
                                         'geometry': 'DB:Extended',
                                         'eventcontent': 'AODSIM',
                                         'datatier': 'AODSIM',
                                         'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'MINIAOD': {'release': 'CMSSW_13_0_14',
                                'scram_arch': 'el8_amd64_gcc11',
                                'mcm': 'BPH-Run3Summer23MiniAODv4-00001',
                                'options': {'step': 'PAT',
                                            'era': 'Run3_2023',
                                            'conditions': '130X_mcRun3_2023_realistic_v14',
                                            'geometry': 'DB:Extended',
                                            'eventcontent': 'MINIAODSIM',
                                            'datatier': 'MINIAODSIM',
                                            'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'NANOAOD': {'release': 'CMSSW_13_0_14',
                                'scram_arch': 'el8_amd64_gcc11',
                                'mcm': 'BPH-Run3Summer23NanoAODv12-00001',
                                'options': {'step': 'NANO',
                                            'era': 'Run3_2023',
                                            'conditions': '130X_mcRun3_2023_realistic_v14',
                                            'eventcontent': 'NANOEDMAODSIM',
                                            'datatier': 'NANOAODSIM',
                                            'scenario': 'pp',
                                            'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}}}},
 '2023BPix': {'campaign': 'Run3Summer23BPix',
              'steps': {'GEN-SIM': {'release': 'CMSSW_13_0_13',
                                    'scram_arch': 'el8_amd64_gcc11',
                                    'mcm': 'BPH-Run3Summer23BPixGS-00001',
                                    'options': {'step': 'GEN,SIM',
                                                'era': 'Run3_2023',
                                                'conditions': '130X_mcRun3_2023_realistic_postBPix_v2',
                                                'beamspot': 'Realistic25ns13p6TeVEarly2023Collision',
                                                'geometry': 'DB:Extended',
                                                'eventcontent': 'RAWSIM',
                                                'datatier': 'GEN-SIM',
                                                'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                        'LHE-GEN-SIM': {'release': 'CMSSW_13_0_17',
                                        'scram_arch': 'el8_amd64_gcc11',
                                        'mcm': 'BPH-Run3Summer23BPixwmLHEGS-00001',
                                        'options': {'step': 'LHE,GEN,SIM',
                                                    'era': 'Run3_2023',
                                                    'conditions': '130X_mcRun3_2023_realistic_postBPix_v6',
                                                    'beamspot': 'Realistic25ns13p6TeVEarly2023Collision',
                                                    'geometry': 'DB:Extended',
                                                    'eventcontent': 'RAWSIM,LHE',
                                                    'datatier': 'GEN-SIM,LHE',
                                                    'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                        'DIGI-HLT': {'release': 'CMSSW_13_0_14',
                                     'scram_arch': 'el8_amd64_gcc11',
                                     'mcm': 'BPH-Run3Summer23BPixDRPremix-00001',
                                     'options': {'step': 'DIGI,DATAMIX,L1,DIGI2RAW,HLT:2023v12',
                                                 'era': 'Run3_2023',
                                                 'conditions': '130X_mcRun3_2023_realistic_postBPix_v2',
                                                 'geometry': 'DB:Extended',
                                                 'procModifiers': 'premix_stage2',
                                                 'datamix': 'PreMix',
                                                 'pileup_input': 'dbs:/Neutrino_E-10_gun/Run3Summer21PrePremix-Summer23BPix_130X_mcRun3_2023_realistic_postBPix_v1-v1/PREMIX',
                                                 'eventcontent': 'PREMIXRAW',
                                                 'datatier': 'GEN-SIM-RAW',
                                                 'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                        'RECO': {'release': 'CMSSW_13_0_14',
                                 'scram_arch': 'el8_amd64_gcc11',
                                 'mcm': 'BPH-Run3Summer23BPixDRPremix-00001',
                                 'options': {'step': 'RAW2DIGI,L1Reco,RECO,RECOSIM',
                                             'era': 'Run3_2023',
                                             'conditions': '130X_mcRun3_2023_realistic_postBPix_v2',
                                             'geometry': 'DB:Extended',
                                             'eventcontent': 'AODSIM',
                                             'datatier': 'AODSIM',
                                             'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                        'MINIAOD': {'release': 'CMSSW_13_0_14',
                                    'scram_arch': 'el8_amd64_gcc11',
                                    'mcm': 'BPH-Run3Summer23BPixMiniAODv4-00001',
                                    'options': {'step': 'PAT',
                                                'era': 'Run3_2023',
                                                'conditions': '130X_mcRun3_2023_realistic_postBPix_v2',
                                                'geometry': 'DB:Extended',
                                                'eventcontent': 'MINIAODSIM',
                                                'datatier': 'MINIAODSIM',
                                                'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                        'NANOAOD': {'release': 'CMSSW_13_0_14',
                                    'scram_arch': 'el8_amd64_gcc11',
                                    'mcm': 'BPH-Run3Summer23BPixNanoAODv12-00001',
                                    'options': {'step': 'NANO',
                                                'era': 'Run3_2023',
                                                'conditions': '130X_mcRun3_2023_realistic_postBPix_v2',
                                                'eventcontent': 'NANOEDMAODSIM',
                                                'datatier': 'NANOAODSIM',
                                                'scenario': 'pp',
                                                'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}}}},
 '2024': {'campaign': 'RunIII2024Summer24',
          'steps': {'GEN-SIM': {'release': 'CMSSW_14_0_19',
                                'scram_arch': 'el8_amd64_gcc12',
                                'mcm': 'BPH-RunIII2024Summer24GS-00002',
                                'options': {'step': 'GEN,SIM',
                                            'era': 'Run3_2024',
                                            'conditions': '140X_mcRun3_2024_realistic_v26',
                                            'beamspot': 'DBrealistic',
                                            'geometry': 'DB:Extended',
                                            'eventcontent': 'RAWSIM',
                                            'datatier': 'GEN-SIM',
                                            'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'LHE-GEN-SIM': {'release': 'CMSSW_14_0_19',
                                    'scram_arch': 'el8_amd64_gcc12',
                                    'mcm': 'BPH-RunIII2024Summer24wmLHEGS-00001',
                                    'options': {'step': 'LHE,GEN,SIM',
                                                'era': 'Run3_2024',
                                                'conditions': '140X_mcRun3_2024_realistic_v26',
                                                'beamspot': 'DBrealistic',
                                                'geometry': 'DB:Extended',
                                                'eventcontent': 'RAWSIM,LHE',
                                                'datatier': 'GEN-SIM,LHE',
                                                'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'DIGI-HLT': {'release': 'CMSSW_14_0_19',
                                 'scram_arch': 'el8_amd64_gcc12',
                                 'mcm': 'BPH-RunIII2024Summer24DRPremix-00001',
                                 'options': {'step': 'DIGI,DATAMIX,L1,DIGI2RAW,HLT:2024v14',
                                             'era': 'Run3_2024',
                                             'conditions': '140X_mcRun3_2024_realistic_v26',
                                             'geometry': 'DB:Extended',
                                             'procModifiers': 'premix_stage2',
                                             'datamix': 'PreMix',
                                             'pileup_input': 'dbs:/Neutrino_E-10_gun/RunIIISummer24PrePremix-Premixlib2024_140X_mcRun3_2024_realistic_v26-v1/PREMIX',
                                             'eventcontent': 'PREMIXRAW',
                                             'datatier': 'GEN-SIM-RAW',
                                             'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'RECO': {'release': 'CMSSW_14_0_19',
                             'scram_arch': 'el8_amd64_gcc12',
                             'mcm': 'BPH-RunIII2024Summer24DRPremix-00001',
                             'options': {'step': 'RAW2DIGI,L1Reco,RECO,RECOSIM',
                                         'era': 'Run3_2024',
                                         'conditions': '140X_mcRun3_2024_realistic_v26',
                                         'geometry': 'DB:Extended',
                                         'eventcontent': 'AODSIM',
                                         'datatier': 'AODSIM',
                                         'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'MINIAOD': {'release': 'CMSSW_15_0_2',
                                'scram_arch': 'el8_amd64_gcc12',
                                'mcm': 'BPH-RunIII2024Summer24MiniAODv6-00001',
                                'options': {'step': 'PAT',
                                            'era': 'Run3_2024',
                                            'conditions': '150X_mcRun3_2024_realistic_v2',
                                            'geometry': 'DB:Extended',
                                            'eventcontent': 'MINIAODSIM1',
                                            'datatier': 'MINIAODSIM',
                                            'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}},
                    'NANOAOD': {'release': 'CMSSW_15_0_2',
                                'scram_arch': 'el8_amd64_gcc12',
                                'mcm': 'BPH-RunIII2024Summer24NanoAODv15-00001',
                                'options': {'step': 'NANO',
                                            'era': 'Run3_2024',
                                            'conditions': '150X_mcRun3_2024_realistic_v2',
                                            'eventcontent': 'NANOEDMAODSIM1',
                                            'datatier': 'NANOAODSIM',
                                            'scenario': 'pp',
                                            'customise': 'Configuration/DataProcessing/Utils.addMonitoring'}}}}}


class CampaignError(ValueError):
    """A run period, tier or pileup choice that does not exist; the message says why."""

    def __init__(self, message: str, suggestion: str = ""):
        super().__init__(message)
        self.suggestion = suggestion


def premix_dataset(run_period: str) -> str:
    """The premixed-pileup library of a run period, as DAS names it."""
    return RUN_PERIODS[run_period]["steps"]["DIGI-HLT"]["options"]["pileup_input"].split(":", 1)[1]


def hlt_menu(run_period: str) -> str:
    step = RUN_PERIODS[run_period]["steps"]["DIGI-HLT"]["options"]["step"]
    return next(part.split(":", 1)[1] for part in step.split(",") if part.startswith("HLT:"))


def describe(run_period: str) -> Dict[str, Any]:
    """What a run period's simulation is made with, step by step."""
    period = RUN_PERIODS[run_period]
    steps = {}
    for name, step in period["steps"].items():
        options = step["options"]
        steps[name] = {"release": step["release"], "scram_arch": step["scram_arch"],
                       "step": options["step"], "conditions": options["conditions"],
                       "era": options["era"], "mcm": step["mcm"]}
        if "beamspot" in options:
            steps[name]["beamspot"] = options["beamspot"]
    return {"run_period": run_period, "description": DESCRIPTIONS[run_period],
            "campaign": period["campaign"], "com_energy_gev": COM_ENERGY_GEV,
            "hlt_menu": hlt_menu(run_period), "premix_dataset": premix_dataset(run_period),
            "steps": steps}


def _without_pileup(options: Dict[str, Any]) -> Dict[str, Any]:
    """The DIGI-to-HLT step without premixed pileup: no DATAMIX, no premix library."""
    out = dict(options)
    out["step"] = ",".join(part for part in out["step"].split(",") if part != "DATAMIX")
    out.pop("datamix", None)
    out.pop("pileup_input", None)
    modifiers = [m for m in str(out.get("procModifiers", "")).split(",") if m and m != "premix_stage2"]
    if modifiers:
        out["procModifiers"] = ",".join(modifiers)
    else:
        out.pop("procModifiers", None)
    out["eventcontent"] = "RAWSIM"
    return out


def _generator_only(options: Dict[str, Any], lhe: bool) -> Dict[str, Any]:
    """The first step without the detector simulation: GEN (or LHE,GEN) only."""
    out = dict(options)
    out["step"] = "LHE,GEN" if lhe else "GEN"
    out["eventcontent"] = "RAWSIM,LHE" if lhe else "RAWSIM"
    out["datatier"] = "GEN,LHE" if lhe else "GEN"
    return out


def check_choice(run_period: str, final_tier: str, pileup: str) -> None:
    if run_period not in RUN_PERIODS:
        raise CampaignError(f"unknown run period {run_period!r}",
                            f"Run periods: {', '.join(RUN_PERIODS)}")
    if final_tier not in TIERS:
        raise CampaignError(f"unknown final tier {final_tier!r}", f"Tiers: {', '.join(TIERS)}")
    if pileup not in ("premix", "none"):
        raise CampaignError(f"pileup must be 'premix' or 'none', not {pileup!r}",
                            "'premix' mixes in the run period's premixed pileup library as the "
                            "official samples do; 'none' simulates the hard process alone")


def build_steps(run_period: str, final_tier: str = "NANOAOD", pileup: str = "premix",
                lhe: bool = False) -> List[Dict[str, Any]]:
    """The steps of one production, in order, each with its release, options and files.

    ``lhe`` makes the first step LHE,GEN,SIM (a fragment with an ExternalLHEProducer,
    i.e. a gridpack). Each step reads the previous step's output in the job's working
    directory and writes ``step<N>_<name>.root`` there.
    """
    check_choice(run_period, final_tier, pileup)
    period = RUN_PERIODS[run_period]["steps"]
    first = copy.deepcopy(period["LHE-GEN-SIM" if lhe else "GEN-SIM"])
    if final_tier == "GEN":
        first["options"] = _generator_only(first["options"], lhe)
        chain = [("GEN", first)]
    else:
        chain = [("GEN-SIM", first)]
        for name in TIER_STEPS[final_tier]:
            step = copy.deepcopy(period[name])
            if name == "DIGI-HLT" and pileup == "none":
                step["options"] = _without_pileup(step["options"])
            if name == "NANOAOD":
                step["options"]["eventcontent"] = "NANOAODSIM"     # a flat TTree, as coffea reads it
            chain.append((name, step))
    steps, previous = [], None
    for index, (name, step) in enumerate(chain, start=1):
        output = f"step{index}_{name}.root"
        steps.append({"index": index, "name": name, "tier": STEP_TIER[name],
                      "release": step["release"], "scram_arch": step["scram_arch"],
                      "mcm": step["mcm"], "options": step["options"], "input": previous,
                      "output": output, "cfg": f"step{index}_{name}_cfg.py"})
        previous = output
    return steps


def releases(steps: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """The distinct releases a chain needs, in the order it first needs them."""
    seen: Dict[str, str] = {}
    for step in steps:
        seen.setdefault(step["release"], step["scram_arch"])
    return [{"release": release, "scram_arch": arch} for release, arch in seen.items()]


def cmsdriver_args(step: Dict[str, Any], fragment_module: Optional[str], events: int,
                   cfg_path: str) -> List[str]:
    """The cmsDriver.py command that writes a step's configuration (``--no_exec``)."""
    args = ["cmsDriver.py"]
    if step["index"] == 1:
        args.append(fragment_module)
    args += ["--python_filename", cfg_path, "--fileout", "file:" + step["output"]]
    if step["input"]:
        args += ["--filein", "file:" + step["input"]]
    for key, value in step["options"].items():
        args += ["--" + key, str(value)]
    args += ["--mc", "-n", str(events if step["index"] == 1 else -1), "--no_exec"]
    return args
